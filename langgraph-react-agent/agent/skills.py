"""Skills: drop-in workflow modules with progressive disclosure.

A *skill* is a folder under ``skills/`` containing a ``SKILL.md`` with YAML
frontmatter (``name``, ``description``, ``when_to_use``) followed by the detailed
instruction body, plus an optional ``tools.py`` exposing a ``TOOLS`` list of
LangChain tools.

Progressive disclosure keeps the base prompt cheap:

- At startup we render a **catalog** (name + description only) into the system
  prompt so the model knows what workflows exist.
- The full instruction body is only pulled into context when the model calls the
  ``load_skill(name)`` tool — i.e. when it has decided a skill is relevant.

Adding a skill requires **no code change** — drop a folder in ``skills/``.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml
from langchain_core.tools import BaseTool, tool

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    name: str
    description: str
    when_to_use: str
    instructions: str
    tools: List[BaseTool] = field(default_factory=list)
    path: Optional[Path] = None


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``---`` YAML frontmatter from the markdown body."""
    if text.lstrip().startswith("---"):
        # maxsplit=2 tolerates ``---`` horizontal rules inside the body.
        parts = text.split("---", 2)
        if len(parts) == 3:
            meta = yaml.safe_load(parts[1]) or {}
            return meta, parts[2].strip()
    return {}, text.strip()


def _load_skill_tools(skill_dir: Path) -> List[BaseTool]:
    tools_file = skill_dir / "tools.py"
    if not tools_file.exists():
        return []
    spec = importlib.util.spec_from_file_location(f"skill_{skill_dir.name}_tools", tools_file)
    if spec is None or spec.loader is None:
        return []
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(getattr(module, "TOOLS", []))


class SkillRegistry:
    def __init__(self, skills: List[Skill]):
        self._skills = {s.name: s for s in skills}

    @classmethod
    def load(cls, skills_dir: str | Path) -> "SkillRegistry":
        skills_dir = Path(skills_dir)
        skills: List[Skill] = []
        if not skills_dir.exists():
            logger.warning("skills directory %s not found; no skills loaded", skills_dir)
            return cls([])
        for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
            try:
                meta, body = _parse_frontmatter(skill_md.read_text())
                name = meta.get("name") or skill_md.parent.name
                skills.append(
                    Skill(
                        name=name,
                        description=meta.get("description", ""),
                        when_to_use=meta.get("when_to_use", ""),
                        instructions=body,
                        tools=_load_skill_tools(skill_md.parent),
                        path=skill_md.parent,
                    )
                )
            except Exception:
                logger.exception("failed to load skill at %s; skipping", skill_md.parent)
        return cls(skills)

    @property
    def skills(self) -> List[Skill]:
        return list(self._skills.values())

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def all_tools(self) -> List[BaseTool]:
        out: List[BaseTool] = []
        for skill in self._skills.values():
            out.extend(skill.tools)
        return out

    def render_catalog(self) -> str:
        if not self._skills:
            return "(no skills available)"
        lines = []
        for s in self._skills.values():
            line = f"- **{s.name}**: {s.description}"
            if s.when_to_use:
                line += f" — use when: {s.when_to_use}"
            lines.append(line)
        return "\n".join(lines)

    def make_load_skill_tool(self) -> BaseTool:
        registry = self

        @tool
        def load_skill(skill_name: str) -> str:
            """Load the full step-by-step instructions for a named skill.

            Call this BEFORE performing a workflow when the user's request matches
            one of the skills listed in the system prompt. Returns the skill's
            detailed instructions to follow.
            """
            skill = registry.get(skill_name)
            if skill is None:
                available = ", ".join(registry._skills) or "(none)"
                return f"No skill named '{skill_name}'. Available skills: {available}."
            return skill.instructions

        return load_skill
