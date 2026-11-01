from alembic import context
from sqlalchemy import create_engine, pool

from app.models.control import ControlBase

config = context.config
target_metadata = ControlBase.metadata


def run_migrations_online():
    url = config.attributes.get("sqlalchemy_url") or config.get_main_option("sqlalchemy.url")
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
