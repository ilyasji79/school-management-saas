"""Operations commands:  python -m app.cli <command>"""
import argparse
import getpass
import json
import os
import sys

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.config import Settings
from app.context import AppContext
from app.db.control import to_sa_url
from app.services import alembic_runner
from app.services.migrations import MigrationInProgress, collect_usage, run_tenant_migrations
from app.services.platform_admins import create_platform_admin, seed_default_plans


def cmd_init_control(settings, args):
    url = make_url(to_sa_url(settings.control_database_url))
    with psycopg.connect(settings.pg_admin_url, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (url.database,)).fetchone():
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(url.database)))
            print(f"Created control database {url.database}")
    alembic_runner.upgrade_to_head(settings.control_alembic_dir, url)
    ctx = AppContext(settings)
    print(f"Control schema is at {alembic_runner.head_revision(settings.control_alembic_dir)}; "
          f"{seed_default_plans(ctx)} plan(s) added (prices are set by the Super Admin).")
    ctx.close()


def cmd_create_platform_admin(settings, args):
    password = os.environ.get("PLATFORM_ADMIN_PASSWORD") or getpass.getpass("Password: ")
    ctx = AppContext(settings)
    create_platform_admin(ctx, args.email, password)
    print("Platform administrator created.")
    ctx.close()


def cmd_migrate_tenants(settings, args):
    ctx = AppContext(settings)
    try:
        summary = run_tenant_migrations(ctx)
    except MigrationInProgress as exc:
        sys.exit(str(exc))
    print(json.dumps(summary, indent=2))
    ctx.close()
    if summary["failed"]:
        sys.exit(1)


def cmd_collect_usage(settings, args):
    ctx = AppContext(settings)
    print(json.dumps(collect_usage(ctx), indent=2))
    ctx.close()


def main():
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-control").set_defaults(fn=cmd_init_control)
    p = sub.add_parser("create-platform-admin"); p.add_argument("--email", required=True)
    p.set_defaults(fn=cmd_create_platform_admin)
    sub.add_parser("migrate-tenants").set_defaults(fn=cmd_migrate_tenants)
    sub.add_parser("collect-usage").set_defaults(fn=cmd_collect_usage)
    args = parser.parse_args()
    args.fn(Settings(), args)


if __name__ == "__main__":
    main()
