"""Everything a school's own users can do. Every route runs on that school's database only."""
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.api.deps import (SCHOOL_COOKIE, AuthUser, SchoolContext, client_ip, current_user, fail, get_ctx, require,
                          resolve_school, tenant_db)
from app.context import AppContext
from app.models.tenant import (AuditLog, LoginAttempt, Role, SchoolSettings, Session, TimingProfile, User, UserRole)
from app.security import (burn_verify, hash_password, hash_token, new_csrf, new_token, validate_password_policy,
                          verify_password)

router = APIRouter(prefix="/api")


def audit(db: DbSession, auth: AuthUser | None, request: Request, action: str, entity: str, entity_id=None, **details):
    db.add(AuditLog(user_id=auth.user.id if auth else None, action=action, entity=entity,
                    entity_id=str(entity_id) if entity_id else None, details=details, ip=client_ip(request)))


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


@router.post("/auth/login")
def login(payload: LoginIn, request: Request, response: Response, sc: SchoolContext = Depends(resolve_school),
          db: DbSession = Depends(tenant_db), ctx: AppContext = Depends(get_ctx)):
    s = ctx.settings
    email, ip = str(payload.email).lower(), client_ip(request)
    since = datetime.now(timezone.utc) - timedelta(minutes=s.login_window_minutes)
    by_email = db.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.email == email, LoginAttempt.success.is_(False), LoginAttempt.created_at >= since))
    by_ip = db.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.ip == ip, LoginAttempt.success.is_(False), LoginAttempt.created_at >= since)) if ip else 0
    if by_email >= s.login_max_failures or by_ip >= s.login_max_failures * 4:
        raise fail(429, "too_many_attempts", "Too many failed attempts. Please wait and try again.")
    user = db.scalars(select(User).where(User.email == email)).first()
    if user is None:
        burn_verify(payload.password)
        ok = False
    else:
        ok = verify_password(user.password_hash, payload.password) and user.is_active
    db.add(LoginAttempt(email=email, ip=ip, success=ok))
    if not ok:
        db.commit()
        raise fail(401, "invalid_credentials", "Incorrect email or password.")
    token, token_hash = new_token()
    csrf = new_csrf()
    db.add(Session(token_hash=token_hash, user_id=user.id, csrf=csrf, ip=ip,
                   expires_at=datetime.now(timezone.utc) + timedelta(minutes=s.session_ttl_minutes)))
    user.last_login_at = datetime.now(timezone.utc)
    db.add(AuditLog(user_id=user.id, action="auth.login", entity="user", entity_id=str(user.id), details={}, ip=ip))
    db.commit()
    response.set_cookie(SCHOOL_COOKIE, f"{sc.school.code}.{token}", httponly=True, secure=s.cookie_secure,
                        samesite="lax", max_age=s.session_ttl_minutes * 60, path="/")
    return {"csrf": csrf, "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name}}


@router.post("/auth/logout")
def logout(response: Response, auth: AuthUser = Depends(current_user), db: DbSession = Depends(tenant_db)):
    db.delete(auth.session)
    db.commit()
    response.delete_cookie(SCHOOL_COOKIE, path="/")
    return {"logged_out": True}


@router.get("/auth/me")
def me(auth: AuthUser = Depends(current_user), sc: SchoolContext = Depends(resolve_school),
       db: DbSession = Depends(tenant_db)):
    settings = db.get(SchoolSettings, 1)
    return {"user": {"id": str(auth.user.id), "email": auth.user.email, "full_name": auth.user.full_name},
            "roles": sorted(auth.roles), "permissions": sorted(auth.permissions), "csrf": auth.session.csrf,
            "school": {"code": sc.school.code, "name": settings.name, "timezone": settings.timezone,
                       "state": settings.state, "board": settings.board}}


def timing_json(p: TimingProfile) -> dict:
    return {"id": p.id, "name": p.name, "class_group": p.class_group, "effective_from": p.effective_from.isoformat(),
            "effective_to": p.effective_to.isoformat() if p.effective_to else None, "working_days": p.working_days,
            "saturdays_off": p.saturdays_off, "start_time": p.start_time.strftime("%H:%M"),
            "end_time": p.end_time.strftime("%H:%M"), "session_mode": p.session_mode,
            "session_split_time": p.session_split_time.strftime("%H:%M") if p.session_split_time else None,
            "late_grace_minutes": p.late_grace_minutes,
            "half_day_after": p.half_day_after.strftime("%H:%M") if p.half_day_after else None,
            "breaks": p.breaks, "periods": p.periods}


@router.get("/school/settings")
def school_settings(auth: AuthUser = Depends(require("settings.read")), db: DbSession = Depends(tenant_db)):
    s = db.get(SchoolSettings, 1)
    return {"name": s.name, "state": s.state, "board": s.board, "timezone": s.timezone,
            "timing_profiles": [timing_json(p) for p in db.scalars(select(TimingProfile).order_by(TimingProfile.id))]}


class UserIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    password: str
    roles: list[str] = Field(min_length=1, max_length=5)

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        validate_password_policy(v)
        return v


@router.get("/school/users")
def list_users(auth: AuthUser = Depends(require("users.read")), db: DbSession = Depends(tenant_db)):
    out = []
    for u in db.scalars(select(User).order_by(User.full_name)):
        roles = list(db.scalars(select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == u.id)))
        out.append({"id": str(u.id), "email": u.email, "full_name": u.full_name, "is_active": u.is_active, "roles": roles})
    return out


@router.post("/school/users", status_code=201)
def create_user(payload: UserIn, request: Request,
                auth: AuthUser = Depends(require("users.create", "users.manage_roles")),
                db: DbSession = Depends(tenant_db)):
    roles = list(db.scalars(select(Role).where(Role.code.in_(payload.roles))))
    if len(roles) != len(set(payload.roles)):
        raise fail(422, "unknown_role", "One of the roles does not exist.")
    user = User(email=str(payload.email).lower(), full_name=payload.full_name, password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise fail(409, "email_taken", "A user with that email already exists.")
    for r in roles:
        db.add(UserRole(user_id=user.id, role_id=r.id))
    audit(db, auth, request, "user.create", "user", user.id, roles=sorted(payload.roles))
    db.commit()
    return {"id": str(user.id), "email": user.email, "roles": sorted(payload.roles)}


@router.get("/school/audit-logs")
def audit_logs(page: int = 1, per_page: int = 25, auth: AuthUser = Depends(require("audit.read")),
               db: DbSession = Depends(tenant_db)):
    per_page = max(1, min(per_page, 100)); page = max(1, page)
    total = db.scalar(select(func.count()).select_from(AuditLog))
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(per_page).offset((page - 1) * per_page))
    return {"total": total, "page": page, "per_page": per_page,
            "items": [{"id": r.id, "action": r.action, "entity": r.entity, "entity_id": r.entity_id,
                       "details": r.details, "user_id": str(r.user_id) if r.user_id else None,
                       "created_at": r.created_at.isoformat()} for r in rows]}
