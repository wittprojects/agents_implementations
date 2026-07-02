"""LangGraph checkpointer backed by Lakebase Postgres.

Builds an async psycopg connection pool whose connections authenticate with a
rotating Lakebase OAuth token (see :mod:`agent.lakebase`), then wraps it in an
``AsyncPostgresSaver``. Conversation state is keyed by ``thread_id`` and survives
process restarts because it lives in Postgres.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import Settings
from .lakebase import OAuthAsyncConnection

logger = logging.getLogger(__name__)


def _conninfo(settings: Settings) -> str:
    # PGUSER is auto-injected when the DB resource is attached; fall back to the
    # service principal's client id. No password here — it is injected per-connect.
    user = settings.pg_user or settings.databricks_client_id
    return (
        f"host={settings.pg_host} port={settings.pg_port} "
        f"dbname={settings.pg_database} user={user} sslmode={settings.pg_sslmode}"
    )


async def build_checkpointer(settings: Settings) -> tuple[AsyncPostgresSaver, AsyncConnectionPool]:
    """Open the pool, run one-time table setup, and return (saver, pool).

    The caller must have seeded the Lakebase token manager first, and is
    responsible for closing the returned pool on shutdown.
    """
    pool = AsyncConnectionPool(
        conninfo=_conninfo(settings),
        connection_class=OAuthAsyncConnection,
        # These kwargs are REQUIRED by the LangGraph Postgres checkpointer.
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        min_size=1,
        max_size=10,
        # Recycle connections before the ~1h token TTL so replacements re-auth with
        # a fresh token (belt-and-suspenders: open connections survive expiry anyway).
        max_lifetime=min(settings.token_refresh_interval_s, 1800),
        open=False,
    )
    await pool.open(wait=True)
    saver = AsyncPostgresSaver(pool)
    await saver.setup()  # idempotent; creates checkpoint tables + runs migrations
    logger.info("Lakebase checkpointer initialized (pool open, tables ready)")
    return saver, pool


def memory_checkpointer():
    """In-memory fallback for local dev / tests when Lakebase is not configured."""
    try:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
    except ImportError:  # pragma: no cover - name varies across versions
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
