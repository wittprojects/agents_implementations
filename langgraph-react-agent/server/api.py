"""FastAPI application.

Wires the agent together on startup (skills → MCP tools → Lakebase checkpointer →
agent) and exposes:

- ``GET  /``                           minimal streaming chat UI (static/index.html)
- ``GET  /health``                     liveness + checkpointer status
- ``POST /chat``                       streamed (SSE) chat, state keyed by thread_id
- ``GET  /threads/{thread_id}/state``  inspect a conversation's persisted messages

Conversation persistence is entirely the checkpointer keyed by ``thread_id`` — the
same thread_id resumes a conversation; a new one starts fresh.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from agent.checkpointer import build_checkpointer, memory_checkpointer
from agent.config import Settings
from agent.dbx import bearer_token, configure_auth, get_workspace_client, resolve_lakebase_host
from agent.graph import build_agent
from agent.lakebase import init_token_manager
from agent.mcp import load_mcp_tools
from agent.skills import SkillRegistry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    thread_id: str
    message: str


class _State:
    settings: Settings | None = None
    agent = None
    pool = None
    token_manager = None


state = _State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    state.settings = settings
    configure_auth(settings)

    registry = SkillRegistry.load(settings.skills_dir)
    logger.info("loaded %d skill(s): %s", len(registry.skills), [s.name for s in registry.skills])

    ws = get_workspace_client(settings)
    mcp_tools = await load_mcp_tools(settings.mcp_config_path, bearer_token=bearer_token(ws))
    logger.info("loaded %d MCP tool(s)", len(mcp_tools))

    # If the host wasn't injected, resolve it from the Lakebase endpoint path.
    if settings.lakebase_endpoint and not settings.pg_host:
        settings.pg_host = await asyncio.to_thread(resolve_lakebase_host, ws, settings.lakebase_endpoint)
        logger.info("resolved Lakebase host: %s", settings.pg_host)

    if settings.lakebase_configured:
        tm = init_token_manager(ws, settings.lakebase_endpoint, settings.token_refresh_interval_s)
        await asyncio.to_thread(tm.seed)  # mint first token off the event loop
        checkpointer, pool = await build_checkpointer(settings)
        tm.start_refresh()
        state.token_manager, state.pool = tm, pool
        logger.info("using Lakebase checkpointer")
    else:
        checkpointer = memory_checkpointer()
        logger.warning(
            "Lakebase not configured (need PGHOST + LAKEBASE_ENDPOINT); using in-memory checkpointer "
            "— conversation state will NOT survive restarts"
        )

    state.agent = build_agent(settings, checkpointer, registry, mcp_tools)
    logger.info("agent ready")
    try:
        yield
    finally:
        if state.token_manager is not None:
            await state.token_manager.stop()
        if state.pool is not None:
            await state.pool.close()


app = FastAPI(title="LangGraph ReAct Agent — Lakebase + skills", lifespan=lifespan)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health():
    checkpointer = "memory"
    if state.pool is not None:
        try:
            async with state.pool.connection() as conn:
                await conn.execute("SELECT 1")
            checkpointer = "lakebase-ok"
        except Exception:
            logger.exception("checkpointer health check failed")
            checkpointer = "lakebase-error"
    return {"status": "ok" if state.agent is not None else "starting", "checkpointer": checkpointer}


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _text(content) -> str:
    """Extract plain text from a message chunk's content (str or content-block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


async def _stream(thread_id: str, message: str):
    """Stream typed SSE events: {"type":"token","content":...} for assistant text and
    {"type":"tool","name":...} when the agent calls a tool. Tool *outputs* (e.g. the
    load_skill instructions) are intentionally not streamed to the chat."""
    config = {"configurable": {"thread_id": thread_id}}
    inputs = {"messages": [{"role": "user", "content": message}]}
    try:
        async for chunk, _meta in state.agent.astream(inputs, config, stream_mode="messages"):
            if not isinstance(chunk, AIMessage):
                continue  # skip tool/human/system messages (AIMessageChunk is an AIMessage)
            for tc in getattr(chunk, "tool_call_chunks", None) or []:
                if tc.get("name"):
                    yield _sse({"type": "tool", "name": tc["name"]})
            text = _text(chunk.content)
            if text:
                yield _sse({"type": "token", "content": text})
    except Exception as exc:  # surface errors to the UI instead of a silent hang
        logger.exception("error while streaming chat")
        yield _sse({"type": "error", "message": str(exc)})
    yield "data: [DONE]\n\n"


@app.post("/chat")
async def chat(req: ChatRequest):
    if state.agent is None:
        raise HTTPException(status_code=503, detail="agent not ready")
    return StreamingResponse(
        _stream(req.thread_id, req.message),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.get("/threads/{thread_id}/state")
async def thread_state(thread_id: str):
    if state.agent is None:
        raise HTTPException(status_code=503, detail="agent not ready")
    snapshot = await state.agent.aget_state({"configurable": {"thread_id": thread_id}})
    messages = (snapshot.values or {}).get("messages", []) if snapshot else []
    return {
        "thread_id": thread_id,
        "num_messages": len(messages),
        "messages": [{"type": getattr(m, "type", "?"), "content": getattr(m, "content", "")} for m in messages],
    }
