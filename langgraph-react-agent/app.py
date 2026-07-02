"""Launcher entrypoint for Databricks Apps.

Databricks Apps provide the port to bind to via the ``DATABRICKS_APP_PORT`` env
var (default 8000). We use a Python launcher (rather than an inline uvicorn
command) because the ``app.yaml`` command list is not shell-expanded, so
``$DATABRICKS_APP_PORT`` cannot be interpolated there.

For local development you can instead run: ``uvicorn server.api:app --reload``.
"""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("DATABRICKS_APP_PORT", "8000"))
    uvicorn.run("server.api:app", host="0.0.0.0", port=port, log_level="info")
