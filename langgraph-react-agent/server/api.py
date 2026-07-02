"""FastAPI application.

Wires the agent together on startup (skills → MCP tools → Lakebase checkpointer →
agent) and exposes:

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

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.checkpointer import build_checkpointer, memory_checkpointer
from agent.config import Settings
from agent.dbx import bearer_token, configure_auth, get_workspace_client
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


async def _stream(thread_id: str, message: str):
    config = {"configurable": {"thread_id": thread_id}}
    inputs = {"messages": [{"role": "user", "content": message}]}
    async for chunk, _meta in state.agent.astream(inputs, config, stream_mode="messages"):
        content = getattr(chunk, "content", "")
        if content:
            yield f"data: {json.dumps({'content': content})}\n\n"
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
