"""Platform (control-plane) database models. Nothing school-internal lives here."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(timezone.utc)


class ControlBase(DeclarativeBase):
    pass


def uuid_pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Plan(ControlBase):
    __tablename__ = "plans"
    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    student_capacity: Mapped[int | None] = mapped_column(Integer)          # None = contracted per school
    price_minor: Mapped[int | None] = mapped_column(BigInteger)            # set by the Super Admin, never hard-coded
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    features: Mapped[dict] = mapped_column(JSONB, default=dict)


class School(ControlBase):
    __tablename__ = "schools"
    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(30), unique=True)             # sign-in identifier / subdomain
    name: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(60))
    board: Mapped[str] = mapped_column(String(40))
    timezone: Mapped[str] = mapped_column(String(60))
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"))
    status: Mapped[str] = mapped_column(String(20), default="provisioning")
    subscription_status: Mapped[str] = mapped_column(String(20), default="pending_payment")
    registered_ip: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SchoolDatabase(ControlBase):
    """Routing table: where each school's database is and how to reach it."""
    __tablename__ = "school_databases"
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schools.id"), primary_key=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int | None] = mapped_column(Integer)
    db_name: Mapped[str] = mapped_column(String(63), unique=True)
    db_role: Mapped[str] = mapped_column(String(63), unique=True)
    password_ciphertext: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="provisioning")
    schema_revision: Mapped[str | None] = mapped_column(String(64))
    next_job_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RegistrationData(ControlBase):
    """What the registrant entered. The admin password is stored only as a hash and removed after provisioning."""
    __tablename__ = "registration_data"
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schools.id"), primary_key=True)
    admin_name: Mapped[str] = mapped_column(String(120))
    admin_email: Mapped[str] = mapped_column(String(254))
    admin_password_hash: Mapped[str | None] = mapped_column(Text)
    timing_profile: Mapped[dict] = mapped_column(JSONB)                    # "school_timing_profiles as entered"


class ProvisioningJob(ControlBase):
    __tablename__ = "provisioning_jobs"
    id: Mapped[uuid.UUID] = uuid_pk()
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schools.id"), index=True)
    step: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))                        # running | done | failed
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MigrationRun(ControlBase):
    __tablename__ = "migration_runs"
    id: Mapped[uuid.UUID] = uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schools.id"), index=True)
    from_revision: Mapped[str | None] = mapped_column(String(64))
    to_revision: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))                        # ok | failed
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageSnapshot(ControlBase):
    """Platform reporting without cross-database queries: a job copies summary numbers here."""
    __tablename__ = "usage_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schools.id"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    user_count: Mapped[int] = mapped_column(Integer)
    schema_revision: Mapped[str | None] = mapped_column(String(64))


class PlatformAdmin(ControlBase):
    __tablename__ = "platform_admins"
    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PlatformSession(ControlBase):
    __tablename__ = "platform_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform_admins.id"))
    csrf: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PlatformLoginAttempt(ControlBase):
    __tablename__ = "platform_login_attempts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    ip: Mapped[str | None] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class PlatformAuditLog(ControlBase):
    __tablename__ = "platform_audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    admin_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("platform_admins.id"))
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
