"""
upload_background.py — Background OCR job runner.
Extracted from upload.py for maintainability.
"""
from __future__ import annotations

import asyncio
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
from loguru import logger

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.enums import ExtractionStatus, FileStatus, JobStatus, LogEventType, LogLevel
from app.models.extracted_data import ExtractedData
from app.models.file import UploadedFile
from app.models.job import OCRJob
from app.models.user import Organisation
from app.services.audit_service import write_audit_log
from app.services.gemini_service import gemini_enrich_missing_fields
from app.services.validation_service import validate_mapped_fields
from app.services.validation_engine import run_validation
from app.services.confidence_engine import score_fields, build_field_response
from app.services.final_field_normalizer import build_final_ocr_decision
from app.services.company_resolver import resolve_company_id
from app.services.memory_apply_engine import (
    apply_memory_to_canonical_fields,
    load_company_correction_memory,
)

from app.api.v1.routers.upload_helpers import (
    OCR_DEMO_JOBS,
    OCR_RESULT_CACHE,
    OCR_FAILURE_TEXT,
    _build_demo_extraction,
    _extract_text_from_result,
    _flatten_fields,
    _ai_fallback_target_fields,
    _ai_fallback_audit_metadata,
    _should_attempt_ai_fallback,
    _build_detection_report,
    _build_ocr_response,
    _refresh_batch_counters,
)

settings = get_settings()


async def _ocr_background(file_path: str, mime_type: str, job_id: str, checksum: str) -> None:
    """Background OCR task — runs after upload response is returned."""
    job_t0 = time.monotonic()
    try:
        from app.services.ocr_service import process_file
        async with aiofiles.open(file_path, "rb") as f:
            data = await f.read()
        logger.info(f"OCR background input: job_id={job_id} path={file_path} mime={mime_type} size={len(data)}")
        async with AsyncSessionLocal() as db:
            job = await db.get(OCRJob, uuid.UUID(job_id))
            await write_audit_log(
                db,
                action="ocr.start",
                event_type=LogEventType.OCR_JOB,
                status="success",
                user_id=job.created_by_id if job else None,
                org_id=job.org_id if job else None,
                resource_type="ocr_job",
                resource_id=job_id,
                message="OCR started",
                metadata={"mime_type": mime_type, "file_size": len(data)},
            )
            await db.commit()
        cached = OCR_RESULT_CACHE.get(checksum)
        if cached:
            result = dict(cached)
            result["cache_hit"] = True
            logger.info("OCR cache hit: job_id={} checksum={}", job_id, checksum[:16])
        else:
            ocr_t0 = time.monotonic()
            result = await asyncio.wait_for(process_file(data, mime_type), timeout=settings.OCR_TIMEOUT_SECONDS)
            result["ocr_wall_ms"] = int((time.monotonic() - ocr_t0) * 1000)
            OCR_RESULT_CACHE[checksum] = dict(result)
        extracted_demo = _build_demo_extraction(result)
        text = _extract_text_from_result(result)
        
        from app.services.company_form_mapper import auto_fill, map_sales_invoice_fields, classify_document, missing_fields_for_doc, normalize_mapped_fields

        # ── Early Company detection (Phase 2 extracted) ──────────────────────
        detected_company_info: dict[str, Any] = {}
        try:
            from app.services.company_detector import detect_company
            async with AsyncSessionLocal() as _det_db:
                det = await detect_company(text, _det_db)
            detected_company_info = {
                "company_id":    str(det.company_id) if det.company_id else None,
                "company_name":  det.company_name,
                "match_method":  det.match_method,
                "confidence":    det.confidence,
                "is_known":      det.is_known,
                "matched_value": det.matched_value,
            }
            logger.info("Early company detection: job_id={} company_id={} company_name={}",
                        job_id,
                        detected_company_info.get("company_id"),
                        detected_company_info.get("company_name"))
        except Exception as _det_exc:
            logger.warning("Early company detection failed: {}", _det_exc)

        # ── Document classification ───────────────────────────────────────────
        cls_result = classify_document(text, filename=Path(file_path).name)

        document_type = "sales" if cls_result["mapper"] == "sales" else "general"
        logger.info("Classifier: job_id={} doc_type={} mapper={} score={} rule={}",
                    job_id, cls_result["doc_type"], cls_result["mapper"],
                    cls_result["score"], cls_result["matched_rule"])

        mapper_t0 = time.monotonic()
        if not text:
            mapped_result = {
                "success": bool(result.get("success", True)),
                "mapped_fields": {},
                "missing_fields": [],
                "message": "No OCR text returned.",
            }
        else:
            if document_type == "sales":
                mapped_result = map_sales_invoice_fields(text)
                from app.services.company_form_mapper import flatten_item_rows_for_legacy_fields
                mapped_result = flatten_item_rows_for_legacy_fields(mapped_result, cls_result["doc_type"], text)
            else:
                mapped_result = auto_fill(text, doc_type=cls_result["doc_type"].lower())
        mapped_result["mapped_fields"] = normalize_mapped_fields(mapped_result.get("mapped_fields", {}))
        regex_fields = dict(mapped_result.get("mapped_fields", {}))
        effective_doc_type = (mapped_result.get("document_type") or cls_result["doc_type"] or document_type or "general").lower()
        mapped_result["document_type"] = effective_doc_type
        mapped_result["missing_fields"] = missing_fields_for_doc(mapped_result["mapped_fields"], effective_doc_type)
        non_doc_values = [
            v.get("value") if isinstance(v, dict) else v
            for k, v in mapped_result["mapped_fields"].items()
            if k not in {"document_type", "doc_type", "item_rows"}
        ]
        if text and not any(str(v or "").strip() for v in non_doc_values):
            mapped_result["weak_mapping"] = True
            mapped_result["message"] = "OCR text detected but mapping failed"
            mapped_result["raw_text_preview"] = text[:500]
        logger.info(
            "AutoFillDebug: job_id={} document_type={} text_len={} preview={} extracted_keys={} missing_fields={}",
            job_id,
            effective_doc_type,
            len(text or ""),
            (text or "")[:500].replace("\n", " "),
            sorted(mapped_result.get("mapped_fields", {}).keys()),
            mapped_result.get("missing_fields", []),
        )
        
        mapper_ms = int((time.monotonic() - mapper_t0) * 1000)

        # ── Phase 5: Training template matching (after regex, before Gemini) ──
        _training_matched = False
        _training_name: str | None = None
        _training_score: float = 0.0
        _training_id: str | None = None
        _training_applied_fields: list[str] = []
        _training_conflicts: list[str] = []
        result["training_template_matched"] = False
        try:
            from app.services.training_template_engine import match_training_dataset, apply_training_to_mapped_fields, THRESHOLD_APPLY
            async with AsyncSessionLocal() as _tdb:
                _company_info_for_training = detected_company_info if "detected_company_info" in dir() else {}
                _training_match = await match_training_dataset(
                    _tdb,
                    text,
                    effective_doc_type,
                    _company_info_for_training,
                    effective_doc_type,
                    update_usage=True,
                )
            if _training_match and _training_match.get("score", 0.0) >= THRESHOLD_APPLY:
                mapped_result["raw_text"] = text
                _apply_result = apply_training_to_mapped_fields(mapped_result, _training_match)
                mapped_result["mapped_fields"] = _apply_result.get("mapped_fields", mapped_result.get("mapped_fields", {}))
                _training_matched = True
                _training_name = _training_match.get("dataset_name") or _training_match.get("name")
                _training_score = float(_training_match.get("score", 0.0))
                _training_id = str(_training_match.get("dataset_id") or _training_match.get("id") or "")
                _training_applied_fields = _apply_result.get("training_applied_fields", [])
                _training_conflicts = _apply_result.get("training_conflicts", [])
                mapped_result["mapped_fields"] = normalize_mapped_fields(mapped_result.get("mapped_fields", {}))
                mapped_result["missing_fields"] = missing_fields_for_doc(
                    mapped_result["mapped_fields"], effective_doc_type
                )
                result["training_template_matched"] = _training_matched
                result["training_template_name"] = _training_name
                result["training_match_score"] = _training_score
                result["training_template_id"] = _training_id
                result["training_applied_fields"] = _training_applied_fields
                result["training_conflicts"] = _training_conflicts
                logger.info(
                    "TrainingTemplate: job_id={} matched={} score={:.2f} applied={}",
                    job_id, _training_name, _training_score, _training_applied_fields,
                )
        except Exception as _te:
            logger.warning("TrainingTemplate: skipped due to error: {}", _te)

        ai_targets = _ai_fallback_target_fields(mapped_result)
        tenant_ai_source: Any = None
        async with AsyncSessionLocal() as db:
            job = await db.get(OCRJob, uuid.UUID(job_id))
            if job and job.org_id:
                tenant_ai_source = await db.get(Organisation, job.org_id)
        if _should_attempt_ai_fallback(ai_targets, tenant_ai_source):
            mapped_result = await gemini_enrich_missing_fields(
                mapped_result,
                text,
                mapped_result.get("document_type", document_type),
                missing_fields=mapped_result.get("missing_fields", []),
            )
            mapped_result["mapped_fields"] = normalize_mapped_fields(mapped_result.get("mapped_fields", {}))
            mapped_result["missing_fields"] = missing_fields_for_doc(
                mapped_result["mapped_fields"],
                mapped_result.get("document_type", effective_doc_type),
            )
        ocr_response = _build_ocr_response(result, mapped_result)

        # ── Task 2+3: Validate mapped fields before DB write ─────────────────
        mapped_for_validation = mapped_result.get("mapped_fields", {})
        validation = validate_mapped_fields(
            mapped_for_validation,
            raw_text=text,
            doc_type=mapped_result.get("document_type", document_type),
        )

        # ── Phase 2: Company detection + extended validation + confidence ────
        detected_company_info: dict[str, Any] = {}
        company_resolution_warnings: list[str] = []
        p2_field_response: list[dict] = []
        p2_summary_counts: dict[str, Any] = {}
        final_ocr_decision: dict[str, Any] = {}
        memory_apply_result: dict[str, Any] = {
            "memory_suggestions": {},
            "memory_applied_fields": [],
            "memory_suggestion_fields": [],
            "memory_applied_count": 0,
            "memory_suggestion_count": 0,
            "warnings": [],
        }
        memory_warnings: list[str] = []
        try:
            # Reuse already-detected company info
            pass

            ext_val = run_validation(
                mapped_for_validation,
                raw_text=text,
                doc_type=mapped_result.get("document_type", document_type),
            )
            existing_w = set(validation["warnings"])
            for w in ext_val.warnings:
                if w not in existing_w:
                    validation["warnings"].append(w)
                    existing_w.add(w)

            conf_summary = score_fields(
                mapped_for_validation,
                validation_errors=ext_val.errors + validation["errors"],
            )
            p2_field_response  = build_field_response(conf_summary)
            p2_summary_counts  = {
                "auto_filled_count":      conf_summary.auto_filled_count,
                "need_review_count":      conf_summary.need_review_count,
                "missing_count":          conf_summary.missing_count,
                "validation_error_count": conf_summary.validation_error_count,
                "export_allowed":         conf_summary.export_allowed,
                "overall_confidence":     conf_summary.overall_confidence,
            }
            final_ocr_decision = build_final_ocr_decision(
                p2_field_response,
                document_type=mapped_result.get("document_type", document_type),
            )
        except Exception as _p2_exc:
            logger.warning("Phase 2 enrichment failed (non-fatal): job_id={} err={}", job_id, _p2_exc)
        if isinstance(final_ocr_decision.get("canonical_fields"), dict):
            try:
                async with AsyncSessionLocal() as _resolve_db:
                    resolved_company = await resolve_company_id(
                        _resolve_db,
                        text,
                        final_ocr_decision["canonical_fields"],
                        detected_company_info,
                    )
                if isinstance(resolved_company, dict):
                    existing_company = detected_company_info if isinstance(detected_company_info, dict) else {}
                    company_resolution_warnings = list(resolved_company.get("warnings") or [])
                    detected_company_info = {
                        **existing_company,
                        **resolved_company,
                        "warnings": list(dict.fromkeys(
                            list(existing_company.get("warnings") or []) + company_resolution_warnings
                        )),
                    }
            except Exception as _resolver_exc:
                company_resolution_warnings.append(f"Company resolution failed: {_resolver_exc}")
                logger.warning("Company resolver failed (non-fatal): job_id={} err={}", job_id, _resolver_exc)
            try:
                company_id = (
                    detected_company_info.get("company_id")
                    if isinstance(detected_company_info, dict)
                    else None
                )
                if company_id:
                    async with AsyncSessionLocal() as _memory_db:
                        memories = await load_company_correction_memory(
                            _memory_db,
                            company_id,
                            mapped_result.get("document_type") or document_type,
                        )
                    memory_apply_result = apply_memory_to_canonical_fields(
                        final_ocr_decision["canonical_fields"],
                        memories,
                    )
                    memory_warnings = [
                        str(w) for w in memory_apply_result.get("warnings", []) if w
                    ]
                    from app.services.correction_memory import recompute_final_decision_after_corrections
                    final_ocr_decision = recompute_final_decision_after_corrections(
                        memory_apply_result.get("canonical_fields", {})
                    )
                else:
                    memory_warnings.append("Correction memory skipped: company_id unavailable")
            except Exception as _memory_exc:
                memory_warnings.append(f"Correction memory apply failed: {_memory_exc}")
                logger.warning("Correction memory apply failed (non-fatal): job_id={} err={}", job_id, _memory_exc)
        _memory_canonical = memory_apply_result.get("canonical_fields", {})
        for _mf_name in memory_apply_result.get("memory_applied_fields", []):
            _mf_entry = _memory_canonical.get(_mf_name)
            if not isinstance(_mf_entry, dict) or not _mf_entry.get("value"):
                continue
            _patched = {
                "value": _mf_entry["value"],
                "confidence": _mf_entry.get("confidence", 0.85),
                "method": _mf_entry.get("method", "memory_auto_apply"),
                "source": _mf_entry.get("source", "correction_memory"),
                "source_evidence": _mf_entry.get("evidence", ""),
                "status": _mf_entry.get("status", "AUTO_FILLED"),
            }
            # Patch mapped_result["mapped_fields"]
            _existing_mf = mapped_result.get("mapped_fields", {}).get(_mf_name)
            if isinstance(_existing_mf, dict):
                mapped_result["mapped_fields"][_mf_name] = {**_existing_mf, **_patched}
            else:
                mapped_result.setdefault("mapped_fields", {})[_mf_name] = _patched
            # Patch validation["validated_fields"]
            _existing_vf = validation["validated_fields"].get(_mf_name)
            if isinstance(_existing_vf, dict):
                validation["validated_fields"][_mf_name] = {**_existing_vf, **_patched}
            else:
                validation["validated_fields"][_mf_name] = _patched
        ai_audit_metadata = _ai_fallback_audit_metadata(mapped_result, validation["warnings"])
        ocr_warnings = list(ocr_response.get("warnings", []))
        ocr_warnings.extend(validation["warnings"])
        ocr_warnings.extend(company_resolution_warnings)
        ocr_warnings.extend(memory_warnings)
        needs_ai_review = bool(ai_audit_metadata and ai_audit_metadata.get("needs_review"))
        is_mock = bool(result.get("is_mock_ocr") or "Mock OCR" in text or not validation["valid"] or needs_ai_review)
        extraction_status = ExtractionStatus.VALIDATED.value if (validation["valid"] and not is_mock) \
            else ExtractionStatus.NEEDS_REVIEW.value

        async with AsyncSessionLocal() as db:
            job_uuid = uuid.UUID(job_id)
            job = await db.get(OCRJob, job_uuid)
            if not job:
                raise RuntimeError(f"OCRJob {job_id} not found")

            completed_at = datetime.now(timezone.utc)
            fields = extracted_demo["fields"]
            extracted = ExtractedData(
                job_id=job.id,
                file_id=job.file_id,
                template_id=job.template_id,
                org_id=job.org_id,
                raw_ocr_json=result,
                raw_text=text,
                page_count=int(result.get("page_count", 0) or 0),
                extracted_fields={
                    k: ({"value": v.get("value"), "confidence": v.get("confidence", 0.0), "method": v.get("method", "regex"), "source": v.get("source", "")} if isinstance(v, dict) else {"value": v, "confidence": 0.0, "method": "unknown"})
                    for k, v in mapped_result.get("mapped_fields", {}).items()
                } or fields,
                validated_fields=validation["validated_fields"],
                mapped_output={k: v["value"] if isinstance(v, dict) else v
                               for k, v in validation["validated_fields"].items()},
                line_items=(
                    mapped_for_validation.get("item_rows", {}).get("value", [])
                    if isinstance(mapped_for_validation.get("item_rows"), dict)
                    else mapped_for_validation.get("item_rows", []) or []
                ),
                overall_confidence=float(result.get("avg_confidence", 0.0) or 0.0),
                validation_errors=[{"error": e} for e in validation["errors"]]
                                 + [{"warning": w} for w in validation["warnings"]],
                fields_extracted=validation["field_count"],
                fields_high_confidence=sum(
                    1 for v in validation["validated_fields"].values()
                    if isinstance(v, dict) and float(v.get("confidence", 0) or 0) >= 0.8
                ),
                fields_needing_review=len(validation["warnings"]),
                status=extraction_status,
            )
            db.add(extracted)

            uploaded_file = await db.get(UploadedFile, job.file_id)
            if uploaded_file:
                uploaded_file.status = FileStatus.DONE.value
                uploaded_file.page_count = int(result.get("page_count", 0) or 0)
                uploaded_file.document_type = effective_doc_type or None

            job.status = JobStatus.COMPLETED.value
            job.started_at = job.started_at or completed_at
            job.completed_at = completed_at
            job.processing_ms = int(result.get("processing_ms", 0) or 0)
            job.page_count = int(result.get("page_count", 0) or 0)
            job.avg_confidence = float(result.get("avg_confidence", 0.0) or 0.0)
            job.total_words = len(text.split())

            await db.flush()
            extracted_data_id = str(extracted.id)
            await write_audit_log(
                db,
                action="ocr.complete",
                event_type=LogEventType.OCR_JOB,
                status="success",
                user_id=job.created_by_id,
                org_id=job.org_id,
                resource_type="ocr_job",
                resource_id=job.id,
                message="OCR completed",
                metadata={
                    "extracted_data_id": extracted_data_id,
                    "page_count": job.page_count,
                    "avg_confidence": job.avg_confidence,
                    "fields_extracted": extracted.fields_extracted,
                },
            )
            await write_audit_log(
                db,
                action="validation.complete",
                event_type=LogEventType.EXTRACTION,
                status="success" if validation["valid"] else "failure",
                user_id=job.created_by_id,
                org_id=job.org_id,
                resource_type="extracted_data",
                resource_id=extracted.id,
                level=LogLevel.INFO if validation["valid"] else LogLevel.WARNING,
                message="Validation completed",
                metadata={
                    "field_count": validation["field_count"],
                    "error_count": len(validation["errors"]),
                    "warning_count": len(validation["warnings"]),
                    "errors": validation["errors"],
                },
            )
            if ai_audit_metadata:
                await write_audit_log(
                    db,
                    action="ai.fallback.used",
                    event_type=LogEventType.EXTRACTION,
                    status="success",
                    user_id=job.created_by_id,
                    org_id=job.org_id,
                    resource_type="extracted_data",
                    resource_id=extracted.id,
                    level=LogLevel.WARNING if ai_audit_metadata["needs_review"] else LogLevel.INFO,
                    message="AI fallback used",
                    metadata=ai_audit_metadata,
                )
            await _refresh_batch_counters(db, job.batch_id)
            await db.commit()

        OCR_DEMO_JOBS[job_id].update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "extracted_data_id": extracted_data_id,
            "ocr_engine": result.get("ocr_engine") or result.get("engine") or "unknown",
            "raw_text": text,
            "text": text,
            "extracted_text": text,
            "full_text": text,
            "raw_text_length": len(text),
            "raw_text_preview": text[:300],
            "detected_line_count": int(result.get("detected_line_count", 0) or 0),
            "weak_ocr": bool(result.get("weak_ocr", len(text.strip()) < 20)),
            "weak_ocr_message": result.get("weak_ocr_message", ""),
            "mapped_fields": mapped_result.get("mapped_fields", {}),
            "validated_fields": validation["validated_fields"],
            "regex_fields": regex_fields,
            "gemini_fields": mapped_result.get("gemini_fields", {}),
            "weak_mapping": bool(mapped_result.get("weak_mapping", False)),
            "items": (
                (mapped_result.get("mapped_fields", {}).get("items") or mapped_result.get("mapped_fields", {}).get("item_rows") or {}).get("value", [])
                if isinstance((mapped_result.get("mapped_fields", {}).get("items") or mapped_result.get("mapped_fields", {}).get("item_rows") or {}), dict)
                else (mapped_result.get("mapped_fields", {}).get("items") or mapped_result.get("mapped_fields", {}).get("item_rows") or [])
            ),
            "missing_fields": mapped_result.get("missing_fields", []),
            "mapper_message": mapped_result.get("message", ""),
            "sales_original_text_preview": mapped_result.get("sales_original_text_preview", ""),
            "sales_mapping_debug": mapped_result.get("sales_mapping_debug", {}),
            "missing_sales_fields": mapped_result.get("missing_sales_fields", []),
            "rejected_sales_values": mapped_result.get("rejected_sales_values", []),
            "field_confidence": mapped_result.get("field_confidence", {}),
            "success": True,
            "fields": ocr_response["fields"],
            "confidence": ocr_response["confidence"],
            "warnings": ocr_warnings,
            "validation_errors": validation["errors"],
            "validation_warnings": validation["warnings"],
            "ai_fallback": ai_audit_metadata,
            "detection_report": _build_detection_report(mapped_for_validation, validation),
            "extraction_status": extraction_status,
            "ocr_response": ocr_response,
            "ocr_result": result,
            "extracted_demo": extracted_demo,
            "classifier": {"doc_type": cls_result["doc_type"], "score": cls_result["score"],
                           "rule": cls_result["matched_rule"]},
            "detected_company":      detected_company_info,
            "company_id":            detected_company_info.get("company_id"),
            "quality":               {
                "page_count":  int(result.get("page_count", 0) or 0),
                "avg_confidence": float(result.get("avg_confidence", 0.0) or 0.0),
                "weak_ocr":    bool(result.get("weak_ocr", len(text.strip()) < 20)),
            },
            "p2_fields":             p2_field_response,
            "auto_filled_count":     p2_summary_counts.get("auto_filled_count", 0),
            "need_review_count":     p2_summary_counts.get("need_review_count", 0),
            "missing_count":         p2_summary_counts.get("missing_count", 0),
            "validation_error_count":p2_summary_counts.get("validation_error_count", 0),
            "export_allowed":        p2_summary_counts.get("export_allowed", True),
            "overall_confidence":    p2_summary_counts.get("overall_confidence", 0.0),
            "memory_applied_count":   memory_apply_result.get("memory_applied_count", 0),
            "memory_suggestion_count":memory_apply_result.get("memory_suggestion_count", 0),
            "memory_applied_fields":  memory_apply_result.get("memory_applied_fields", []),
            "memory_suggestion_fields": memory_apply_result.get("memory_suggestion_fields", []),
            "memory_suggestions":     memory_apply_result.get("memory_suggestions", {}),
            "memory_warnings":        memory_warnings,
            "training_template_matched":  _training_matched,
            "training_template_name":     _training_name,
            "training_match_score":       _training_score,
            "training_template_id":       _training_id or None,
            "training_applied_fields":    _training_applied_fields,
            "training_conflicts":         _training_conflicts,
            **final_ocr_decision,
        })
        if len(text.strip()) < 20:
            OCR_DEMO_JOBS[job_id].update({
                "weak_ocr": True,
                "weak_ocr_message": "OCR text is weak. Use clearer image or paste text manually.",
            })
        if result.get("is_mock_ocr") or "Mock OCR" in text:
            OCR_DEMO_JOBS[job_id].update({
                "warning": "Real OCR engine not installed. Install paddlepaddle+paddleocr.",
                "install_hint": "pip install paddlepaddle paddleocr",
                "extraction_status": ExtractionStatus.NEEDS_REVIEW.value,
                "is_mock_ocr": True,
            })
        if validation["errors"]:
            OCR_DEMO_JOBS[job_id]["warning"] = " | ".join(validation["errors"])
        logger.info(
            "Background OCR complete: job_id={} engine={} pages={} conf={:.0%} text_len={} lines={} mapped_fields={} mapper_status={} pdf_text_ms={} ocr_ms={} mapper_ms={} total_job_ms={} cache_hit={}",
            job_id,
            result.get("ocr_engine") or result.get("engine") or "unknown",
            result.get("page_count", 0),
            float(result.get("avg_confidence", 0.0) or 0.0),
            len(text),
            int(result.get("detected_line_count", 0) or 0),
            len(mapped_result.get("mapped_fields", {}) or {}),
            mapped_result.get("message", ""),
            int(result.get("pdf_text_extraction_ms", 0) or 0),
            int(result.get("ocr_ms", result.get("processing_ms", 0)) or 0),
            mapper_ms,
            int((time.monotonic() - job_t0) * 1000),
            bool(result.get("cache_hit")),
        )
    except Exception as exc:
        async with AsyncSessionLocal() as db:
            try:
                job = await db.get(OCRJob, uuid.UUID(job_id))
                if job:
                    job.status = JobStatus.FAILED.value
                    job.completed_at = datetime.now(timezone.utc)
                    job.error_message = str(exc)
                    uploaded_file = await db.get(UploadedFile, job.file_id)
                    if uploaded_file:
                        uploaded_file.status = FileStatus.FAILED.value
                    await write_audit_log(
                        db,
                        action="ocr.failure",
                        event_type=LogEventType.OCR_JOB,
                        status="failure",
                        user_id=job.created_by_id,
                        org_id=job.org_id,
                        resource_type="ocr_job",
                        resource_id=job.id,
                        level=LogLevel.ERROR,
                        message="OCR failed",
                        metadata={"error": str(exc)},
                        error=str(exc),
                    )
                    await _refresh_batch_counters(db, job.batch_id)
                    await db.commit()
            except Exception as db_exc:
                await db.rollback()
                logger.error(f"Background OCR failure status update failed: job_id={job_id} error={db_exc}")
        error_text = str(exc)
        failed_response = {
            "success": True,
            "text": OCR_FAILURE_TEXT,
            "raw_text_length": len(OCR_FAILURE_TEXT),
            "detected_line_count": 1,
            "raw_text_preview": OCR_FAILURE_TEXT,
            "fields": {},
            "confidence": {},
            "warnings": [error_text],
        }
        OCR_DEMO_JOBS.setdefault(job_id, {}).update({
            "status": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "success": True,
            "text": OCR_FAILURE_TEXT,
            "raw_text": OCR_FAILURE_TEXT,
            "extracted_text": OCR_FAILURE_TEXT,
            "full_text": OCR_FAILURE_TEXT,
            "raw_text_length": len(OCR_FAILURE_TEXT),
            "detected_line_count": 1,
            "raw_text_preview": OCR_FAILURE_TEXT,
            "fields": {},
            "confidence": {},
            "warnings": [error_text],
            "ocr_response": failed_response,
            "error": error_text,
        })
        logger.exception(f"Background OCR failed: job_id={job_id} error={exc}")
