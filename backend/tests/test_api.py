import copy
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models.control import Plan, SchoolDatabase, School
from app.services.platform_admins import create_platform_admin
from app.services.provisioning import drop_role_and_database
from tests.conftest import PASSWORD, registration


@pytest.fixture()
def api(settings):
    app = create_app(settings)
    ctx = app.state.ctx
    with ctx.control() as db:
        db.add_all([Plan(code="starter", name="Starter", student_capacity=250),
                    Plan(code="standard", name="Standard", student_capacity=1000)])
        db.commit()
    client = TestClient(app)
    client.ctx = ctx
    yield client
    with ctx.control() as db:
        names = [(r.db_name, r.db_role) for r in db.scalars(select(SchoolDatabase))]
    ctx.close()
    for n, r in names:
        drop_role_and_database(settings, n, r)


def new_client(api):
    return TestClient(api.app)


def register(api, code="alpha", **kw):
    r = api.post("/api/schools/register", json=registration(code, **kw))
    assert r.status_code == 201, r.text
    return r


def login(api, code, email=None, password=PASSWORD):
    c = new_client(api)
    r = c.post("/api/auth/login", json={"email": email or f"admin@{code}.example", "password": password},
               headers={"X-School-Code": code})
    return c, r


def signed_in(api, code="alpha"):
    register(api, code)
    c, r = login(api, code)
    assert r.status_code == 200, r.text
    c.headers.update({"X-School-Code": code, "X-CSRF-Token": r.json()["csrf"]})
    return c


# ----------------------------------------------------------------------------- registration
def test_register_then_sign_in_and_see_the_schools_own_data(api):
    c = signed_in(api, "alpha")
    me = c.get("/api/auth/me").json()
    assert me["school"] == {"code": "alpha", "name": "Alpha School", "timezone": "Asia/Kolkata",
                            "state": "Karnataka", "board": "CBSE"}
    assert me["roles"] == ["school_admin"] and "users.create" in me["permissions"]
    timing = c.get("/api/school/settings").json()["timing_profiles"][0]
    assert timing["working_days"] == [0, 1, 2, 3, 4, 5] and timing["saturdays_off"] == [2, 4]
    assert timing["start_time"] == "09:00" and timing["session_split_time"] == "12:30"
    assert timing["breaks"][0]["name"] == "Lunch"


def test_registration_rejects_bad_input_with_field_messages(api):
    def bad(mutate):
        body = registration("zeta"); mutate(body)
        r = api.post("/api/schools/register", json=body)
        assert r.status_code == 422, r.text
        return " ".join(f["message"] for f in r.json()["error"]["fields"])

    assert "after start" in bad(lambda b: b["timing"].update(end_time="08:00"))
    assert "overlap" in bad(lambda b: b["timing"].update(breaks=[{"name": "A", "start": "10:00", "end": "11:00"},
                                                                  {"name": "B", "start": "10:30", "end": "11:30"}]))
    assert "session_split_time" in bad(lambda b: b["timing"].update(session_mode="two", session_split_time=None))
    assert "Saturday" in bad(lambda b: b["timing"].update(working_days=[0, 1, 2], saturdays_off=[2]))
    assert "inside school hours" in bad(lambda b: b["timing"].update(breaks=[{"name": "Odd", "start": "06:00", "end": "07:00"}]))
    assert "10 characters" in bad(lambda b: b["admin"].update(password="short1A"))
    assert "state" in bad(lambda b: b["school"].update(state="Atlantis"))
    assert "reserved" in bad(lambda b: b["school"].update(code="admin"))
    assert "timezone" in bad(lambda b: b["school"].update(timezone="Mars/Base"))
    assert bad(lambda b: b["timing"].update(working_days=[]))                    # empty working days
    assert api.get("/api/platform/schools").status_code == 401                    # sanity: nothing leaked


def test_duplicate_school_code_and_unknown_plan(api):
    register(api, "alpha")
    r = api.post("/api/schools/register", json=registration("alpha", ))
    assert r.status_code == 409 and r.json()["error"]["code"] == "code_taken"
    body = registration("beta"); body["plan_code"] = "nope"
    assert api.post("/api/schools/register", json=body).status_code == 422


def test_registration_is_rate_limited_per_address(api):
    for code in ("one", "two", "three"):
        register(api, code)
    r = api.post("/api/schools/register", json=registration("four"))
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_registrations"


def test_meta_comes_from_the_database_and_prices_are_unset_until_the_super_admin_sets_them(api):
    meta = api.get("/api/meta").json()
    assert "Karnataka" in meta["states"] and "CBSE" in meta["boards"]
    plans = {p["code"]: p for p in meta["plans"]}
    assert plans["starter"]["student_capacity"] == 250 and plans["starter"]["price_minor"] is None


# ----------------------------------------------------------------------------- tenant isolation
def test_unknown_or_missing_school_is_404(api):
    assert api.post("/api/auth/login", json={"email": "a@b.co", "password": "x"}).status_code == 404
    assert api.post("/api/auth/login", json={"email": "a@b.co", "password": "x"},
                    headers={"X-School-Code": "ghost"}).status_code == 404


def test_a_session_from_one_school_is_useless_at_another(api):
    a = signed_in(api, "alpha")
    register(api, "beta")
    cookie = a.cookies.get("school_session")
    assert cookie.startswith("alpha.")
    # same browser cookie, pointed at beta
    r = new_client(api).get("/api/auth/me", headers={"X-School-Code": "beta"}, cookies={"school_session": cookie})
    assert r.status_code == 401
    # forged cookie that claims to be beta's but carries alpha's token
    forged = "beta." + cookie.split(".", 1)[1]
    r = new_client(api).get("/api/auth/me", headers={"X-School-Code": "beta"}, cookies={"school_session": forged})
    assert r.status_code == 401
    # alpha's admin credentials do not exist in beta
    _, r = login(api, "beta", email="admin@alpha.example")
    assert r.status_code == 401


def test_users_of_each_school_are_invisible_to_the_other(api):
    a, b = signed_in(api, "alpha"), signed_in(api, "beta")
    assert [u["email"] for u in a.get("/api/school/users").json()] == ["admin@alpha.example"]
    assert [u["email"] for u in b.get("/api/school/users").json()] == ["admin@beta.example"]


def test_registry_pointing_at_the_wrong_database_is_refused(api):
    register(api, "alpha"); register(api, "beta")
    with api.ctx.control() as db:
        a = db.scalars(select(SchoolDatabase).join(School).where(School.code == "alpha")).one()
        b = db.scalars(select(SchoolDatabase).join(School).where(School.code == "beta")).one()
        taken = (b.db_name, b.db_role, b.password_ciphertext)
        b.db_name, b.db_role = "parked_db", "parked_role"          # free the unique names (the constraint is real)
        db.flush()
        a.db_name, a.db_role, a.password_ciphertext = taken
        db.commit()
    api.ctx.tenants.dispose_all(); api.ctx.verified_schools.clear()
    r = new_client(api).post("/api/auth/login", json={"email": "admin@beta.example", "password": PASSWORD},
                             headers={"X-School-Code": "alpha"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "tenant_mismatch"


def test_suspended_school_is_locked_and_subscription_rule_is_enforced(api):
    register(api, "alpha")
    with api.ctx.control() as db:
        db.scalars(select(School).where(School.code == "alpha")).one().status = "suspended"; db.commit()
    assert login(api, "alpha")[1].status_code == 423
    with api.ctx.control() as db:
        s = db.scalars(select(School).where(School.code == "alpha")).one(); s.status = "active"; db.commit()
    assert login(api, "alpha")[1].status_code == 200
    api.ctx.settings.require_active_subscription = True
    assert login(api, "alpha")[1].status_code == 402                       # pending_payment
    with api.ctx.control() as db:
        db.scalars(select(School).where(School.code == "alpha")).one().subscription_status = "active"; db.commit()
    assert login(api, "alpha")[1].status_code == 200


# ----------------------------------------------------------------------------- authentication and permissions
def test_login_errors_are_generic_and_throttled(api):
    register(api, "alpha")
    wrong, unknown = login(api, "alpha", password="Wrong-Password1")[1], login(api, "alpha", email="nobody@x.example")[1]
    assert wrong.status_code == unknown.status_code == 401 and wrong.json() == unknown.json()
    for _ in range(api.ctx.settings.login_max_failures):
        login(api, "alpha", password="Wrong-Password1")
    assert login(api, "alpha")[1].status_code == 429                         # even the right password is refused


def test_session_cookie_flags_and_logout(api):
    register(api, "alpha")
    c, r = login(api, "alpha")
    header = r.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header
    c.headers.update({"X-School-Code": "alpha", "X-CSRF-Token": r.json()["csrf"]})
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_csrf_is_required_for_changes(api):
    c = signed_in(api, "alpha")
    body = {"email": "t@alpha.example", "full_name": "Tara Teacher", "password": PASSWORD, "roles": ["teacher"]}
    no_token = {k: v for k, v in c.headers.items() if k.lower() != "x-csrf-token"}
    r = new_client(api).post("/api/school/users", json=body, headers={"X-School-Code": "alpha"},
                             cookies={"school_session": c.cookies.get("school_session")})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    assert c.post("/api/school/users", json=body).status_code == 201


def test_permissions_are_enforced_by_the_backend(api):
    admin = signed_in(api, "alpha")
    for email, role in (("p@alpha.example", "principal"), ("t@alpha.example", "teacher")):
        r = admin.post("/api/school/users", json={"email": email, "full_name": role.title() + " User",
                                                   "password": PASSWORD, "roles": [role]})
        assert r.status_code == 201, r.text
    assert admin.post("/api/school/users", json={"email": "p@alpha.example", "full_name": "Dup Dup",
                                                  "password": PASSWORD, "roles": ["teacher"]}).status_code == 409
    assert admin.post("/api/school/users", json={"email": "x@alpha.example", "full_name": "Bad Role",
                                                  "password": PASSWORD, "roles": ["wizard"]}).status_code == 422

    def as_user(email):
        c, r = login(api, "alpha", email=email)
        assert r.status_code == 200
        c.headers.update({"X-School-Code": "alpha", "X-CSRF-Token": r.json()["csrf"]})
        return c

    principal, teacher = as_user("p@alpha.example"), as_user("t@alpha.example")
    assert principal.get("/api/school/users").status_code == 200
    assert principal.get("/api/school/audit-logs").status_code == 200
    forbidden = {"email": "n@alpha.example", "full_name": "New Person", "password": PASSWORD, "roles": ["teacher"]}
    assert principal.post("/api/school/users", json=forbidden).status_code == 403
    assert teacher.get("/api/school/users").status_code == 403
    assert teacher.get("/api/school/settings").status_code == 403
    assert teacher.post("/api/school/users", json=forbidden).status_code == 403
    assert new_client(api).get("/api/school/users", headers={"X-School-Code": "alpha"}).status_code == 401


def test_changes_are_audited_and_hashes_never_leave_the_server(api):
    c = signed_in(api, "alpha")
    c.post("/api/school/users", json={"email": "t@alpha.example", "full_name": "Tara Teacher", "password": PASSWORD, "roles": ["teacher"]})
    logs = c.get("/api/school/audit-logs").json()["items"]
    assert {"school.provisioned", "auth.login", "user.create"} <= {l["action"] for l in logs}
    everything = c.get("/api/school/users").text + c.get("/api/auth/me").text + c.get("/api/school/settings").text
    assert "argon2" not in everything and "password" not in everything.lower()


# ----------------------------------------------------------------------------- platform administrators
def platform_client(api):
    create_platform_admin(api.ctx, "owner@platform.example", PASSWORD)
    c = new_client(api)
    r = c.post("/api/platform/auth/login", json={"email": "owner@platform.example", "password": PASSWORD})
    assert r.status_code == 200
    c.headers.update({"X-CSRF-Token": r.json()["csrf"]})
    return c


def test_platform_api_needs_a_platform_session_and_school_sessions_do_not_count(api):
    school = signed_in(api, "alpha")
    assert api.get("/api/platform/schools").status_code == 401
    assert school.get("/api/platform/schools").status_code == 401              # a school admin is not a platform admin
    assert api.post("/api/platform/migrations/run").status_code in (401, 403)


def test_platform_admin_sees_schools_runs_migrations_and_sets_prices(api):
    register(api, "alpha"); register(api, "beta")
    p = platform_client(api)
    schools = {s["code"]: s for s in p.get("/api/platform/schools").json()}
    assert schools["alpha"]["status"] == "active" and schools["alpha"]["schema_revision"] == "0001"
    run = p.post("/api/platform/migrations/run").json()
    assert run["up_to_date"] == 2 and run["failed"] == []
    assert p.post("/api/platform/usage/collect").json()["collected"] == 2
    usage = {u["school"]: u["user_count"] for u in p.get("/api/platform/usage").json()}
    assert usage == {"alpha": 1, "beta": 1}
    assert p.put("/api/platform/plans/starter", json={"price_minor": 499900}).status_code == 200
    meta = {x["code"]: x for x in api.get("/api/meta").json()["plans"]}
    assert meta["starter"]["price_minor"] == 499900
    detail = p.get(f"/api/platform/schools/{schools['alpha']['id']}").json()
    assert [j["step"] for j in detail["provisioning"]] == ["role_and_database", "migrate", "seed"]
    no_csrf = new_client(api)
    no_csrf.cookies.update(p.cookies)
    assert no_csrf.put("/api/platform/plans/starter", json={"price_minor": 1}).status_code == 403


def test_platform_retry_only_for_failed_schools(api, monkeypatch):
    from app.services import provisioning
    register(api, "alpha")
    p = platform_client(api)
    sid = p.get("/api/platform/schools").json()[0]["id"]
    assert p.post(f"/api/platform/schools/{sid}/retry-provisioning").status_code == 409


def test_tenant_schema_matches_models(api):
    register(api, "alpha")
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app.models.tenant import TenantBase
    with api.ctx.control() as db:
        reg = db.scalars(select(SchoolDatabase)).one()
    with api.ctx.tenants.engine(reg).connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), TenantBase.metadata) == []
