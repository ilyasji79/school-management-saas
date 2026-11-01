from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def to_sa_url(url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_control(settings):
    engine = create_engine(to_sa_url(settings.control_database_url), pool_size=5, max_overflow=5,
                           pool_pre_ping=True, pool_recycle=1800)
    return engine, sessionmaker(engine, expire_on_commit=False)
