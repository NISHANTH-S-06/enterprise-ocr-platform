"""
FormFillEngine  —  OCR JSON → field mapping → export

Usage
─────
  engine = FormFillEngine()
  result = await engine.fill(
      extracted_data_id = uuid,
      export_format     = ExportFormat.PDF,
      overrides         = {"total_amount": 10500.0},
      db                = async_session,
      user_id           = uuid,
  )
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ExportFormat, FillJobStatus
from app.models.extracted_data import ExtractedData
from app.models.fill_job import FormFillJob
from app.models.template import ExtractionTemplate
from app.schemas.fill import FieldFillResult, FillResult
from app.services.field_mapper import FieldMapper
from app.services.exporters import ExcelExporter, JSONExporter, PDFExporter
from app.services.ownership_service import get_owned_extracted_data


class FormFillEngine:
    def __init__(self) -> None:
        self._mapper       = FieldMapper()
        self._json_exp     = JSONExporter()
        self._excel_exp    = ExcelExporter()
        self._pdf_exp      = PDFExporter()

    async def fill(
        self,
        extracted_data_id: uuid.UUID,
        export_format:     ExportFormat,
        overrides:         Dict[str, Any],
        db:                AsyncSession,
        user_id:           uuid.UUID,
        current_user:      Optional[dict] = None,
        template_id:       Optional[uuid.UUID] = None,
        org_id:            Optional[uuid.UUID] = None,
    ) -> FillResult:
        t0 = time.monotonic()

        # ── 1. load extracted data ─────────────────────────────────────────────
        if current_user:
            extraction = await get_owned_extracted_data(db, extracted_data_id, current_user)
        else:
            extraction: ExtractedData = await db.get(ExtractedData, extracted_data_id)
            if extraction is None:
                raise ValueError(f"ExtractedData {extracted_data_id} not found")

        # ── 2. resolve template ────────────────────────────────────────────────
        tid = template_id or extraction.template_id
        template: Optional[ExtractionTemplate] = await db.get(ExtractionTemplate, tid) if tid else None

        schema          = template.field_schema     if template else {}
        mapping_config  = template.mapping_config   if template else {}
        fill_config     = template.fill_config      if template else {}
        template_path   = template.template_file_path if template else None

        # ── 3. create FormFillJob row ──────────────────────────────────────────
        job = FormFillJob(
            extracted_data_id=extracted_data_id,
            template_id=tid,
            created_by_id=user_id,
            org_id=org_id,
            export_format=export_format.value,
            status=FillJobStatus.PROCESSING.value,
            fill_overrides=overrides,
            started_at=datetime.utcnow(),
        )
        db.add(job)
        await db.flush()

        try:
            # ── 4. map fields ──────────────────────────────────────────────────
            final_fields, warnings = self._mapper.map(
                extracted_fields=extraction.extracted_fields,
                mapped_output=extraction.mapped_output,
                template_schema=schema,
                mapping_config=mapping_config,
                overrides=overrides,
            )
            line_items: List[Dict[str, Any]] = extraction.line_items or []

            # ── 5. export ──────────────────────────────────────────────────────
            metadata = {
                "job_id":           str(job.id),
                "extracted_data_id": str(extracted_data_id),
                "template_id":      str(tid) if tid else None,
                "export_format":    export_format.value,
            }

            if export_format == ExportFormat.JSON:
                output_path = self._json_exp.export(final_fields, line_items, job.id, metadata)
            elif export_format == ExportFormat.EXCEL:
                output_path = self._excel_exp.export(final_fields, line_items, job.id, fill_config, template_path)
            else:  # PDF
                output_path = self._pdf_exp.export(final_fields, line_items, job.id, fill_config, template_path)

            # ── 6. build result ────────────────────────────────────────────────
            field_results = self._build_field_results(final_fields, overrides, extraction.extracted_fields)
            filled   = sum(1 for r in field_results if r.source != "skipped")
            skipped  = sum(1 for r in field_results if r.source == "skipped")
            overrode = sum(1 for r in field_results if r.source == "override")
            elapsed  = int((time.monotonic() - t0) * 1000)

            fill_result_data = {
                "fields_filled":    filled,
                "fields_skipped":   skipped,
                "fields_overridden": overrode,
                "warnings":         warnings,
            }

            # ── 7. persist completion ──────────────────────────────────────────
            job.status       = FillJobStatus.COMPLETED.value
            job.output_path  = str(output_path)
            job.output_size  = output_path.stat().st_size if output_path.exists() else None
            job.fill_result  = fill_result_data
            job.processing_ms = elapsed
            job.completed_at = datetime.utcnow()
            await db.flush()

            logger.info(f"FormFill done: job={job.id} format={export_format} filled={filled} ms={elapsed}")

            return FillResult(
                job_id=job.id,
                export_format=export_format,
                output_path=str(output_path),
                fields_filled=filled,
                fields_skipped=skipped,
                fields_overridden=overrode,
                warnings=warnings,
                field_results=field_results,
                processing_ms=elapsed,
            )

        except Exception as exc:
            job.status        = FillJobStatus.FAILED.value
            job.error_message = str(exc)
            job.completed_at  = datetime.utcnow()
            await db.flush()
            logger.exception(f"FormFill failed: job={job.id} error={exc}")
            raise

    def _build_field_results(
        self,
        final:     Dict[str, Any],
        overrides: Dict[str, Any],
        extracted: Dict[str, Any],
    ) -> List[FieldFillResult]:
        results = []
        for field, value in final.items():
            if field in overrides:
                source = "override"
            elif field in extracted:
                source = "extracted"
                fv = extracted[field]
                conf = fv.get("confidence") if isinstance(fv, dict) else None
                results.append(FieldFillResult(field=field, value=value, source=source, confidence=conf))
                continue
            else:
                source = "default"
            results.append(FieldFillResult(field=field, value=value, source=source))
        return results


form_fill_engine = FormFillEngine()
