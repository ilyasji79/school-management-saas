"""Thin wrapper so the application can run Alembic programmatically against any database URL."""
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL


def _config(script_dir: str, url) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", script_dir)
    cfg.attributes["sqlalchemy_url"] = url
    return cfg


def head_revision(script_dir: str) -> str:
    return ScriptDirectory(script_dir).get_current_head()


def current_revision(url) -> str | None:
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def upgrade_to_head(script_dir: str, url) -> None:
    command.upgrade(_config(script_dir, url), "head")


def make_revision(script_dir: str, url, message: str) -> None:
    command.revision(_config(script_dir, url), message=message, autogenerate=True, rev_id="0001")
