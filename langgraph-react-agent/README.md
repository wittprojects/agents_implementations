# LangGraph ReAct Agent — Lakebase state + skills + MCP

A generalist, reusable ReAct agent you can fork and specialize. It is a **LangGraph**
agent served over **FastAPI**, deployed as a **Databricks App**, that uses **Lakebase**
(Databricks managed Postgres) as the LangGraph **checkpointer** so conversation state
survives across turns and process restarts.

It is extended two drop-in ways, with **no code changes**:

- **Skills** — add a folder under `skills/` to teach the agent a workflow.
- **MCP servers** — add an entry to `mcp_servers.json` to give it a whole external toolset.

## Architecture

```
HTTP ── FastAPI (server/api.py) ──> LangGraph agent (agent/graph.py, create_agent)
                                         │  tools = builtin + skills + MCP + load_skill
                                         │  LLM  = ChatDatabricks (Foundation Model API)
                                         └─ checkpointer = AsyncPostgresSaver
                                                              │
                                              psycopg AsyncConnectionPool
                                              (OAuthAsyncConnection injects a
                                               rotating Lakebase token as the
                                               Postgres password)
```

Three concerns are wired together at startup (`server/api.py` `lifespan`): the skill
registry, MCP tools, and the Lakebase-backed checkpointer, then the agent itself.

### Why the token dance?

Lakebase has **no static Postgres password** — the password is a short-lived OAuth
token (~1h) minted at runtime for the app's service principal. Because the checkpointer
owns a connection pool for the app's whole lifetime, we:

1. mint + cache the token, refreshing it in the background every 30 min
   (`agent/lakebase.py::LakebaseTokenManager`), and
2. inject the cached token as the password on every new physical connection via a
   psycopg connection subclass (`OAuthAsyncConnection`) — never minting inside the
   connect path (that would block the event loop).

This pattern is ported from the production `genie-api-cache-queue` gateway.

## Layout

| Path | Purpose |
|------|---------|
| `agent/config.py` | Env-driven settings (Pydantic) |
| `agent/lakebase.py` | Token manager + `OAuthAsyncConnection` |
| `agent/checkpointer.py` | Pool → `AsyncPostgresSaver` (+ in-memory fallback) |
| `agent/llm.py` | `ChatDatabricks` factory |
| `agent/skills.py` | Skill registry, catalog, `load_skill` tool |
| `agent/mcp.py` | Load MCP toolsets from `mcp_servers.json` |
| `agent/tools.py` | Built-in example tools |
| `agent/graph.py` | `create_agent(...)` assembly |
| `server/api.py` | FastAPI app: `/health`, `/chat` (SSE), `/threads/{id}/state` |
| `app.py` / `app.yaml` | Databricks App launcher + runtime config |
| `databricks.yml` | Asset Bundle for deploy |
| `skills/example-weather/` | Example skill (`SKILL.md` + `tools.py`) |

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in FEVM profile + Lakebase details
python app.py                 # or: uvicorn server.api:app --reload
```

Then:

```bash
curl localhost:8000/health

# Streamed chat (SSE). Reuse the same thread_id to continue a conversation.
curl -N -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"thread_id":"demo-1","message":"Hi, my name is Alex."}'
curl -N -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"thread_id":"demo-1","message":"What is my name?"}'   # remembers, via Lakebase

curl localhost:8000/threads/demo-1/state
```

If `PGHOST` + `LAKEBASE_ENDPOINT` aren't set, the app falls back to an in-memory
checkpointer (state is lost on restart) so you can develop without Lakebase.

## Add a skill (no code)

Create `skills/<your-skill>/SKILL.md`:

```markdown
---
name: my_skill
description: One-line summary shown in the agent's skill catalog.
when_to_use: The situation in which the model should load this skill.
---

# My skill
Step-by-step instructions the model follows after calling `load_skill("my_skill")`.
```

Optionally add `skills/<your-skill>/tools.py` exposing `TOOLS = [...]` (LangChain tools).
Restart — the skill appears in the catalog and its tools are registered.

## Add an MCP server (no code)

Add an entry to `mcp_servers.json`:

```json
{
  "mcpServers": {
    "uc_functions": {
      "transport": "streamable_http",
      "url": "https://<host>/api/2.0/mcp/functions/<catalog>/<schema>",
      "auth": "databricks"
    }
  }
}
```

`"auth": "databricks"` injects the current bearer token (local: your profile; deployed:
the service principal). HTTP transports are the deployable default; a `stdio` server needs
its binary present in the App container. Unreachable servers are logged and skipped.

## Deploy to Databricks Apps (FEVM)

1. **Provision Lakebase (Autoscaling):** create a Postgres project + branch; note the
   endpoint path for `LAKEBASE_ENDPOINT`.
2. **Deploy the code:** `databricks bundle deploy -t fevm -p fevm` (or `databricks sync`
   + `databricks apps deploy`; profile names are case-sensitive).
3. **Attach resources** to the app: the **Lakebase Database** (auto-injects `PG*` and
   auto-creates the service-principal Postgres role) and the **Model Serving endpoint**
   (`databricks-claude-sonnet-4-5`, CAN QUERY). **Redeploy** so the env vars are injected.
4. **Verify:** open `<app-url>/logz`, hit `<app-url>/health`, and run a `/chat` round-trip
   with a repeated `thread_id`.

### Lakebase binding note

Binding a Lakebase **Autoscaling** database inside an Asset Bundle is version-sensitive
(a non-deterministic resource id makes `apps.resources[].database` hard to reference). The
robust path is to attach the DB resource to the app **after** first deploy via the Apps UI
or `databricks apps`. See `genie-api-cache-queue/docs/dab_chicken_egg_findings.md` for the
full analysis and the portable "Option C" fallback (self-resolve host + bootstrap the SP
role via an init job) if stable-name binding isn't available in your workspace.

## Test

```bash
pip install pytest
pytest            # from this folder; runs hermetically (no Lakebase needed)
```

## Version notes

`create_agent` (LangChain 1.x) is the successor to the deprecated
`langgraph.prebuilt.create_react_agent`; it supports middleware (`@dynamic_prompt`,
`@wrap_model_call` + `request.override(tools=...)`) if you later want per-skill *tool*
hiding rather than just instruction loading. Pin the langchain/langgraph versions in
`requirements.txt` — this area moves fast.
