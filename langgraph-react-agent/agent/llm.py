"""LLM factory.

Uses a Databricks Foundation Model serving endpoint via ``ChatDatabricks`` so the
agent authenticates with the app's service principal (deployed) or the CLI profile
(local) — no API key to manage — and stays inside Databricks governance/billing.
The app's service principal needs CAN QUERY on the serving endpoint, granted by
attaching a Model Serving endpoint resource to the app.
"""

from __future__ import annotations

from databricks_langchain import ChatDatabricks

from .config import Settings


def build_llm(settings: Settings) -> ChatDatabricks:
    return ChatDatabricks(endpoint=settings.llm_endpoint, temperature=0)
