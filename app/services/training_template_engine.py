"""training_template_engine.py — OCR Training Template Matching Engine.

Phase 3 — minimal, focused service.

Public API
----------
build_template_signature(raw_text, document_type, company_info)
    → deterministic string that fingerprints a document LAYOUT,
      not its volatile values.

score_template_similarity(raw_text, dataset, document_type, company_info)
    → float 0.0–1.0  (normalised from the 0-100 integer scorer below)

match_training_dataset(db, raw_text, document_type, company_info, module)
    → best-match dict  OR  None if no dataset scores >= 0.50

apply_training_to_mapped_fields(mapped_result, matched_dataset)
    → updated mapped_result + metadata dicts

Priority (do-not-overwrite rule)
---------------------------------
manual_correction   100  (source=="user_correction" or method=="manual_correction")
correction_memory    85  (source=="correction_memory")
training             80  ← what this engine writes
regex / AI           70 / 60
ocr_guess            40
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import TrainingDataset


# ── Volatile-value patterns (EXCLUDED from signature) ─────────────────────
# These change per document instance and must NOT anchor the fingerprint.
_VOLATILE_RE = re.compile(
    r"""
    (?:
        \b\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4}\b   # dates  12/01/2025
      | \b\d{4}[\/\-.]\d{1,2}[\/\-.]\d{1,2}\b      # dates  2025-01-12
      | \b[A-Z]{2,4}[-/]?\d{3,}\b                  # inv numbers  INV-001
      | (?<!\w)\d[\d,]*\.?\d*\b                     # amounts / quantities
      | \b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b  # month words
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Stable structural label keywords worth including in the signature.
# These are layout anchors — headings, labels, column headers.
_LABEL_WORDS = re.compile(
    r"""
    \b(
        invoice|bill|tax|purchase|order|delivery|dispatch|grn|receipt|
        proforma|commercial|export|import|consignment|shipment|
        gstin|gstin_uin|gst|iec|pan|cin|
        buyer|seller|vendor|supplier|consignee|exporter|shipper|
        description|particulars|item|product|goods|quantity|rate|amount|
        total|subtotal|grand|net|gross|balance|
        bank|account|ifsc|swift|micr|
        freight|insurance|cif|fob|
        port|loading|discharge|destination|
        eway|vehicle|transporter|lr|grn|challan|
        date|due|place|state|country|currency|
        hsn|sac|tax|cgst|sgst|igst|cess|
        remarks|declaration|terms|conditions
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Fields that correction_memory and manual corrections write — never overwrite
_PROTECTED_SOURCES = {"user_correction", "correction_memory", "manual_correction"}
_PROTECTED_METHODS = {"manual_correction", "memory_auto_apply"}


# ── Normalisation helpers ──────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Collapse whitespace, lower-case."""
    return " ".join(str(text or "").casefold().split())


def _normalise_module(module: str | None) -> str:
    """Map frontend/loose module strings to canonical stored values."""
    raw = _clean(module or "general").replace(" ", "_")
    _MAP = {
        "sales": "sales_order",
        "purchase": "purchase_order",
        "sales_invoice": "sales",
        "commercial_invoice": "commercial_invoice",
        "export_invoice": "export_invoice",
        "grn": "grn",
        "invoice": "invoice",
        "general": "general",
    }
    return _MAP.get(raw, raw)


# ── Signature builder ──────────────────────────────────────────────────────

def build_template_signature(
    raw_text: str,
    document_type: str | None = None,
    company_info: dict[str, Any] | None = None,
) -> str:
    """Return a stable, deterministic fingerprint of the document *layout*.

    What IS included (stable anchors):
        • document_type
        • company identifier tokens (name / GST / IEC when available)
        • top-N structural label keywords extracted from OCR text
        • first-line tokens from the document (usually the header/title)

    What is NOT included (volatile):
        • invoice numbers, dates, amounts, quantities, order numbers
        • raw OCR text in full
    """
    parts: list[str] = []

    # 1. Document type anchor
    doc_t = _clean(document_type or "")
    if doc_t:
        parts.append(f"dt:{doc_t}")

    # 2. Company identifier (stable across documents from same company)
    info = company_info or {}
    for key in ("gst_number", "iec_code", "company_name"):
        val = _clean(
            (info.get(key) or "")
            if not isinstance(info.get(key), dict)
            else (info[key].get("value") or info[key].get("text") or "")
        )
        if val:
            # For company_name keep only first 3 tokens to avoid address noise
            if key == "company_name":
                val = " ".join(val.split()[:3])
            parts.append(f"{key[0]}:{val}")
            break  # one identifier is sufficient

    # 3. First non-blank line — often the document title / letterhead
    lines = [ln.strip() for ln in str(raw_text or "").splitlines() if ln.strip()]
    if lines:
        first_line = _VOLATILE_RE.sub(" ", lines[0])
        first_line = " ".join(first_line.split())[:80]
        if first_line:
            parts.append(f"h:{first_line}")

    # 4. Sorted set of structural label keywords found in the whole text
    cleaned = _VOLATILE_RE.sub(" ", str(raw_text or ""))
    keywords = sorted(set(m.group(1).lower() for m in _LABEL_WORDS.finditer(cleaned)))
    # Keep top-40 to bound length; alphabetical sort makes it deterministic
    label_token = ",".join(keywords[:40])
    if label_token:
        parts.append(f"kw:{label_token}")

    # 5. Page/block count hint (rough structural marker)
    page_count = str(raw_text or "").lower().count("\f") + 1
    if page_count > 1:
        parts.append(f"pg:{page_count}")

    raw_sig = "|".join(parts)
    # SHA-1 (not for security — just a short, stable fingerprint)
    sig_hash = hashlib.sha1(raw_sig.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"tpl:{sig_hash}"


# ── Similarity scorer ──────────────────────────────────────────────────────

def score_template_similarity(
    raw_text: str,
    dataset: TrainingDataset,
    document_type: str | None = None,
    company_info: dict[str, Any] | None = None,
) -> float:
    """Score 0.0–1.0 how well *dataset* matches the incoming document.

    Scoring breakdown (total 100 pts → divided by 100):
        document_type exact match      : 35
        company identity exact match   : 30
        company identity partial match : 15
        template_signature match       : 25
        keyword overlap bonus          :  5  (added on top, capped at 1.0)
    """
    score = 0

    # ── document_type ────────────────────────────────────────────────────
    dt_req = _clean(document_type or "")
    dt_ds = _clean(getattr(dataset, "document_type", "") or "")
    if dt_req and dt_ds:
        if dt_req == dt_ds:
            score += 35
        elif dt_req in dt_ds or dt_ds in dt_req:
            score += 20

    # ── company identity ─────────────────────────────────────────────────
    info = company_info or {}

    def _field(d: dict, key: str) -> str:
        v = d.get(key) or ""
        return _clean(v.get("value") or v.get("text") or "" if isinstance(v, dict) else v)

    req_company = _field(info, "company_name")
    req_gst = _field(info, "gst_number")
    req_vendor = _field(info, "vendor_name")
    req_customer = _field(info, "customer_name")

    ds_company = _clean(getattr(dataset, "company_name", "") or "")
    ds_vendor = _clean(getattr(dataset, "vendor_name", "") or "")
    ds_customer = _clean(getattr(dataset, "customer_name", "") or "")

    def _identity_score(req: str, stored: str) -> int:
        if not req or not stored:
            return 0
        if req == stored:
            return 30
        if req in stored or stored in req:
            return 15
        # token overlap ≥ 50 %
        toks_req = set(req.split())
        toks_ds = set(stored.split())
        if toks_req and toks_ds:
            overlap = len(toks_req & toks_ds) / max(len(toks_req | toks_ds), 1)
            if overlap >= 0.5:
                return 10
        return 0

    id_score = max(
        _identity_score(req_company, ds_company),
        _identity_score(req_vendor, ds_vendor),
        _identity_score(req_customer, ds_customer),
        # GST exact match is a strong identifier
        30 if (req_gst and ds_company and req_gst.lower()[:15] in ds_company.lower()) else 0,
    )
    score += id_score

    # ── template_signature ───────────────────────────────────────────────
    ds_sig = _clean(getattr(dataset, "template_signature", "") or "")
    if ds_sig:
        incoming_sig = build_template_signature(raw_text, document_type, company_info)
        if incoming_sig == ds_sig:
            score += 25
        else:
            # Partial: compare the keyword portion after "kw:"
            def _kw_tokens(sig: str) -> set[str]:
                m = re.search(r"kw:([^|]+)", sig)
                return set(m.group(1).split(",")) if m else set()
            kw_ds = _kw_tokens(ds_sig)
            kw_in = _kw_tokens(incoming_sig)
            if kw_ds and kw_in:
                kw_overlap = len(kw_ds & kw_in) / max(len(kw_ds | kw_in), 1)
                score += int(kw_overlap * 20)

    # ── keyword bonus (partial OCR text overlap) ──────────────────────────
    # Compare top structural keywords in incoming text with keywords implied
    # by the dataset's document_type + company_name (rough heuristic).
    cleaned_text = _VOLATILE_RE.sub(" ", str(raw_text or ""))
    incoming_kws = set(m.group(1).lower() for m in _LABEL_WORDS.finditer(cleaned_text))
    ds_reference = f"{dt_ds} {ds_company} {ds_vendor} {ds_customer}"
    ds_ref_kws = set(m.group(1).lower() for m in _LABEL_WORDS.finditer(ds_reference))
    if incoming_kws and ds_ref_kws:
        kw_bonus = len(incoming_kws & ds_ref_kws) / max(len(ds_ref_kws), 1)
        score += int(kw_bonus * 5)

    return min(round(score / 100.0, 4), 1.0)


# ── Dataset matcher ────────────────────────────────────────────────────────

# Threshold constants (as float 0.0–1.0)
THRESHOLD_APPLY = 0.75    # auto-apply training mapping
THRESHOLD_SUGGEST = 0.50  # suggest training but mark needs_review
THRESHOLD_IGNORE = 0.50   # below this → return None

_VALID_MODULES = {
    "sales_order", "purchase_order", "sales", "purchase",
    "commercial_invoice", "export_invoice", "grn", "invoice", "general",
}


async def match_training_dataset(
    db: AsyncSession,
    raw_text: str,
    document_type: str | None = None,
    company_info: dict[str, Any] | None = None,
    module: str | None = None,
    *,
    update_usage: bool = False,
) -> dict[str, Any] | None:
    """Find the best matching active+approved TrainingDataset for this document.

    Returns a dict with keys:
        dataset, score, match_reason, apply_mode
    or None if no dataset scores >= THRESHOLD_IGNORE.

    Parameters
    ----------
    update_usage:
        If True, increment usage_count and set last_used_at on the winner.
        Callers in the hot OCR path should pass False (default) to avoid
        write overhead; the pipeline integration (Phase 5) can pass True.
    """
    if not db:
        return None

    normalised_module = _normalise_module(module)

    try:
        clauses = [
            TrainingDataset.is_active.is_(True),
        ]
        # approved column added in Phase 2A — guard with getattr for safety
        # (old SQLite test rows may not have it)
        try:
            clauses.append(
                (TrainingDataset.approved.is_(True))
                | (TrainingDataset.approved.is_(None))
            )
        except Exception:
            pass  # column absent on pre-migration SQLite — ignore

        if normalised_module and normalised_module != "general":
            clauses.append(
                (TrainingDataset.module == normalised_module)
                | (TrainingDataset.module.in_(_VALID_MODULES))
            )

        stmt = select(TrainingDataset).where(*clauses)
        result = await db.execute(stmt)
        candidates: list[TrainingDataset] = result.scalars().all()
    except Exception as exc:
        logger.warning("match_training_dataset: DB query failed: {}", exc)
        return None

    if not candidates:
        return None

    best_score = 0.0
    best_dataset: TrainingDataset | None = None
    for dataset in candidates:
        s = score_template_similarity(raw_text, dataset, document_type, company_info)
        if s > best_score:
            best_score = s
            best_dataset = dataset

    if best_score < THRESHOLD_IGNORE or best_dataset is None:
        logger.debug(
            "match_training_dataset: no match above threshold "
            "(best={:.3f} threshold={:.3f})",
            best_score, THRESHOLD_IGNORE,
        )
        return None

    apply_mode = "auto" if best_score >= THRESHOLD_APPLY else "suggest"

    if update_usage:
        try:
            best_dataset.usage_count = (getattr(best_dataset, "usage_count", 0) or 0) + 1
            best_dataset.last_used_at = datetime.now(timezone.utc)
            await db.commit()
        except Exception as exc:
            logger.warning("match_training_dataset: usage_count update failed: {}", exc)
            await db.rollback()

    return {
        "dataset": best_dataset,
        "score": best_score,
        "match_reason": f"similarity_{apply_mode}",
        "apply_mode": apply_mode,
        "dataset_id": str(best_dataset.id),
        "dataset_name": getattr(best_dataset, "dataset_name", ""),
        "training_match_score": best_score,
        "training_template_matched": True,
    }


# ── Training field applicator ──────────────────────────────────────────────

def _source_priority(field: dict[str, Any]) -> int:
    """Return the write-priority of an existing mapped field (higher = protect)."""
    source = str(field.get("source") or "").lower()
    method = str(field.get("method") or "").lower()
    if source in _PROTECTED_SOURCES or method in _PROTECTED_METHODS:
        return 100
    # correction_memory writes source="correction_memory"
    if source == "correction_memory":
        return 85
    # regex mapper writes source="" or source="regex"
    if source in {"regex", "template", ""} or method in {"regex", "near_label"}:
        return 70
    # Gemini / AI
    if source in {"gemini", "ai"} or method in {"gemini", "llm"}:
        return 60
    return 40  # unknown / ocr_guess


def apply_training_to_mapped_fields(
    mapped_result: dict[str, Any],
    matched_dataset: dict[str, Any] | TrainingDataset,
) -> dict[str, Any]:
    """Merge training dataset field mappings into mapped_result["mapped_fields"].

    Rules
    -----
    • Training writes source="training", method="training_template", priority 80.
    • Does NOT overwrite fields with priority >= 85
      (manual_correction, correction_memory).
    • Overwrites weak fields (regex / AI / missing / low-confidence).
    • Returns the updated mapped_result plus metadata keys:
        training_applied_fields  list[str]
        training_conflicts       list[dict]
        training_template_matched bool
        training_match_score     float
        training_template_name   str
    """
    # Unwrap if caller passed the full match dict from match_training_dataset
    if isinstance(matched_dataset, dict) and "dataset" in matched_dataset:
        match_meta = matched_dataset
        dataset: TrainingDataset = matched_dataset["dataset"]
    else:
        dataset = matched_dataset  # type: ignore[assignment]
        match_meta = {}

    if dataset is None:
        return {**mapped_result, "training_applied_fields": [], "training_conflicts": [],
                "training_template_matched": False, "training_match_score": 0.0,
                "training_template_name": ""}

    mappings: list[dict[str, Any]] = list(getattr(dataset, "mappings_json", None) or [])
    mapped_fields: dict[str, Any] = dict(mapped_result.get("mapped_fields") or {})

    dataset_confidence = float(getattr(dataset, "confidence", None) or 0.90)
    dataset_name = str(getattr(dataset, "dataset_name", "") or "")
    dataset_id = str(getattr(dataset, "id", "") or "")
    match_score = float(match_meta.get("score", match_meta.get("training_match_score", 0.0)) or 0.0)

    applied: list[str] = []
    conflicts: list[dict[str, Any]] = []

    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue

        # Resolve target field name (support multiple key variants)
        target = (
            mapping.get("target_erp_name")
            or mapping.get("target_field")
            or mapping.get("erp_field_name")
            or mapping.get("target")
            or mapping.get("canonical_name")
        )
        if not target:
            continue
        target = str(target).strip()

        # Training value: use sample_value as the learned reference value
        trained_value = str(
            mapping.get("sample_value")
            or mapping.get("value")
            or ""
        ).strip()

        # Skip if no learned value
        if not trained_value:
            continue

        trained_confidence = float(mapping.get("confidence") or dataset_confidence or 0.90)
        trained_entry: dict[str, Any] = {
            "value": trained_value,
            "confidence": trained_confidence,
            "method": "training_template",
            "source": "training",
            "training_dataset_id": dataset_id,
            "training_dataset_name": dataset_name,
            "status": "AUTO_FILLED",
        }

        existing = mapped_fields.get(target)
        if existing is None:
            # Field not present → write freely
            mapped_fields[target] = trained_entry
            applied.append(target)
            continue

        if not isinstance(existing, dict):
            # Scalar value — wrap and overwrite (low priority)
            existing = {"value": str(existing), "confidence": 0.0, "method": "ocr_guess", "source": ""}

        existing_priority = _source_priority(existing)
        existing_value = str(existing.get("value") or "").strip()
        existing_confidence = float(existing.get("confidence") or 0.0)

        # Protect high-priority fields
        if existing_priority >= 85:
            conflicts.append({
                "field": target,
                "trained_value": trained_value,
                "existing_value": existing_value,
                "existing_source": existing.get("source", ""),
                "action": "skipped_protected",
            })
            continue

        # Overwrite if: field is empty, or existing is weak (low confidence / regex/AI)
        should_overwrite = (
            not existing_value
            or existing_confidence < 0.70
            or existing_priority < 80
        )
        if should_overwrite:
            # Preserve audit trail — keep original value in _prev_
            merged = {**existing, **trained_entry}
            if existing_value and existing_value != trained_value:
                merged["_prev_value"] = existing_value
                merged["_prev_source"] = existing.get("source", "")
                conflicts.append({
                    "field": target,
                    "trained_value": trained_value,
                    "existing_value": existing_value,
                    "existing_source": existing.get("source", ""),
                    "action": "overwritten",
                })
            mapped_fields[target] = merged
            applied.append(target)
        else:
            # Existing value is high-confidence regex/trained — keep, record info
            conflicts.append({
                "field": target,
                "trained_value": trained_value,
                "existing_value": existing_value,
                "existing_source": existing.get("source", ""),
                "action": "kept_existing",
            })

    updated_result = {
        **mapped_result,
        "mapped_fields": mapped_fields,
        "training_applied_fields": applied,
        "training_conflicts": conflicts,
        "training_template_matched": True,
        "training_match_score": match_score,
        "training_template_name": dataset_name,
        "training_dataset_id": dataset_id,
    }
    logger.info(
        "apply_training_to_mapped_fields: dataset={!r} applied={} conflicts={}",
        dataset_name, len(applied), len(conflicts),
    )
    return updated_result
