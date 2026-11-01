import copy
import shutil
import uuid
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import OperationalError

from app.db.tenant import tenant_url
from app.models.control import MigrationRun, ProvisioningJob, RegistrationData, School, SchoolDatabase
from app.models.tenant import AuditLog, Role, SchoolSettings, TimingProfile, User
from app.services import alembic_runner
from app.services.migrations import collect_usage, run_tenant_migrations
from app.services.provisioning import (ProvisioningFailed, deprovision_school, provision_school)
from app.services.registration import RegistrationError, register_school
from tests.conftest import PASSWORD, registration  # noqa: F401


def school_id(ctx, code):
    with ctx.control() as db:
        return db.scalars(select(School).where(School.code == code)).one().id


def test_registration_creates_a_separate_database_with_everything_seeded(ctx, reg_input):
    result = register_school(ctx, reg_input("greenfield"))
    assert result["status"] == "active"
    sid = school_id(ctx, "greenfield")
    with ctx.control() as db:
        reg = db.get(SchoolDatabase, sid)
        assert reg.status == "ready" and reg.db_name.startswith("school_") and reg.schema_revision == "0001"
        assert db.get(RegistrationData, sid).admin_password_hash is None     # removed after provisioning
        steps = {j.step: j.status for j in db.scalars(select(ProvisioningJob).where(ProvisioningJob.school_id == sid))}
        assert steps == {"role_and_database": "done", "migrate": "done", "seed": "done"}
        assert "Str0ng" not in reg.password_ciphertext                          # encrypted, not plain
    t = ctx.tenants.session(reg)
    try:
        assert t.get(SchoolSettings, 1).school_uuid == sid
        assert {r.code for r in t.scalars(select(Role))} >= {"school_admin", "teacher", "parent"}
        user = t.scalars(select(User)).one()
        assert user.email == "admin@greenfield.example" and user.password_hash.startswith("$argon2")
        profile = t.scalars(select(TimingProfile)).one()
        assert profile.session_mode == "two" and profile.late_grace_minutes == 10 and profile.saturdays_off == [2, 4]
        assert t.scalars(select(AuditLog).where(AuditLog.action == "school.provisioned")).one()
    finally:
        t.close()


def test_school_code_must_be_unique_and_plan_must_exist(ctx, reg_input):
    register_school(ctx, reg_input("alpha"))
    with pytest.raises(RegistrationError) as e:
        register_school(ctx, reg_input("alpha", ))
    assert e.value.code == "code_taken" and e.value.status == 409
    data = reg_input("beta")
    data.plan_code = "nonexistent"
    with pytest.raises(RegistrationError) as e:
        register_school(ctx, data)
    assert e.value.code == "unknown_plan"


def test_each_school_has_its_own_database_and_data_does_not_cross(ctx, reg_input):
    register_school(ctx, reg_input("alpha", ))
    register_school(ctx, reg_input("beta"))
    with ctx.control() as db:
        regs = {s.code: db.get(SchoolDatabase, s.id) for s in db.scalars(select(School))}
    assert regs["alpha"].db_name != regs["beta"].db_name
    a, b = ctx.tenants.session(regs["alpha"]), ctx.tenants.session(regs["beta"])
    try:
        assert a.scalars(select(User)).one().email == "admin@alpha.example"
        assert b.scalars(select(User)).one().email == "admin@beta.example"
        assert a.get(SchoolSettings, 1).name == "Alpha School" and b.get(SchoolSettings, 1).name == "Beta School"
    finally:
        a.close(); b.close()


def test_a_school_role_cannot_open_another_schools_database(ctx, reg_input, settings):
    register_school(ctx, reg_input("alpha"))
    register_school(ctx, reg_input("beta"))
    with ctx.control() as db:
        regs = {s.code: db.get(SchoolDatabase, s.id) for s in db.scalars(select(School))}
    a, b = regs["alpha"], regs["beta"]
    own = tenant_url(a, ctx.box)
    other = own.set(database=b.db_name)                   # school A's credentials, school B's database
    ok_engine = create_engine(own)
    with ok_engine.connect() as c:
        assert c.execute(text("select 1")).scalar() == 1
    ok_engine.dispose()
    bad_engine = create_engine(other)
    with pytest.raises(OperationalError):
        bad_engine.connect()
    bad_engine.dispose()


def test_provisioning_is_repeatable_and_resumes_after_a_failure(ctx, reg_input, monkeypatch):
    from app.services import provisioning
    calls = {"n": 0}
    real = provisioning._seed

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash while seeding")
        return real(*a, **k)

    monkeypatch.setattr(provisioning, "_seed", flaky)
    result = register_school(ctx, reg_input("gamma"))
    assert result["status"] == "provision_failed"
    sid = school_id(ctx, "gamma")
    with ctx.control() as db:
        assert db.get(School, sid).status == "provision_failed"
        failed = db.scalars(select(ProvisioningJob).where(ProvisioningJob.status == "failed")).one()
        assert failed.step == "seed" and "simulated crash" in failed.error
        assert db.get(RegistrationData, sid).admin_password_hash is not None   # kept so the retry can finish
    provision_school(ctx, sid)                                                # retry
    with ctx.control() as db:
        assert db.get(School, sid).status == "active"
        assert db.get(RegistrationData, sid).admin_password_hash is None
    provision_school(ctx, sid)                                                # running again changes nothing
    with ctx.control() as db:
        reg = db.get(SchoolDatabase, sid)
    t = ctx.tenants.session(reg)
    try:
        assert len(t.scalars(select(User)).all()) == 1
    finally:
        t.close()


def test_failure_messages_never_contain_the_database_password(ctx, reg_input, monkeypatch):
    from app.services import provisioning
    pw_holder = {}
    real = provisioning.create_role_and_database

    def boom(settings, db_name, role, password):
        pw_holder["pw"] = password
        raise RuntimeError(f"cannot create role with password {password}")

    monkeypatch.setattr(provisioning, "create_role_and_database", boom)
    assert register_school(ctx, reg_input("delta"))["status"] == "provision_failed"
    with ctx.control() as db:
        err = db.scalars(select(ProvisioningJob).where(ProvisioningJob.status == "failed")).one().error
    assert pw_holder["pw"] not in err and "***" in err


def test_deprovision_removes_database_and_role(ctx, reg_input, settings):
    register_school(ctx, reg_input("epsilon"))
    sid = school_id(ctx, "epsilon")
    with ctx.control() as db:
        reg = db.get(SchoolDatabase, sid)
        name, role = reg.db_name, reg.db_role
    deprovision_school(ctx, sid)
    with psycopg.connect(settings.pg_admin_url) as conn:
        assert conn.execute("select 1 from pg_database where datname=%s", (name,)).fetchone() is None
        assert conn.execute("select 1 from pg_roles where rolname=%s", (role,)).fetchone() is None
    with ctx.control() as db:
        assert db.get(School, sid).status == "closed" and db.get(SchoolDatabase, sid) is None


def _copy_with_new_revision(settings, tmp_path, body: str):
    target = tmp_path / "alembic_tenant_v2"
    shutil.copytree(settings.tenant_alembic_dir, target, ignore=shutil.ignore_patterns("__pycache__"))
    (target / "versions" / "0002_add_motto.py").write_text(
        '"""add motto"""\nfrom alembic import op\nimport sqlalchemy as sa\n'
        "revision = '0002'\ndown_revision = '0001'\nbranch_labels = None\ndepends_on = None\n\n"
        f"def upgrade():\n    {body}\n\ndef downgrade():\n    pass\n")
    return str(target)


def test_migration_runner_upgrades_every_school_and_reports_failures_per_school(ctx, reg_input, settings, tmp_path):
    for code in ("alpha", "beta", "gamma"):
        register_school(ctx, reg_input(code))
    with ctx.control() as db:
        regs = {s.code: db.get(SchoolDatabase, s.id) for s in db.scalars(select(School))}
    # make ONE school fail: its column already exists, so the new migration cannot add it
    t = create_engine(tenant_url(regs["beta"], ctx.box))
    with t.begin() as c:
        c.execute(text("ALTER TABLE school_settings ADD COLUMN motto varchar(200)"))
    t.dispose()

    ctx.settings.tenant_alembic_dir = _copy_with_new_revision(
        settings, tmp_path, "op.add_column('school_settings', sa.Column('motto', sa.String(200), nullable=True))")
    summary = run_tenant_migrations(ctx)
    assert summary["head"] == "0002" and summary["upgraded"] == 2
    assert [f["school"] for f in summary["failed"]] == ["beta"]
    with ctx.control() as db:
        runs = {db.get(School, r.school_id).code: r for r in db.scalars(select(MigrationRun))}
        assert runs["alpha"].status == "ok" and runs["alpha"].to_revision == "0002"
        assert runs["beta"].status == "failed" and "motto" in runs["beta"].error
        assert db.get(SchoolDatabase, regs["alpha"].school_id).schema_revision == "0002"
        assert db.get(SchoolDatabase, regs["beta"].school_id).schema_revision == "0001"
    # the failed school is retried by simply running again; fix its column first
    t = create_engine(tenant_url(regs["beta"], ctx.box))
    with t.begin() as c:
        c.execute(text("ALTER TABLE school_settings DROP COLUMN motto"))
    t.dispose()
    again = run_tenant_migrations(ctx)
    assert again["upgraded"] == 1 and again["up_to_date"] == 2 and again["failed"] == []


def test_two_migration_runs_cannot_overlap(ctx, reg_input):
    from sqlalchemy import text as sqltext
    from app.services.migrations import LOCK_KEY, MigrationInProgress
    with ctx.control_engine.connect() as holder:
        holder.execute(sqltext("SELECT pg_advisory_lock(:k)"), {"k": LOCK_KEY})
        with pytest.raises(MigrationInProgress):
            run_tenant_migrations(ctx)
        holder.execute(sqltext("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        holder.commit()


def test_usage_snapshots_come_from_each_school_into_the_control_plane(ctx, reg_input):
    register_school(ctx, reg_input("alpha")); register_school(ctx, reg_input("beta"))
    out = collect_usage(ctx)
    assert out["collected"] == 2 and out["failed"] == []
    from app.models.control import UsageSnapshot
    with ctx.control() as db:
        snaps = db.scalars(select(UsageSnapshot)).all()
    assert len(snaps) == 2 and all(s.user_count == 1 and s.schema_revision == "0001" for s in snaps)


def test_engine_cache_is_capped(ctx, reg_input):
    ctx.settings.tenant_max_engines = 2
    for code in ("alpha", "beta", "gamma"):
        register_school(ctx, reg_input(code))
    assert len(ctx.tenants._engines) <= 2


def test_migrations_match_models(ctx, settings):
    """If a model changes without a migration, this fails."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app.models.control import ControlBase
    engine = ctx.control_engine
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), ControlBase.metadata)
    assert diff == []
