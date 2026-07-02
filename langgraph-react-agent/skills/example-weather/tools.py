"""Tools contributed by the weather skill.

Export a module-level ``TOOLS`` list; the skill registry discovers and registers
it automatically. These tools are always bound to the agent, but the model is
steered to use them via the skill's instructions (loaded on demand).
"""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def get_weather(city: str) -> str:
    """Return the current weather report for a city."""
    # Template stub — replace with a real weather API call.
    return f"The weather in {city} is 22°C and sunny (stubbed example data)."


TOOLS = [get_weather]
