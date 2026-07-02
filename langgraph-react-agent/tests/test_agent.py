import asyncio
from pathlib import Path

import pytest

from agent.checkpointer import memory_checkpointer
from agent.graph import assemble_tools
from agent.skills import SkillRegistry

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def test_assemble_tools_includes_expected():
    registry = SkillRegistry.load(SKILLS_DIR)
    names = {t.name for t in assemble_tools(registry, [])}
    # built-in + skill tool + the progressive-disclosure loader
    assert {"current_time", "add", "get_weather", "load_skill"} <= names


def test_agent_round_trips_and_persists():
    """Build the agent with an in-memory checkpointer + a fake chat model and
    confirm a turn round-trips and is persisted under its thread_id. Skips if the
    installed fake model can't be bound with tools."""
    from langchain.agents import create_agent
    from langchain_core.messages import AIMessage

    try:
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    except ImportError:  # pragma: no cover
        pytest.skip("fake chat model unavailable")

    registry = SkillRegistry.load(SKILLS_DIR)
    model = GenericFakeChatModel(messages=iter([AIMessage(content="Hello from the agent!")]))

    try:
        agent = create_agent(
            model=model,
            tools=assemble_tools(registry, []),
            system_prompt="test",
            checkpointer=memory_checkpointer(),
        )
    except NotImplementedError:
        pytest.skip("fake model does not support bind_tools in this version")

    config = {"configurable": {"thread_id": "t1"}}
    result = asyncio.run(agent.ainvoke({"messages": [{"role": "user", "content": "hi"}]}, config))
    assert result["messages"][-1].content == "Hello from the agent!"

    # Persisted under the thread_id (user + assistant messages present).
    snapshot = asyncio.run(agent.aget_state(config))
    assert len(snapshot.values["messages"]) >= 2
