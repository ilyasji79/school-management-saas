"""Settings are read from environment variables prefixed SAAS_ (or a local .env file)."""
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SAAS_", extra="ignore")

    env: str = "development"

    # A role allowed to CREATE DATABASE / CREATE ROLE (libpq URI). Used only for provisioning.
    pg_admin_url: str
    # The platform (control-plane) database. SQLAlchemy or libpq style URL.
    control_database_url: str
    # Where new school databases are created. Stored per school so a school can be moved later.
    tenant_db_host: str
    tenant_db_port: int | None = None
    # Fernet key used to encrypt per-school database passwords at rest in the control plane.
    secrets_key: str

    session_ttl_minutes: int = 480
    cookie_secure: bool = False
    base_domain: str | None = None          # e.g. "example.com" -> school-code.example.com

    tenant_pool_size: int = 2
    tenant_max_overflow: int = 2
    tenant_max_engines: int = 200           # cap on simultaneously cached school pools
    tenant_role_connection_limit: int = 20

    # Subscription activation arrives with the billing phase; until then this stays False in development.
    require_active_subscription: bool = False

    registration_max_per_ip_per_hour: int = 3
    login_max_failures: int = 5
    login_window_minutes: int = 15

    control_alembic_dir: str = str(BACKEND_DIR / "alembic_control")
    tenant_alembic_dir: str = str(BACKEND_DIR / "alembic_tenant")

    @model_validator(mode="after")
    def _production_guard(self):
        if self.env == "production":
            if not self.cookie_secure:
                raise ValueError("SAAS_COOKIE_SECURE must be true in production")
            if not self.require_active_subscription:
                raise ValueError("SAAS_REQUIRE_ACTIVE_SUBSCRIPTION must be true in production")
        return self
