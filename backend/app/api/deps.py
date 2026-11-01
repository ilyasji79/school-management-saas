import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.context import AppContext
from app.models.control import PlatformAdmin, PlatformSession, School, SchoolDatabase
from app.models.tenant import Permission, Role, RolePermission, SchoolSettings, Session, User, UserRole
from app.schemas import CODE_RE
from app.security import hash_token

log = logging.getLogger(__name__)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SCHOOL_COOKIE = "school_session"
PLATFORM_COOKIE = "platform_session"


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def fail(status: int, code: str, message: str):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


# ----------------------------------------------------------------------------- which school is this request for?
@dataclass
class SchoolContext:
    school: School
    reg: SchoolDatabase


def _code_from_request(request: Request, base_domain: str | None) -> str:
    code = request.headers.get("x-school-code", "").strip().lower()
    if not code and base_domain:
        host = request.headers.get("host", "").split(":")[0].lower()
        if host.endswith("." + base_domain):
            code = host[: -(len(base_domain) + 1)]
    return code


def resolve_school(request: Request, ctx: AppContext = Depends(get_ctx)) -> SchoolContext:
    code = _code_from_request(request, ctx.settings.base_domain)
    if not code or not CODE_RE.match(code):
        raise fail(404, "school_not_found", "School not found.")
    with ctx.control() as db:
        school = db.scalars(select(School).where(School.code == code)).first()
        reg = db.get(SchoolDatabase, school.id) if school else None
    if not school or not reg:
        raise fail(404, "school_not_found", "School not found.")
    if school.status != "active" or reg.status != "ready":
        raise fail(423, "school_unavailable", "This school is not available right now.")
    if ctx.settings.require_active_subscription and school.subscription_status not in ("active", "grace"):
        raise fail(402, "subscription_inactive", "This school's subscription is not active.")
    return SchoolContext(school, reg)


def tenant_db(sc: SchoolContext = Depends(resolve_school), ctx: AppContext = Depends(get_ctx)):
    """A session on THIS school's database only.  Nothing in the request can choose another database."""
    db = ctx.tenants.session(sc.reg)
    try:
        key = str(sc.school.id)
        if key not in ctx.verified_schools:
            row = db.get(SchoolSettings, 1)
            if row is None or row.school_uuid != sc.school.id:
                log.critical("Tenant database mismatch for school %s", sc.school.code)
                raise fail(500, "tenant_mismatch", "Configuration error. Contact support.")
            ctx.verified_schools.add(key)
        yield db
    finally:
        db.close()


# ----------------------------------------------------------------------------- who is signed in (school users)
@dataclass
class AuthUser:
    user: User
    session: Session
    roles: list[str]
    permissions: set[str]


def current_user(request: Request, sc: SchoolContext = Depends(resolve_school),
                 db: DbSession = Depends(tenant_db)) -> AuthUser:
    cookie = request.cookies.get(SCHOOL_COOKIE, "")
    code, _, token = cookie.partition(".")
    if not token or code != sc.school.code:           # a session from another school is useless here
        raise fail(401, "unauthorized", "Please sign in.")
    sess = db.get(Session, hash_token(token))
    if sess is None or sess.expires_at <= datetime.now(timezone.utc):
        raise fail(401, "unauthorized", "Please sign in.")
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise fail(401, "unauthorized", "Please sign in.")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not hmac.compare_digest(sent, sess.csrf):
            raise fail(403, "csrf_failed", "Security token missing or expired. Reload and try again.")
    roles = list(db.scalars(select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user.id)))
    perms = set(db.scalars(select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id)
                           .join(UserRole, UserRole.role_id == RolePermission.role_id).where(UserRole.user_id == user.id)))
    return AuthUser(user, sess, roles, perms)


def require(*needed: str):
    """Server-side permission check; hiding a button in the UI is never the control."""
    def checker(auth: AuthUser = Depends(current_user)) -> AuthUser:
        missing = [p for p in needed if p not in auth.permissions]
        if missing:
            raise fail(403, "forbidden", "You do not have permission to do that.")
        return auth
    return checker


# ----------------------------------------------------------------------------- platform administrators
@dataclass
class PlatformAuth:
    admin: PlatformAdmin
    session: PlatformSession


def current_platform_admin(request: Request, ctx: AppContext = Depends(get_ctx)) -> PlatformAuth:
    token = request.cookies.get(PLATFORM_COOKIE, "")
    with ctx.control() as db:
        sess = db.get(PlatformSession, hash_token(token)) if token else None
        admin = db.get(PlatformAdmin, sess.admin_id) if sess else None
    if not sess or sess.expires_at <= datetime.now(timezone.utc) or not admin or not admin.is_active:
        raise fail(401, "unauthorized", "Please sign in.")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not hmac.compare_digest(sent, sess.csrf):
            raise fail(403, "csrf_failed", "Security token missing or expired. Reload and try again.")
    return PlatformAuth(admin, sess)
