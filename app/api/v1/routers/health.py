from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.core.database import get_db
from app.schemas.common import HealthResponse

router = APIRouter(tags=["Health"])
settings = get_settings()


async def _ocr_engine_status() -> dict:
    """Task 5: Detect real vs mock OCR engine at runtime."""
    try:
        from app.services.ocr_service import get_engine
        engine_info = await get_engine()
        engine_name = str(getattr(engine_info, "name", engine_info))
        is_mock = getattr(engine_info, "is_mock", False) or engine_name.lower().startswith("mock")
        return {"engine": engine_name, "mock": is_mock, "status": "warn" if is_mock else "ok"}
    except Exception as e:
        return {"engine": "unknown", "mock": True, "status": "warn", "detail": str(e)}


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
@router.head("/health", include_in_schema=False)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        app=settings.APP_NAME,
        environment=settings.APP_ENV,
    )


@router.get("/ready", response_model=HealthResponse, summary="Readiness probe — checks DB + OCR engine")
@router.get("/health/ready", response_model=HealthResponse, summary="Readiness probe — checks DB + OCR engine")
@router.head("/ready", include_in_schema=False)
@router.head("/health/ready", include_in_schema=False)
async def readiness(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    checks: dict = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    ocr = await _ocr_engine_status()
    checks["ocr_engine"] = ocr["engine"]
    checks["ocr_mock"]   = str(ocr["mock"])

    overall = "ok" if all(v == "ok" for k, v in checks.items() if k == "database") else "degraded"
    if ocr["mock"]:
        overall = "degraded"

    degraded_reasons = []
    if checks["database"] != "ok":
        degraded_reasons.append(f"Database query failed: {checks['database']}")
    if ocr["mock"]:
        degraded_reasons.append("OCR engine is mock fallback")
    degraded_reason = " | ".join(degraded_reasons) if degraded_reasons else None

    return HealthResponse(
        status=overall,
        app=settings.APP_NAME,
        environment=settings.APP_ENV,
        checks=checks,
        degraded_reason=degraded_reason,
    )
