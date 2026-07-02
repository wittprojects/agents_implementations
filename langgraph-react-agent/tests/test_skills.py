from pathlib import Path

from agent.skills import SkillRegistry

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def test_registry_loads_example_skill():
    registry = SkillRegistry.load(SKILLS_DIR)
    names = [s.name for s in registry.skills]
    assert "weather" in names


def test_catalog_contains_description():
    registry = SkillRegistry.load(SKILLS_DIR)
    catalog = registry.render_catalog()
    assert "weather" in catalog
    assert "weather conditions" in catalog.lower() or "weather" in catalog.lower()


def test_skill_contributes_tools():
    registry = SkillRegistry.load(SKILLS_DIR)
    tool_names = {t.name for t in registry.all_tools()}
    assert "get_weather" in tool_names


def test_load_skill_tool_returns_instructions():
    registry = SkillRegistry.load(SKILLS_DIR)
    load_skill = registry.make_load_skill_tool()
    result = load_skill.invoke({"skill_name": "weather"})
    assert "Weather skill" in result
    assert "get_weather" in result


def test_load_skill_tool_unknown_name():
    registry = SkillRegistry.load(SKILLS_DIR)
    load_skill = registry.make_load_skill_tool()
    result = load_skill.invoke({"skill_name": "does-not-exist"})
    assert "No skill named" in result


def _write_skill(root, name, desc, body):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}")


def test_load_dirs_merges_and_local_overrides(tmp_path):
    base = tmp_path / "skills"
    local = tmp_path / "skills_local"
    _write_skill(base, "shipped", "a shipped skill", "SHIPPED")
    _write_skill(base, "shared", "base version", "BASE_SHARED")
    _write_skill(local, "shared", "local version", "LOCAL_SHARED")
    _write_skill(local, "localonly", "a local skill", "LOCAL_ONLY")

    reg = SkillRegistry.load_dirs([base, local, tmp_path / "missing"])  # missing dir skipped quietly
    assert {s.name for s in reg.skills} == {"shipped", "shared", "localonly"}
    # later dir (local) wins on name conflict
    assert reg.get("shared").instructions == "LOCAL_SHARED"
