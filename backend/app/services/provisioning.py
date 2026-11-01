"""Creates, finishes and removes one school's own database.

Steps are recorded in ``provisioning_jobs`` and each one can be repeated safely, so a failed
registration can be retried (``retry``) or cleaned up (``deprovision``)."""
import logging
import secrets
import uuid
from datetime import datetime, timezone

import psycopg
from psycopg import sql
from sqlalchemy import select

from app.context import AppContext
from app.db.tenant import tenant_url
from app.models.control import ProvisioningJob, RegistrationData, School, SchoolDatabase
from app.models.tenant import SchoolSettings, TimingProfile, User, UserRole, Role, AuditLog
from app.services import alembic_runner
from app.services.tenant_seed import ensure_roles_permissions

log = logging.getLogger(__name__)


class ProvisioningFailed(Exception):
    def __init__(self, school_id, step, message):
        super().__init__(message)
        self.school_id, self.step, self.message = school_id, step, message


# ----------------------------------------------------------------------------- PostgreSQL admin work
def create_role_and_database(settings, db_name: str, role: str, password: str) -> None:
    with psycopg.connect(settings.pg_admin_url, autocommit=True) as conn:
        cur = conn.cursor()
        if cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            cur.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
        else:
            cur.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
        cur.execute(sql.SQL("ALTER ROLE {} CONNECTION LIMIT {}").format(
            sql.Identifier(role), sql.Literal(settings.tenant_role_connection_limit)))
        if not cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)).fetchone():
            cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(db_name), sql.Identifier(role)))
        # Only this school's role may connect to this database.
        cur.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db_name)))
        cur.execute(sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO {}").format(
            sql.Identifier(db_name), sql.Identifier(role)))


def drop_role_and_database(settings, db_name: str, role: str) -> None:
    with psycopg.connect(settings.pg_admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db_name)))
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


# ----------------------------------------------------------------------------- orchestration
def _scrub(text: str, secrets_: list[str]) -> str:
    for s in secrets_:
        if s:
            text = text.replace(s, "***")
    return text[:1500]


def _run_step(ctx: AppContext, school_id, step: str, fn, secret_values: list[str]):
    with ctx.control() as db:
        job = ProvisioningJob(school_id=school_id, step=step, status="running")
        db.add(job)
        db.commit()
        job_id = job.id
    try:
        result = fn()
    except Exception as exc:
        message = _scrub(f"{type(exc).__name__}: {exc}", secret_values)
        log.error("Provisioning step %s failed for school %s: %s", step, school_id, message)
        with ctx.control() as db:
            job = db.get(ProvisioningJob, job_id)
            job.status, job.error, job.finished_at = "failed", message, datetime.now(timezone.utc)
            db.commit()
        raise ProvisioningFailed(school_id, step, message) from exc
    with ctx.control() as db:
        job = db.get(ProvisioningJob, job_id)
        job.status, job.finished_at = "done", datetime.now(timezone.utc)
        db.commit()
    return result


def _ensure_registry(ctx: AppContext, school_id) -> tuple[SchoolDatabase, str]:
    """Create the routing row (with an encrypted password) before touching PostgreSQL, so a crash can be cleaned up."""
    with ctx.control() as db:
        reg = db.get(SchoolDatabase, school_id)
        if reg is None:
            ident = uuid.uuid4().hex[:16]
            password = secrets.token_hex(24)
            reg = SchoolDatabase(school_id=school_id, host=ctx.settings.tenant_db_host, port=ctx.settings.tenant_db_port,
                                 db_name=f"school_{ident}", db_role=f"u_{ident}",
                                 password_ciphertext=ctx.box.encrypt(password), status="provisioning")
            db.add(reg)
            db.commit()
        else:
            password = ctx.box.decrypt(reg.password_ciphertext)
        return reg, password


def _seed(ctx: AppContext, school_id, reg: SchoolDatabase) -> None:
    with ctx.control() as db:
        school = db.get(School, school_id)
        data = db.get(RegistrationData, school_id)
        info = dict(name=school.name, state=school.state, board=school.board, timezone=school.timezone)
        admin = dict(name=data.admin_name, email=data.admin_email.lower(), hash=data.admin_password_hash)
        timing = dict(data.timing_profile)
    tdb = ctx.tenants.session(reg)
    try:
        ensure_roles_permissions(tdb)
        if tdb.get(SchoolSettings, 1) is None:
            tdb.add(SchoolSettings(id=1, school_uuid=school_id, **info))
        if not tdb.scalars(select(TimingProfile)).first():
            from datetime import date, time as dtime
            tdb.add(TimingProfile(
                name=timing["name"], class_group=timing.get("class_group"),
                effective_from=date.fromisoformat(timing["effective_from"]) if timing.get("effective_from") else date.today(),
                effective_to=date.fromisoformat(timing["effective_to"]) if timing.get("effective_to") else None,
                working_days=timing["working_days"], saturdays_off=timing.get("saturdays_off", []),
                start_time=dtime.fromisoformat(timing["start_time"]), end_time=dtime.fromisoformat(timing["end_time"]),
                session_mode=timing["session_mode"],
                session_split_time=dtime.fromisoformat(timing["session_split_time"]) if timing.get("session_split_time") else None,
                late_grace_minutes=timing["late_grace_minutes"],
                half_day_after=dtime.fromisoformat(timing["half_day_after"]) if timing.get("half_day_after") else None,
                breaks=timing.get("breaks", []), periods=timing.get("periods", [])))
        if not tdb.scalars(select(User).where(User.email == admin["email"])).first():
            if not admin["hash"]:
                raise RuntimeError("admin password hash is missing; cannot create the first administrator")
            user = User(email=admin["email"], full_name=admin["name"], password_hash=admin["hash"])
            tdb.add(user)
            tdb.flush()
            role = tdb.scalars(select(Role).where(Role.code == "school_admin")).one()
            tdb.add(UserRole(user_id=user.id, role_id=role.id))
            tdb.add(AuditLog(action="school.provisioned", entity="school", entity_id=str(school_id),
                             details={"first_admin": admin["email"]}))
        tdb.commit()
    finally:
        tdb.close()


def provision_school(ctx: AppContext, school_id) -> None:
    """Run (or resume) every provisioning step.  Raises ProvisioningFailed on the first failure."""
    reg, password = _ensure_registry(ctx, school_id)
    sensitive = [password]
    try:
        _run_step(ctx, school_id, "role_and_database",
                  lambda: create_role_and_database(ctx.settings, reg.db_name, reg.db_role, password), sensitive)
        url = tenant_url(reg, ctx.box)
        _run_step(ctx, school_id, "migrate",
                  lambda: alembic_runner.upgrade_to_head(ctx.settings.tenant_alembic_dir, url), sensitive)
        _run_step(ctx, school_id, "seed", lambda: _seed(ctx, school_id, reg), sensitive)
    except ProvisioningFailed:
        with ctx.control() as db:
            db.get(School, school_id).status = "provision_failed"
            db.commit()
        raise
    head = alembic_runner.head_revision(ctx.settings.tenant_alembic_dir)
    with ctx.control() as db:
        school, reg_row, data = db.get(School, school_id), db.get(SchoolDatabase, school_id), db.get(RegistrationData, school_id)
        school.status, reg_row.status, reg_row.schema_revision = "active", "ready", head
        data.admin_password_hash = None                  # no longer needed once the admin exists
        db.commit()


def deprovision_school(ctx: AppContext, school_id) -> None:
    """Remove a school's database and role (failed registrations, tests, closure after export)."""
    with ctx.control() as db:
        reg = db.get(SchoolDatabase, school_id)
        school = db.get(School, school_id)
        if reg is None:
            return
        names = (reg.db_name, reg.db_role)
    ctx.tenants.forget(school_id)
    drop_role_and_database(ctx.settings, *names)
    with ctx.control() as db:
        db.delete(db.get(SchoolDatabase, school_id))
        if school:
            db.get(School, school_id).status = "closed"
        db.commit()
