# School Management SaaS – Phase 1 and 2 foundation

Multi-school SaaS for KG to Class 10. **Every school has its own PostgreSQL database**, plus one platform (control-plane) database.
This repository implements the decisions in the Phase 0 plan. Manual class-wise attendance, holidays, students, fees and the other
modules are **not built yet** (see Status).

## What exists

| Area | Built |
| --- | --- |
| Control plane | `schools`, plans (capacity set, **prices unset until the Super Admin sets them**), routing registry with encrypted per-school DB passwords, provisioning job log, migration run log, usage snapshots, platform admins and sessions, platform audit log |
| School database (one per school) | school settings, **school timing profiles**, users, roles, permissions, sessions, login attempts, audit log |
| Provisioning | Registration creates a dedicated DB role and database, revokes public access, runs migrations, seeds roles and the first administrator. Every step is recorded, can be repeated, and can be retried or cleaned up |
| Tenant routing | School identified by `X-School-Code` header or `code.base-domain` host; session is bound to that school; mismatched database is refused |
| Migrations | `python -m app.cli migrate-tenants` upgrades every school database, records success/failure per school, continues past failures, and refuses to run twice at once |
| Platform reporting | `collect-usage` copies summary numbers from each school into the control plane (no cross-database queries) |
| Registration | 4-step wizard. **The School timing step has no defaults**: working days, alternate Saturdays, start/end, one or two sessions, late grace, half-day rule, breaks, periods |
| Auth | argon2 passwords, server-side sessions in httpOnly cookies, CSRF header on every change, failed-login throttling (per email and per address), generic login errors |
| Permissions | Backend-enforced permission codes per role; only permissions for features that exist are defined |
| API/UI | FastAPI backend; React + TypeScript + Vite frontend: registration, school sign-in, school workspace (overview, timing, users, audit log), platform console (schools, migrations, plan prices) |

## Run the tests (no Docker needed)
```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q          # starts a real throw-away PostgreSQL via pgserver
```
## Run locally with Docker
```bash
cp .env.example .env         # fill in POSTGRES_PASSWORD and SAAS_SECRETS_KEY
docker compose up --build    # UI on http://localhost:8080
docker compose exec backend python -m app.cli create-platform-admin --email you@example.com
```
Without Docker: create a PostgreSQL role with CREATEDB and CREATEROLE, set the `SAAS_*` variables from `backend/app/config.py`,
run `python -m app.cli init-control`, then `uvicorn app.main:create_app --factory`, and in `frontend/` run `npm install && npm run dev`.

## Verification status (what was actually run)
* **Backend: 31 automated tests passed** against a real PostgreSQL 16: provisioning (database, role, migrations, seed), retry after failure,
  password scrubbing from error logs, one school's credentials refused on another's database, migration fan-out with one school failing,
  overlap lock, usage snapshots, engine cap, models-match-migrations (both databases), registration validation and abuse limit,
  cross-school sessions and cookies, permissions, CSRF, throttling, audit, platform API.
* **Frontend:** TypeScript build passes. A scripted headless-Chromium run against the real backend passed: registration wizard (with validation
  that blocks an empty timing step), sign-in (wrong password rejected), timing shown back, user creation, audit log, a teacher seeing only
  Overview and getting 403 from the backend on the users API, platform login, running migrations and saving a plan price.
* **Not verified:** the Docker files (no Docker in the build environment), HTTPS/subdomain routing, production password authentication on
  PostgreSQL (the test server trusts local connections, so database-level isolation was proven by privileges, not passwords),
  load and connection-count behaviour with many schools, and a visual review on real phones.

## Known gaps and decisions
* **Subscriptions are not active yet.** Registration leaves `subscription_status = pending_payment`. `SAAS_REQUIRE_ACTIVE_SUBSCRIPTION` defaults to
  false for development and the app refuses to start in production with it false. Payments, webhooks, capacity enforcement: Phase 9.
* Registration is open and only limited per address. Email verification or payment before provisioning is needed before launch.
* Database passwords are encrypted with `SAAS_SECRETS_KEY`; use a proper secrets manager in production.
* The provisioning role is a superuser in the sample setup; use a role with only CREATEDB/CREATEROLE in production.
* Login throttling uses each database (works across workers). A shared cache (Redis) arrives when the publishing queue does.
* Behind a proxy, the client address needs forwarded-header handling before the per-address limits are meaningful.
* Cross-school accounts are out of scope for Version 1.

## Status against the phase plan
Phase 1 (foundation, authentication, control plane): done as above. Phase 2 (registration and automated database provisioning): done, payment excluded.
Next: Phase 3 (school timing editing, holiday packs, manual class-wise attendance).
