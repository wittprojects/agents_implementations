"""Databricks SDK client helpers.

Centralizes how we obtain a ``WorkspaceClient`` and how auth is resolved so that
every Databricks client in the app (the SDK, ``ChatDatabricks``, MCP bearer
tokens) uses one consistent identity:

- **Deployed** (inside a Databricks App): the injected service principal is used
  automatically by the SDK default auth chain — no arguments needed.
- **Local**: the configured CLI profile (e.g. ``FEVM``) is used.
"""

from __future__ import annotations

import logging
import os

from databricks.sdk import WorkspaceClient

from .config import Settings

logger = logging.getLogger(__name__)


def configure_auth(settings: Settings) -> None:
    """Pin the SDK profile for local runs so ``ChatDatabricks`` (which uses the SDK
    default auth chain, not our explicit ``WorkspaceClient``) resolves the same
    identity. No-op when deployed."""
    if not settings.is_deployed and settings.databricks_profile:
        os.environ.setdefault("DATABRICKS_CONFIG_PROFILE", settings.databricks_profile)
        logger.info("local auth pinned to profile '%s'", settings.databricks_profile)


def get_workspace_client(settings: Settings) -> WorkspaceClient:
    if settings.is_deployed:
        return WorkspaceClient()
    if settings.databricks_profile:
        return WorkspaceClient(profile=settings.databricks_profile)
    return WorkspaceClient()


def resolve_lakebase_host(ws: WorkspaceClient, endpoint_path: str) -> str | None:
    """Resolve the Postgres host for a Lakebase endpoint path.

    Attaching a Lakebase (autoscaling) `postgres` resource to the app provisions the
    service principal's Postgres role but does not inject PGHOST. Given
    LAKEBASE_ENDPOINT (``projects/<p>/branches/<b>/endpoints/<e>``), we look the host
    up from the endpoint's status so deployments only need to set LAKEBASE_ENDPOINT.
    """
    try:
        resp = ws.api_client.do("GET", f"/api/2.0/postgres/{endpoint_path}")
        status = (resp or {}).get("status", {}) or {}
        hosts = status.get("hosts") or {}
        return hosts.get("host") or status.get("host")
    except Exception:
        logger.warning("could not resolve Lakebase host for %s", endpoint_path, exc_info=True)
        return None


def bearer_token(ws: WorkspaceClient) -> str | None:
    """Best-effort extraction of the current bearer token (used to authenticate to
    Databricks-hosted MCP servers). Returns None if it cannot be obtained."""
    try:
        headers = ws.config.authenticate()  # {"Authorization": "Bearer <token>"}
        auth = headers.get("Authorization", "")
        return auth.removeprefix("Bearer ").strip() or None
    except Exception:
        logger.debug("could not obtain bearer token", exc_info=True)
        return None
