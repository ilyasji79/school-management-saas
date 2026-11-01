import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import platform, public, school
from app.config import Settings
from app.context import AppContext

log = logging.getLogger("schoolsaas")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    ctx = AppContext(settings)
    app = FastAPI(title="School Management SaaS", docs_url="/api/docs" if settings.env != "production" else None,
                  redoc_url=None, openapi_url="/api/openapi.json" if settings.env != "production" else None)
    app.state.ctx = ctx

    @app.on_event("shutdown")
    def _close():
        ctx.close()

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        problems = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": str(e["msg"]).removeprefix("Value error, ")}
                    for e in exc.errors()]
        return JSONResponse({"error": {"code": "validation_error", "message": "Please correct the highlighted fields.",
                                       "fields": problems}}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"error": {"code": "server_error", "message": "Something went wrong. It has been logged."}},
                            status_code=500)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(public.router)
    app.include_router(school.router)
    app.include_router(platform.router)
    return app
