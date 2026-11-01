from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from app.api.deps import client_ip, get_ctx
from app.context import AppContext
from app.models.control import Plan
from app.schemas import BOARDS, INDIAN_STATES_AND_UTS, RegistrationIn
from app.services.registration import RegistrationError, register_school

router = APIRouter()


@router.get("/api/health")
def health(ctx: AppContext = Depends(get_ctx)):
    try:
        with ctx.control() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        return JSONResponse({"status": "degraded"}, status_code=503)


@router.get("/api/meta")
def meta(ctx: AppContext = Depends(get_ctx)):
    """Options the registration form needs. Prices come from the database; nothing is hard-coded."""
    with ctx.control() as db:
        plans = db.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.student_capacity.nulls_last())).all()
    return {"states": INDIAN_STATES_AND_UTS, "boards": BOARDS,
            "plans": [{"code": p.code, "name": p.name, "student_capacity": p.student_capacity,
                       "price_minor": p.price_minor, "currency": p.currency} for p in plans]}


@router.post("/api/schools/register", status_code=201)
def register(payload: RegistrationIn, request: Request, ctx: AppContext = Depends(get_ctx)):
    try:
        result = register_school(ctx, payload, ip=client_ip(request))
    except RegistrationError as exc:
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)
    if result["status"] != "active":
        return JSONResponse({"error": {"code": "provisioning_failed",
                                       "message": "Your school was registered but its workspace could not be created. "
                                                  "Support has been notified; please contact us with this school code.",
                                       "school_code": result["code"]}}, status_code=500)
    return {"code": result["code"], "status": result["status"],
            "message": "School created. Sign in with the administrator email and password you entered."}
