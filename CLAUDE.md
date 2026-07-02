# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

This repo is a collection of **reference implementations of popular agent architectures**. Each
example is a self-contained, runnable illustration of one architecture — optimized for being read
and learned from, not for being a shared framework. Favor clarity and explicitness over abstraction;
duplication across examples is acceptable when it keeps each one readable in isolation.

Remote: https://github.com/wittprojects/agents_implementations

## Examples

Each example's own `README.md` is the authoritative source for how to run, test, and deploy it.

- **`langgraph-react-agent/`** — generalist LangGraph ReAct agent (via `langchain.agents.create_agent`)
  served over FastAPI, deployed as a Databricks App, with **Lakebase Postgres as the checkpointer**
  for conversation state. It is the reference for two drop-in extension mechanisms every future
  example should reuse where relevant:
  - **Skills** (`skills/<name>/SKILL.md` + optional `tools.py`) — workflow instruction modules
    surfaced by progressive disclosure via a `load_skill` tool. Add a folder, no code change.
  - **MCP servers** (`mcp_servers.json`) — external toolsets loaded via `langchain-mcp-adapters`.
    Add a config entry, no code change.

## Stack

- **Python** throughout.
- **LangGraph / LangChain** — graph-based, stateful agents (supervisor / multi-agent, etc.).
- **Anthropic and OpenAI SDKs** — both provider-native agent loops and the "from scratch" core
  patterns built directly on a raw LLM client (no framework).
- **FastAPI** — every example is wrapped in a FastAPI app so it runs locally and deploys unchanged.
- **Databricks Apps** — the deployment target for each example.

## Big picture: one folder = one deployable example

The load-bearing architectural convention is that **each architecture lives in its own folder and is
a complete, independently deployable unit**. A folder bundles three concerns that must stay in sync:

1. the agent logic (LangGraph graph, or a from-scratch SDK loop),
2. a FastAPI app that exposes it over HTTP, and
3. an `app.yaml` that makes it a Databricks App.

Understanding any single example means reading all three together — the graph defines behavior, the
FastAPI layer defines the interface, and `app.yaml` defines how it boots in Databricks. Keep these
consistent when editing one of them.

**Lakebase gotcha (reused across examples that persist state):** Lakebase has no static Postgres
password — it's a ~1h OAuth token minted at runtime for the app's service principal. A long-lived
connection pool must refresh it. See `langgraph-react-agent/agent/lakebase.py` for the pattern
(background token refresh + a psycopg connection subclass that injects the cached token on connect),
ported from the production `genie-api-cache-queue` gateway.

Architectures to cover fall into three families:
- **Core patterns from scratch** (raw Anthropic/OpenAI SDK): ReAct, reflection, planning, tool-use,
  multi-agent, orchestrator-worker, evaluator-optimizer.
- **LangGraph / LangChain** graph-based agents.
- **Provider-native SDK agents** (OpenAI Agents SDK, Anthropic tool-use loops).

## Conventions for a new example

- Create a new top-level folder named for the architecture; do not add cross-example shared packages
  unless a pattern clearly repeats and the user asks for it.
- Include a `README.md` (what the architecture is, how to run it, how to deploy), the FastAPI
  `app`, and an `app.yaml`.
- Read model/provider config and secrets from **environment variables** — never hardcode keys.
- Default to the latest Claude models when using the Anthropic SDK.

## Commands

Work from within an example folder (e.g. `langgraph-react-agent/`):

- Install: `python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest`.
- Run locally: `python app.py` (or `uvicorn server.api:app --reload`).
- Tests: `pytest` (single test: `pytest tests/test_skills.py::test_name`).

**Network note:** `pypi.org` is firewalled on this machine (`files.pythonhosted.org` is reachable but
the index is blocked, and no mirror is configured), so `pip`/`uv` install fails locally. The base
conda env (`/Users/alex.witt/miniconda3/bin/python`) already has `langchain-core`, `pydantic`,
`pyyaml`, and `pytest`, which is enough to run the pure-Python tests (skills registry, MCP config
parsing). The full stack (`langchain`, `langgraph`, `psycopg` v3) installs from Databricks' own mirror
inside the App container at deploy time — so the deployed app is the full-stack acceptance test.

## Databricks Apps deployment

Deployment specifics (the `app.yaml` command format, the port the server must bind to, workspace
sync, and `databricks apps deploy`) are handled by the **`fe-databricks-tools:databricks-apps`
skill** and the **`databricks-apps-developer` agent** available in this environment — use them as the
source of truth rather than hardcoding CLI flags here, since they track the current API.
