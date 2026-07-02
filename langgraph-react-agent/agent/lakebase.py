"""Lakebase credential management.

Lakebase (Databricks managed Postgres) has **no static password** — the Postgres
password is a short-lived OAuth token (~1h TTL) minted at runtime for the app's
service principal. A long-lived connection pool (which the LangGraph checkpointer
owns for the app's lifetime) must therefore refresh that token.

Design (ported from the production ``genie-api-cache-queue`` gateway, adapted from
asyncpg to psycopg which the LangGraph Postgres checkpointer requires):

- A background asyncio task refreshes a cached token every ~30 min, well ahead of
  the ~1h expiry, with bounded retry/backoff and "keep the last good token on
  failure" semantics.
- The psycopg connection factory (:class:`OAuthAsyncConnection`) reads that cache
  **synchronously and without blocking** on every new physical connection. We do
  NOT mint inside the connect path — an SDK round trip there (100-500ms) would
  stall the event loop on every new connection.

Note: the token expiry is enforced only at *login*; already-open connections keep
working past expiry. So only newly established connections need a fresh token.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

import psycopg

logger = logging.getLogger(__name__)

# JWT lives ~1h; retry a failed refresh with increasing backoff before giving up
# for this cycle (the pool keeps using the last good token in the meantime).
_RETRY_BACKOFFS_S = (5, 10, 20, 40, 60)


class LakebaseTokenManager:
    """Mints, caches, and background-refreshes the Lakebase OAuth token."""

    def __init__(self, ws_client, endpoint: str, refresh_interval_s: int = 1800):
        self._ws = ws_client
        self._endpoint = endpoint
        self._refresh_interval_s = refresh_interval_s
        self._token: Optional[str] = None
        self._lock = threading.Lock()
        self._task: Optional[asyncio.Task] = None

    # --- minting -----------------------------------------------------------
    def _mint(self) -> str:
        """Mint a fresh credential. Tries the typed Autoscaling SDK method first,
        then raw REST fallbacks (the Lakebase SDK surface is still stabilizing)."""
        # 1) Typed SDK (Autoscaling)
        try:
            return self._ws.postgres.generate_database_credential(endpoint=self._endpoint).token
        except Exception:
            logger.debug("postgres.generate_database_credential unavailable; trying raw API", exc_info=True)
        # 2) Raw Autoscaling endpoint API
        try:
            resp = self._ws.api_client.do(
                "POST", "/api/2.0/postgres/credentials", body={"endpoint": self._endpoint}
            )
            if resp and resp.get("token"):
                return resp["token"]
        except Exception:
            logger.debug("raw /api/2.0/postgres/credentials failed; trying instance API", exc_info=True)
        # 3) Raw provisioned/instance API (endpoint treated as an instance name)
        resp = self._ws.api_client.do(
            "POST", "/api/2.0/database/credentials", body={"instance_names": [self._endpoint]}
        )
        token = (resp or {}).get("token")
        if not token:
            raise RuntimeError(f"Could not mint Lakebase credential for endpoint '{self._endpoint}'")
        return token

    # --- lifecycle ---------------------------------------------------------
    def seed(self) -> None:
        """Mint the first token synchronously, before the pool is created, so the
        very first connection has a valid password. Safe to call off the event loop."""
        token = self._mint()
        with self._lock:
            self._token = token
        logger.info("Lakebase token seeded for endpoint '%s'", self._endpoint)

    def current_token(self) -> str:
        with self._lock:
            if not self._token:
                raise RuntimeError("Lakebase token has not been seeded")
            return self._token

    def start_refresh(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # --- refresh loop ------------------------------------------------------
    async def _refresh_once(self) -> bool:
        for attempt, backoff in enumerate((0, *_RETRY_BACKOFFS_S)):
            if backoff:
                await asyncio.sleep(backoff)
            try:
                token = await asyncio.to_thread(self._mint)
                with self._lock:
                    self._token = token
                logger.info("Lakebase token refreshed")
                return True
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Lakebase token refresh failed (attempt %d); keeping cached token",
                               attempt, exc_info=True)
        return False

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._refresh_interval_s)
                await self._refresh_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("unexpected error in Lakebase token refresh loop")


# --- module-level singleton --------------------------------------------------
# A single endpoint per app, so a module-level manager is sufficient. (The genie
# gateway keeps per-instance state only because it fans out to many endpoints.)
_manager: Optional[LakebaseTokenManager] = None


def init_token_manager(ws_client, endpoint: str, refresh_interval_s: int = 1800) -> LakebaseTokenManager:
    global _manager
    _manager = LakebaseTokenManager(ws_client, endpoint, refresh_interval_s)
    return _manager


def get_token_manager() -> LakebaseTokenManager:
    if _manager is None:
        raise RuntimeError("Lakebase token manager not initialized")
    return _manager


class OAuthAsyncConnection(psycopg.AsyncConnection):
    """psycopg async connection that injects the current Lakebase OAuth token as the
    password on every new physical connection. Handed to the psycopg pool via
    ``connection_class=``; the pool calls this ``connect`` for each new connection."""

    @classmethod
    async def connect(cls, conninfo: str = "", **kwargs):  # type: ignore[override]
        kwargs["password"] = get_token_manager().current_token()
        return await super().connect(conninfo, **kwargs)
