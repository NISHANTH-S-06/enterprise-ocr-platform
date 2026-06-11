from __future__ import annotations
import uuid
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import require_roles
from app.models.enums import ExportFormat, UserRole
from app.models.fill_job import FormFillJob
from app.schemas.fill import FillRequest, FillResult
from app.services.ownership_service import get_owned_fill_job
from app.services.form_fill_engine import form_fill_engine

router = APIRouter(prefix="/fill", tags=["Form Fill"])


@router.post("/", response_model=FillResult, status_code=202,
             summary="Fill a form from extracted OCR data",
             description=(
                 "Maps OCR-extracted fields onto a template and exports a document.\n\n"
                 "**Formats:** `pdf` · `excel` · `json`\n\n"
                 "Supply `overrides` to correct any field before export.\n\n"
                 "Returns fill stats + per-field source attribution."
             ))
async def fill_form(
    req: FillRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
) -> FillResult:
    try:
        return await form_fill_engine.fill(
            extracted_data_id=req.extracted_data_id,
            export_format=req.export_format,
            overrides=req.overrides,
            db=db,
            user_id=uuid.UUID(current_user["user_id"]),
            current_user=current_user,
            template_id=req.template_id,
            org_id=uuid.UUID(current_user["org_id"]) if current_user.get("org_id") else None,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Fill failed: {exc}")


@router.get("/jobs/{job_id}/download",
            summary="Download the exported document")
async def download_output(
    job_id: uuid.UUID,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    job = await get_owned_fill_job(db, job_id, current_user)
    if job.status != "completed" or not job.output_path:
        raise HTTPException(status_code=409, detail="Output not ready")

    from pathlib import Path
    p = Path(job.output_path)
    if not p.exists():
        raise HTTPException(status_code=410, detail="Output file no longer available")

    media_map = {"pdf": "application/pdf",
                 "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 "json": "application/json"}
    media = media_map.get(p.suffix.lstrip("."), "application/octet-stream")
    return FileResponse(path=str(p), media_type=media, filename=p.name)


@router.get("/jobs/{job_id}", summary="Get fill job status")
async def get_fill_job(
    job_id: uuid.UUID,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    job = await get_owned_fill_job(db, job_id, current_user)
    return {
        "job_id":        str(job.id),
        "status":        job.status,
        "export_format": job.export_format,
        "output_path":   job.output_path,
        "output_size":   job.output_size,
        "fill_result":   job.fill_result,
        "processing_ms": job.processing_ms,
        "error_message": job.error_message,
        "created_at":    job.created_at.isoformat(),
        "completed_at":  job.completed_at.isoformat() if job.completed_at else None,
    }


@router.get("/formats", summary="List supported export formats")
async def list_formats() -> dict:
    return {"formats": [f.value for f in ExportFormat]}
