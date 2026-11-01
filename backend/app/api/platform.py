"""Platform Super Admin API. Reads and writes the control plane only."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from app.api.deps import (PLATFORM_COOKIE, PlatformAuth, client_ip, current_platform_admin, fail, get_ctx)
from app.context import AppContext
from app.models.control import (MigrationRun, Plan, PlatformAdmin, PlatformAuditLog, PlatformLoginAttempt,
                                PlatformSession, ProvisioningJob, School, SchoolDatabase, UsageSnapshot)
from app.security import burn_verify, hash_token, new_csrf, new_token, verify_password
from app.services.migrations import MigrationInProgress, collect_usage, run_tenant_migrations
from app.services.provisioning import ProvisioningFailed, provision_school

router = APIRouter(prefix="/api/platform")


def audit(db, auth: PlatformAuth, action, entity, entity_id=None, **details):
    db.add(PlatformAuditLog(admin_id=auth.admin.id, action=action, entity=entity,
                            entity_id=str(entity_id) if entity_id else None, details=details))


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


@router.post("/auth/login")
def login(payload: LoginIn, request: Request, response: Response, ctx: AppContext = Depends(get_ctx)):
    s, email, ip = ctx.settings, str(payload.email).lower(), client_ip(request)
    with ctx.control() as db:
        since = datetime.now(timezone.utc) - timedelta(minutes=s.login_window_minutes)
        fails = db.scalar(select(func.count()).select_from(PlatformLoginAttempt).where(
            PlatformLoginAttempt.email == email, PlatformLoginAttempt.success.is_(False),
            PlatformLoginAttempt.created_at >= since))
        if fails >= s.login_max_failures:
            raise fail(429, "too_many_attempts", "Too many failed attempts. Please wait and try again.")
        admin = db.scalars(select(PlatformAdmin).where(PlatformAdmin.email == email)).first()
        if admin is None:
            burn_verify(payload.password)
            ok = False
        else:
            ok = verify_password(admin.password_hash, payload.password) and admin.is_active
        db.add(PlatformLoginAttempt(email=email, ip=ip, success=ok))
        if not ok:
            db.commit()
            raise fail(401, "invalid_credentials", "Incorrect email or password.")
        token, token_hash = new_token()
        csrf = new_csrf()
        db.add(PlatformSession(token_hash=token_hash, admin_id=admin.id, csrf=csrf,
                               expires_at=datetime.now(timezone.utc) + timedelta(minutes=s.session_ttl_minutes)))
        db.commit()
    response.set_cookie(PLATFORM_COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="lax",
                        max_age=s.session_ttl_minutes * 60, path="/")
    return {"csrf": csrf, "email": email}


@router.post("/auth/logout")
def logout(response: Response, auth: PlatformAuth = Depends(current_platform_admin), ctx: AppContext = Depends(get_ctx)):
    with ctx.control() as db:
        sess = db.get(PlatformSession, auth.session.token_hash)
        if sess:
            db.delete(sess)
            db.commit()
    response.delete_cookie(PLATFORM_COOKIE, path="/")
    return {"logged_out": True}


@router.get("/auth/me")
def me(auth: PlatformAuth = Depends(current_platform_admin)):
    return {"email": auth.admin.email, "csrf": auth.session.csrf}


@router.get("/schools")
def schools(auth: PlatformAuth = Depends(current_platform_admin), ctx: AppContext = Depends(get_ctx)):
    with ctx.control() as db:
        rows = db.execute(select(School, Plan.code, SchoolDatabase.schema_revision, SchoolDatabase.status)
                          .join(Plan, Plan.id == School.plan_id).outerjoin(SchoolDatabase, SchoolDatabase.school_id == School.id)
                          .order_by(School.created_at.desc())).all()
        out = []
        for school, plan, rev, dbstatus in rows:
            last = db.scalars(select(MigrationRun).where(MigrationRun.school_id == school.id)
                              .order_by(MigrationRun.started_at.desc()).limit(1)).first()
            out.append({"id": str(school.id), "code": school.code, "name": school.name, "state": school.state,
                        "board": school.board, "plan": plan, "status": school.status,
                        "subscription_status": school.subscription_status, "database_status": dbstatus,
                        "schema_revision": rev, "last_migration": last.status if last else None,
                        "created_at": school.created_at.isoformat()})
    return out


@router.get("/schools/{school_id}")
def school_detail(school_id: str, auth: PlatformAuth = Depends(current_platform_admin), ctx: AppContext = Depends(get_ctx)):
    with ctx.control() as db:
        school = db.get(School, school_id)
        if not school:
            raise fail(404, "not_found", "School not found.")
        jobs = db.scalars(select(ProvisioningJob).where(ProvisioningJob.school_id == school.id).order_by(ProvisioningJob.started_at)).all()
        runs = db.scalars(select(MigrationRun).where(MigrationRun.school_id == school.id).order_by(MigrationRun.started_at.desc()).limit(20)).all()
        return {"id": str(school.id), "code": school.code, "name": school.name, "status": school.status,
                "provisioning": [{"step": j.step, "status": j.status, "error": j.error} for j in jobs],
                "migrations": [{"from": r.from_revision, "to": r.to_revision, "status": r.status, "error": r.error,
                                "started_at": r.started_at.isoformat()} for r in runs]}


@router.post("/schools/{school_id}/retry-provisioning")
def retry(school_id: str, auth: PlatformAuth = Depends(current_platform_admin), ctx: AppContext = Depends(get_ctx)):
    with ctx.control() as db:
        school = db.get(School, school_id)
        if not school:
            raise fail(404, "not_found", "School not found.")
        if school.status != "provision_failed":
            raise fail(409, "not_failed", "Only a school whose provisioning failed can be retried.")
        sid = school.id
        audit(db, auth, "school.retry_provisioning", "school", sid)
        db.commit()
    try:
        provision_school(ctx, sid)
    except ProvisioningFailed as exc:
        raise fail(500, "provisioning_failed", f"Provisioning failed at step '{exc.step}'. See the school's job log.")
    return {"status": "active"}


@router.post("/migrations/run")
def migrate(auth: PlatformAuth = Depends(current_platform_admin), ctx: AppContext = Depends(get_ctx)):
    try:
        summary = run_tenant_migrations(ctx)
    except MigrationInProgress:
        raise fail(409, "migration_in_progress", "A migration run is already in progress.")
    with ctx.control() as db:
        audit(db, auth, "migrations.run", "platform", None, upgraded=summary["upgraded"], failed=len(summary["failed"]))
        db.commit()
    return summary


@router.post("/usage/collect")
def usage_collect(auth: PlatformAuth = Depends(current_platform_admin), ctx: AppContext = Depends(get_ctx)):
    return collect_usage(ctx)


@router.get("/usage")
def usage(auth: PlatformAuth = Depends(current_platform_admin), ctx: AppContext = Depends(get_ctx)):
    with ctx.control() as db:
        latest = select(UsageSnapshot.school_id, func.max(UsageSnapshot.taken_at).label("t")).group_by(UsageSnapshot.school_id).subquery()
        rows = db.execute(select(UsageSnapshot, School.code).join(latest, (latest.c.school_id == UsageSnapshot.school_id) &
                          (latest.c.t == UsageSnapshot.taken_at)).join(School, School.id == UsageSnapshot.school_id)).all()
        return [{"school": code, "user_count": u.user_count, "schema_revision": u.schema_revision,
                 "taken_at": u.taken_at.isoformat()} for u, code in rows]


class PlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    student_capacity: int | None = Field(default=None, ge=1, le=100000)
    price_minor: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


@router.get("/plans")
def plans(auth: PlatformAuth = Depends(current_platform_admin), ctx: AppContext = Depends(get_ctx)):
    with ctx.control() as db:
        return [{"code": p.code, "name": p.name, "student_capacity": p.student_capacity, "price_minor": p.price_minor,
                 "currency": p.currency, "is_active": p.is_active} for p in db.scalars(select(Plan).order_by(Plan.student_capacity.nulls_last()))]


@router.put("/plans/{code}")
def update_plan(code: str, payload: PlanUpdate, auth: PlatformAuth = Depends(current_platform_admin),
                ctx: AppContext = Depends(get_ctx)):
    changes = payload.model_dump(exclude_unset=True)
    with ctx.control() as db:
        plan = db.scalars(select(Plan).where(Plan.code == code)).first()
        if not plan:
            raise fail(404, "not_found", "Plan not found.")
        for k, v in changes.items():
            setattr(plan, k, v)
        audit(db, auth, "plan.update", "plan", code, **changes)
        db.commit()
    return {"updated": sorted(changes)}
