"""Agent construction.

Builds a LangGraph ReAct agent via ``langchain.agents.create_agent`` (the v1
successor to the deprecated ``langgraph.prebuilt.create_react_agent``). Tools are
the union of built-ins, skill-contributed tools, MCP tools, and the ``load_skill``
tool. The system prompt embeds the skill catalog and instructs the model to load a
skill before running its workflow.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from langchain.agents import create_agent
from langchain_core.tools import BaseTool

from .config import Settings
from .llm import build_llm
from .skills import SkillRegistry
from .tools import BUILTIN_TOOLS

logger = logging.getLogger(__name__)

# Generic base persona. A deployment can replace this with its own domain persona
# via a local `system_prompt.local.md` (see Settings.system_prompt_file); the skills
# section below is always appended regardless.
BASE_INSTRUCTIONS = "You are a helpful, capable general-purpose assistant that uses tools to accomplish tasks."

SKILLS_SECTION = """

You have access to a set of SKILLS — specialized workflows, each with detailed instructions:

{skill_catalog}

When a user's request matches one of these skills, FIRST call `load_skill(skill_name)` to read its full
instructions, then follow them step by step using the available tools. If no skill applies, respond
directly or use your other tools. Think step by step and be concise."""


def _base_instructions(settings: Settings) -> str:
    path = Path(settings.system_prompt_file)
    if path.exists():
        logger.info("using local system prompt override: %s", path)
        return path.read_text().strip()
    return BASE_INSTRUCTIONS


def assemble_tools(registry: SkillRegistry, mcp_tools: List[BaseTool]) -> List[BaseTool]:
    """Union of built-in tools, skill-contributed tools, MCP tools, and load_skill."""
    return [
        *BUILTIN_TOOLS,
        *registry.all_tools(),
        *mcp_tools,
        registry.make_load_skill_tool(),
    ]


def build_agent(settings: Settings, checkpointer, registry: SkillRegistry, mcp_tools: List[BaseTool]):
    llm = build_llm(settings)
    tools = assemble_tools(registry, mcp_tools)
    system_prompt = _base_instructions(settings) + SKILLS_SECTION.format(skill_catalog=registry.render_catalog())
    logger.info("building agent with %d tools", len(tools))
    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )
