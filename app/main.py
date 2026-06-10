# ── Paddle/oneDNN flags — MUST be first, before any paddle import ──────────────
import os as _os
_os.environ.setdefault("FLAGS_use_mkldnn", "0")
_os.environ.setdefault("FLAGS_enable_pir_api", "0")
_os.environ.setdefault("OMP_NUM_THREADS", "1")
_os.environ.setdefault("PADDLE_CPP_LOGLEVEL", "3")
# ──────────────────────────────────────────────────────────────────────────────

import sys, logging, uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse
from loguru import logger
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.enums import LogEventType, LogLevel
from app.services.audit_service import write_audit_log

settings = get_settings()


def _cors_origins() -> list[str]:
    value = (settings.CORS_ALLOWED_ORIGINS or "").strip()
    if value == "*":
        return ["*"]
    return [origin.strip() for origin in value.split(",") if origin.strip()]


def _setup_logging() -> None:
    logger.remove()
    logger.add(sys.stdout, level=settings.LOG_LEVEL, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <cyan>{name}</cyan> — <level>{message}</level>")
    class _I(logging.Handler):
        def emit(self, r):
            try: lvl = logger.level(r.levelname).name
            except ValueError: lvl = r.levelno
            logger.opt(depth=6, exception=r.exc_info).log(lvl, r.getMessage())
    logging.basicConfig(handlers=[_I()], level=0, force=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    for p in (settings.upload_path, settings.output_path, settings.templates_path):
        p.mkdir(parents=True, exist_ok=True)
    logger.info(f"Starting {settings.APP_NAME!r} [{settings.APP_ENV}]")
    try:
        from app.services.ocr_service import get_engine
        engine = await get_engine()
        logger.info(f"OCR engine preloaded during startup: {engine.name}")
    except Exception as exc:
        logger.exception(f"OCR engine preload failed: {exc}")
    yield
    logger.info("Shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="OCR Platform — Form Fill Engine",
        version="1.0.0",
        description=(
            "## OCR Platform — Complete Pipeline\n\n"
            "**DB models:** users · organisations · uploaded_files · file_pages · ocr_jobs · "
            "job_events · extracted_data · extraction_templates · audit_logs · system_logs · form_fill_jobs\n\n"
            "**Form-fill flow:**\n"
            "1. `POST /api/v1/auth/login` → JWT\n"
            "2. `POST /api/v1/fill/` with `extracted_data_id` + `export_format` → FillResult\n"
            "3. `GET  /api/v1/fill/jobs/{id}/download` → file\n\n"
            "**Formats:** `pdf` · `excel` · `json`"
        ),
        docs_url="/docs", redoc_url="/redoc",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def _req_id(request: Request, call_next):
        response = await call_next(request)
        if response.status_code in (401, 403) and not request.url.path.startswith(f"{settings.API_V1_PREFIX}/auth/"):
            async with AsyncSessionLocal() as db:
                await write_audit_log(
                    db,
                    action="auth.unauthorized_access",
                    event_type=LogEventType.AUTH,
                    status="failure",
                    level=LogLevel.WARNING,
                    resource_type="http_request",
                    resource_id=str(request.url.path),
                    message="Unauthorized access",
                    metadata={"method": request.method, "status_code": response.status_code},
                    request=request,
                )
                await db.commit()
        response.headers["X-Request-ID"] = str(uuid.uuid4())[:8]
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(CORSMiddleware, allow_origins=_cors_origins(), allow_methods=["*"], allow_headers=["*"], expose_headers=["Content-Disposition"])

    @app.exception_handler(RequestValidationError)
    async def _val(request: Request, exc: RequestValidationError):
        return ORJSONResponse(status_code=422, content={"error": "Validation Error", "detail": exc.errors()})

    @app.exception_handler(Exception)
    async def _err(request: Request, exc: Exception):
        logger.exception(f"Unhandled: {exc}")
        async with AsyncSessionLocal() as db:
            await write_audit_log(
                db,
                action="system.error",
                event_type=LogEventType.SYSTEM,
                status="failure",
                level=LogLevel.ERROR,
                resource_type="http_request",
                resource_id=str(request.url.path),
                message="Unhandled system error",
                metadata={"method": request.method, "error": str(exc)},
                error=str(exc),
                request=request,
            )
            await db.commit()
        return ORJSONResponse(status_code=500, content={"error": "Internal Server Error"})

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)
    return app


app = create_app()
