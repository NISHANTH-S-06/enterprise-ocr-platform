from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.core.database import get_db
from app.schemas.common import HealthResponse

router = APIRouter(tags=["Health"])
settings = get_settings()


def _ocr_engine_status() -> dict:
    """Task 5: Detect real vs mock OCR engine at runtime."""
    try:
        from app.services.ocr_service import get_engine
        engine_info = get_engine()
        is_mock = getattr(engine_info, "is_mock", False) or str(engine_info).lower().startswith("mock")
        return {"engine": str(engine_info), "mock": is_mock, "status": "warn" if is_mock else "ok"}
    except Exception as e:
        return {"engine": "unknown", "mock": True, "status": "warn", "detail": str(e)}


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        app=settings.APP_NAME,
        environment=settings.APP_ENV,
    )


@router.get("/health/ready", response_model=HealthResponse, summary="Readiness probe — checks DB + OCR engine")
async def readiness(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    checks: dict = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    ocr = _ocr_engine_status()
    checks["ocr_engine"] = ocr["engine"]
    checks["ocr_mock"]   = str(ocr["mock"])

    overall = "ok" if all(v == "ok" for k, v in checks.items() if k == "database") else "degraded"
    if ocr["mock"]:
        overall = "degraded"
    return HealthResponse(
        status=overall,
        app=settings.APP_NAME,
        environment=settings.APP_ENV,
        checks=checks,
    )
