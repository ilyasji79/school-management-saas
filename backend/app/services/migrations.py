"""Runs schema migrations on every school database, one school at a time, and records the result per school.

A failure on one school never stops the others; re-running picks up the schools that are still behind."""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, text

from app.context import AppContext
from app.db.tenant import tenant_url
from app.models.control import MigrationRun, School, SchoolDatabase, UsageSnapshot
from app.models.tenant import User
from app.services import alembic_runner
from app.services.tenant_seed import ensure_roles_permissions

log = logging.getLogger(__name__)
LOCK_KEY = 727001


class MigrationInProgress(Exception):
    pass


def run_tenant_migrations(ctx: AppContext, only_school_ids: list | None = None) -> dict:
    head = alembic_runner.head_revision(ctx.settings.tenant_alembic_dir)
    batch = uuid.uuid4()
    summary = {"batch_id": str(batch), "head": head, "upgraded": 0, "up_to_date": 0, "failed": []}
    with ctx.control_engine.connect() as lock_conn:
        if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar():
            raise MigrationInProgress("another migration run is already in progress")
        try:
            with ctx.control() as db:
                rows = db.execute(select(SchoolDatabase, School.code).join(School, School.id == SchoolDatabase.school_id)
                                  .where(SchoolDatabase.status == "ready").order_by(School.code)).all()
            for reg, code in rows:
                if only_school_ids and reg.school_id not in only_school_ids:
                    continue
                _migrate_one(ctx, reg, code, head, batch, summary)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            lock_conn.commit()
    return summary


def _migrate_one(ctx, reg, code, head, batch, summary):
    started = datetime.now(timezone.utc)
    url = tenant_url(reg, ctx.box)
    before = None
    try:
        before = alembic_runner.current_revision(url)
        if before == head:
            summary["up_to_date"] += 1
            return
        alembic_runner.upgrade_to_head(ctx.settings.tenant_alembic_dir, url)
        tdb = ctx.tenants.session(reg)
        try:
            ensure_roles_permissions(tdb)
        finally:
            tdb.close()
        status, error = "ok", None
        summary["upgraded"] += 1
    except Exception as exc:
        status, error = "failed", f"{type(exc).__name__}: {str(exc)[:1500]}"
        summary["failed"].append({"school": code, "error": error})
        log.error("Migration failed for school %s: %s", code, error)
    with ctx.control() as db:
        db.add(MigrationRun(batch_id=batch, school_id=reg.school_id, from_revision=before,
                            to_revision=head if status == "ok" else before, status=status, error=error,
                            started_at=started, finished_at=datetime.now(timezone.utc)))
        if status == "ok":
            db.get(SchoolDatabase, reg.school_id).schema_revision = head
        db.commit()


def collect_usage(ctx: AppContext) -> dict:
    """Platform-wide numbers without cross-database queries: read each school, store a snapshot centrally."""
    out = {"collected": 0, "failed": []}
    with ctx.control() as db:
        rows = db.execute(select(SchoolDatabase, School.code).join(School, School.id == SchoolDatabase.school_id)
                          .where(SchoolDatabase.status == "ready")).all()
    for reg, code in rows:
        try:
            tdb = ctx.tenants.session(reg)
            try:
                users = tdb.scalar(select(func.count()).select_from(User))
            finally:
                tdb.close()
            with ctx.control() as db:
                db.add(UsageSnapshot(school_id=reg.school_id, user_count=users, schema_revision=reg.schema_revision))
                db.commit()
            out["collected"] += 1
        except Exception as exc:
            out["failed"].append({"school": code, "error": f"{type(exc).__name__}"})
    return out
