from sqlalchemy import select

from app.context import AppContext
from app.models.control import Plan, PlatformAdmin
from app.security import hash_password, validate_password_policy

DEFAULT_PLANS = [  # capacities from the product plan; prices are deliberately left unset for the Super Admin
    ("starter", "Starter", 250), ("standard", "Standard", 1000), ("enterprise", "Enterprise", None)]


def create_platform_admin(ctx: AppContext, email: str, password: str) -> None:
    validate_password_policy(password)
    email = email.strip().lower()
    with ctx.control() as db:
        if db.scalars(select(PlatformAdmin).where(PlatformAdmin.email == email)).first():
            raise ValueError("That platform administrator already exists.")
        db.add(PlatformAdmin(email=email, password_hash=hash_password(password)))
        db.commit()


def seed_default_plans(ctx: AppContext) -> int:
    added = 0
    with ctx.control() as db:
        for code, name, cap in DEFAULT_PLANS:
            if not db.scalars(select(Plan).where(Plan.code == code)).first():
                db.add(Plan(code=code, name=name, student_capacity=cap))
                added += 1
        db.commit()
    return added
