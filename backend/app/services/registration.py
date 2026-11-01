import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.context import AppContext
from app.models.control import Plan, RegistrationData, School
from app.schemas import RegistrationIn
from app.security import hash_password
from app.services.provisioning import ProvisioningFailed, provision_school

log = logging.getLogger(__name__)


class RegistrationError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def register_school(ctx: AppContext, data: RegistrationIn, ip: str | None = None) -> dict:
    """Create the platform records, then provision the school's own database."""
    with ctx.control() as db:
        if ip:
            since = datetime.now(timezone.utc) - timedelta(hours=1)
            recent = db.scalar(select(func.count()).select_from(School).where(School.registered_ip == ip, School.created_at >= since))
            if recent >= ctx.settings.registration_max_per_ip_per_hour:
                raise RegistrationError("too_many_registrations", "Too many registrations from this address. Try again later.", 429)
        plan = db.scalars(select(Plan).where(Plan.code == data.plan_code, Plan.is_active.is_(True))).first()
        if plan is None:
            raise RegistrationError("unknown_plan", "That plan is not available.", 422)
        school = School(code=data.school.code, name=data.school.name, state=data.school.state, board=data.school.board,
                        timezone=data.school.timezone, plan_id=plan.id, status="provisioning", registered_ip=ip)
        db.add(school)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise RegistrationError("code_taken", "That school code is already in use.", 409)
        db.add(RegistrationData(school_id=school.id, admin_name=data.admin.full_name, admin_email=str(data.admin.email).lower(),
                                admin_password_hash=hash_password(data.admin.password),
                                timing_profile=data.timing.model_dump(mode="json")))
        db.commit()
        school_id, code = school.id, school.code
    try:
        provision_school(ctx, school_id)
        status = "active"
    except ProvisioningFailed as exc:
        log.error("Registration provisioning failed for %s at step %s", code, exc.step)
        status = "provision_failed"
    return {"school_id": str(school_id), "code": code, "status": status}
