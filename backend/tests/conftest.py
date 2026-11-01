import shutil
import sys
import tempfile
import uuid
from datetime import time
from pathlib import Path

import psycopg
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pgserver = pytest.importorskip("pgserver")

from app.config import Settings  # noqa: E402
from app.context import AppContext  # noqa: E402
from app.models.control import Plan  # noqa: E402
from app.schemas import RegistrationIn  # noqa: E402
from app.services import alembic_runner  # noqa: E402


@pytest.fixture(scope="session")
def pg():
    """A real, throw-away PostgreSQL server for the whole test session."""
    directory = tempfile.mkdtemp()
    server = pgserver.get_server(directory)
    yield server
    server.cleanup()
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture()
def settings(pg, tmp_path):
    """Fresh control database per test; school databases are created by the code under test."""
    uri = pg.get_uri()
    control_name = f"control_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{control_name}"')
    sa_admin = make_url(uri.replace("postgresql://", "postgresql+psycopg://"))
    control_url = sa_admin.set(database=control_name)
    host = sa_admin.query.get("host")
    s = Settings(pg_admin_url=uri, control_database_url=control_url.render_as_string(hide_password=False),
                 tenant_db_host=host, secrets_key=Fernet.generate_key().decode(), env="test")
    alembic_runner.upgrade_to_head(s.control_alembic_dir, control_url)
    yield s
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{control_name}" WITH (FORCE)')


@pytest.fixture()
def ctx(settings):
    c = AppContext(settings)
    with c.control() as db:
        db.add_all([Plan(code="starter", name="Starter", student_capacity=250),
                    Plan(code="standard", name="Standard", student_capacity=1000),
                    Plan(code="enterprise", name="Enterprise", student_capacity=None)])
        db.commit()
    yield c
    # remove every school database this test created
    from app.models.control import SchoolDatabase
    from app.services.provisioning import drop_role_and_database
    with c.control() as db:
        regs = [(r.db_name, r.db_role) for r in db.scalars(select(SchoolDatabase))]
    c.close()
    for name, role in regs:
        drop_role_and_database(settings, name, role)


PASSWORD = "Str0ngPassw0rd!"


def timing_payload(**over):
    base = {
        "name": "Regular timing", "working_days": [0, 1, 2, 3, 4, 5], "saturdays_off": [2, 4],
        "start_time": "09:00", "end_time": "15:30", "session_mode": "two", "session_split_time": "12:30",
        "late_grace_minutes": 10, "half_day_after": "11:00",
        "breaks": [{"name": "Lunch", "start": "12:30", "end": "13:10"}],
        "periods": [{"name": "P1", "start": "09:00", "end": "09:45"}, {"name": "P2", "start": "09:45", "end": "10:30"}],
    }
    base.update(over)
    return base


def registration(code="greenfield", email=None, **timing_over):
    return {
        "school": {"name": f"{code.title()} School", "code": code, "state": "Karnataka", "board": "CBSE",
                   "timezone": "Asia/Kolkata"},
        "admin": {"full_name": "Asha Admin", "email": email or f"admin@{code}.example", "password": PASSWORD},
        "plan_code": "starter", "timing": timing_payload(**timing_over),
    }


@pytest.fixture()
def reg_input():
    return lambda code="greenfield", **kw: RegistrationIn.model_validate(registration(code, **kw))
