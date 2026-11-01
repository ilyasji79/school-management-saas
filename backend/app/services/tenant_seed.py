"""Roles and permissions every school database starts with.  Idempotent: safe to run after every migration,
so new permissions introduced by later phases reach existing schools too.

Only permissions for features that exist today are listed.  Later phases add their own
(attendance.*, fees.*, social.* ...) together with the feature.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenant import Permission, Role, RolePermission

ROLES = {
    "school_admin": "School Owner / Administrator",
    "principal": "Principal",
    "teacher": "Teacher",
    "accountant": "Accountant",
    "attendance_operator": "Attendance Operator",
    "social_media_manager": "Social Media Manager",
    "parent": "Parent / Guardian",
    "student": "Student",
}

PERMISSIONS = ["users.read", "users.create", "users.manage_roles", "settings.read", "settings.update", "audit.read"]

ROLE_PERMISSIONS = {
    "school_admin": PERMISSIONS,
    "principal": ["users.read", "settings.read", "audit.read"],
}


def ensure_roles_permissions(db: Session) -> None:
    roles = {r.code: r for r in db.scalars(select(Role))}
    for code, name in ROLES.items():
        if code not in roles:
            roles[code] = Role(code=code, name=name)
            db.add(roles[code])
    perms = {p.code: p for p in db.scalars(select(Permission))}
    for code in PERMISSIONS:
        if code not in perms:
            perms[code] = Permission(code=code)
            db.add(perms[code])
    db.flush()
    existing = {(rp.role_id, rp.permission_id) for rp in db.scalars(select(RolePermission))}
    for role_code, codes in ROLE_PERMISSIONS.items():
        for pcode in codes:
            key = (roles[role_code].id, perms[pcode].id)
            if key not in existing:
                db.add(RolePermission(role_id=key[0], permission_id=key[1]))
    db.commit()
