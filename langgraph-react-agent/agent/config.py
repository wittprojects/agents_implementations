"""Runtime configuration.

Reads from environment variables (and a local ``.env`` for development). In a
deployed Databricks App the Lakebase ``PG*`` vars and ``LAKEBASE_ENDPOINT`` are
injected automatically by the attached Database resource, and the service
principal credentials (``DATABRICKS_CLIENT_ID`` / ``DATABRICKS_CLIENT_SECRET`` /
``DATABRICKS_HOST``) are injected by the Apps runtime.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # --- LLM (Databricks Foundation Model serving endpoint) ---
    llm_endpoint: str = Field(default="databricks-claude-sonnet-4-5", alias="LLM_ENDPOINT")

    # --- Lakebase / Postgres ---
    # PG* are auto-injected when the Lakebase Database resource is attached to the app.
    # LAKEBASE_ENDPOINT is the endpoint path used to mint the short-lived DB credential.
    lakebase_endpoint: str | None = Field(default=None, alias="LAKEBASE_ENDPOINT")
    pg_host: str | None = Field(default=None, alias="PGHOST")
    pg_port: int = Field(default=5432, alias="PGPORT")
    pg_user: str | None = Field(default=None, alias="PGUSER")
    pg_database: str = Field(default="databricks_postgres", alias="PGDATABASE")
    pg_sslmode: str = Field(default="require", alias="PGSSLMODE")
    # Dedicated schema for checkpoint tables. The app's role owns it (it has
    # database-level CREATE), avoiding the "permission denied for schema public"
    # that Postgres 15+ raises for non-owner roles.
    pg_schema: str = Field(default="langgraph", alias="PGSCHEMA")

    # --- Auth / environment ---
    databricks_profile: str | None = Field(default=None, alias="DATABRICKS_PROFILE")
    databricks_app_name: str | None = Field(default=None, alias="DATABRICKS_APP_NAME")
    databricks_client_id: str | None = Field(default=None, alias="DATABRICKS_CLIENT_ID")

    # --- Agent runtime ---
    # JWT lives ~1h; refresh comfortably ahead of expiry.
    token_refresh_interval_s: int = Field(default=1800, alias="TOKEN_REFRESH_INTERVAL_S")
    port: int = Field(default=8000, alias="DATABRICKS_APP_PORT")

    # --- Extension points (paths relative to the working directory) ---
    skills_dir: str = Field(default="skills", alias="SKILLS_DIR")
    mcp_config_path: str = Field(default="mcp_servers.json", alias="MCP_CONFIG_PATH")

    # Optional deployment-specific overrides, layered on top of the committed
    # defaults above. Intended to be gitignored so a deployment can specialize the
    # agent (extra skills, real MCP servers, a domain system prompt) without
    # touching the shared scaffold. All are no-ops when the file/dir is absent.
    local_skills_dir: str = Field(default="skills_local", alias="LOCAL_SKILLS_DIR")
    local_mcp_config_path: str = Field(default="mcp_servers.local.json", alias="LOCAL_MCP_CONFIG_PATH")
    system_prompt_file: str = Field(default="system_prompt.local.md", alias="SYSTEM_PROMPT_FILE")

    @property
    def is_deployed(self) -> bool:
        """True when running inside a Databricks App (name is injected by the runtime)."""
        return bool(self.databricks_app_name)

    @property
    def lakebase_configured(self) -> bool:
        return bool(self.pg_host and self.lakebase_endpoint)
