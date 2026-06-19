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
    else:
        # No signature stored yet — use source_key labels from mappings_json
        # to check if OCR text contains those field labels (structural match).
        try:
            mappings = list(getattr(dataset, "mappings_json", None) or [])
            label_keys: list[str] = []
            for m in mappings:
                if not isinstance(m, dict):
                    continue
                lbl = (m.get("source_label") or m.get("source_key") or
                       m.get("normalized_source_key") or "").lower()
                if lbl:
                    label_keys.append(lbl)
            if label_keys:
                cleaned_ocr = _clean(str(raw_text or "")).lower()
                hits = sum(1 for lbl in label_keys if lbl in cleaned_ocr)
                hit_ratio = hits / max(len(label_keys), 1)
                # Give up to 20 pts based on how many of dataset's field labels
                # appear in the incoming OCR text (layout fingerprint substitute).
                score += int(hit_ratio * 20)
        except Exception:
            pass

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
THRESHOLD_SUGGEST = 0.70  # suggest training but mark needs_review (raised from 0.50)
THRESHOLD_IGNORE = 0.60   # below this → return None (raised from 0.40)

_VALID_MODULES = {
    "sales_order", "purchase_order", "sales", "purchase",
    "commercial_invoice", "export_invoice", "grn", "invoice", "general",
}


def _module_family(module: str | None) -> str:
    norm = _normalise_module(module)
    if "purchase" in norm or norm in {"grn"}:
        return "purchase"
    if "sales" in norm or norm in {"commercial_invoice", "export_invoice"}:
        return "sales"
    if norm == "invoice":
        return "sales"
    return "general"


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
            is_purchase = "purchase" in normalised_module
            is_sales = "sales" in normalised_module
            if is_purchase:
                clauses.append(TrainingDataset.module.in_({"purchase_order", "purchase", "general"}))
            elif is_sales:
                clauses.append(TrainingDataset.module.in_({"sales_order", "sales", "general"}))
            else:
                clauses.append(TrainingDataset.module == normalised_module)

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
    requested_family = _module_family(normalised_module)
    for dataset in candidates:
        dataset_family = _module_family(getattr(dataset, "module", None))
        if (
            requested_family in {"sales", "purchase"}
            and dataset_family in {"sales", "purchase"}
            and requested_family != dataset_family
        ):
            continue
        s = score_template_similarity(raw_text, dataset, document_type, company_info)
        if s > best_score:
            best_score = s
            best_dataset = dataset

    if best_score < THRESHOLD_SUGGEST or best_dataset is None:
        logger.debug(
            "match_training_dataset: no match above threshold "
            "(best={:.3f} threshold={:.3f})",
            best_score, THRESHOLD_SUGGEST,
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


def extract_value_for_label_from_text(raw_text: str, label: str) -> str | None:
    if not raw_text or not label:
        return None
    normalized_label = label.strip()
    if not normalized_label:
        return None

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    label_clean = normalized_label.rstrip(":-| ")
    if not label_clean:
        return None

    escaped_label = re.escape(label_clean)

    # 1. Try same line match
    pattern = r"(?i)\b" + escaped_label + r"\b\s*[:\-\|\s]\s*(.+)"
    for line in lines:
        m = re.search(pattern, line)
        if m:
            val = m.group(1).strip(" :-|")
            if val:
                val = re.split(r"\s{2,}|\t", val)[0].strip()
                val = re.split(r"(?i)\s+(?:date|gstin|tin|pan|cin|bill|ship|to)\b", val)[0].strip()
                val = val.strip(" :-|")
                if val:
                    return val

    # Try loose same line
    pattern_loose = r"(?i)" + escaped_label + r"\s*[:\-\|\s]\s*(.+)"
    for line in lines:
        m = re.search(pattern_loose, line)
        if m:
            val = m.group(1).strip(" :-|")
            if val:
                val = re.split(r"\s{2,}|\t", val)[0].strip()
                val = re.split(r"(?i)\s+(?:date|gstin|tin|pan|cin|bill|ship|to)\b", val)[0].strip()
                val = val.strip(" :-|")
                if val:
                    return val

    # 2. Try next line match
    for i, line in enumerate(lines):
        if line.lower() == label_clean.lower() or label_clean.lower() in line.lower():
            if i + 1 < len(lines):
                val = lines[i+1].strip()
                if val and len(val) < 80 and not any(k in val.lower() for k in ["invoice", "date", "total", "gstin", "buyer", "seller", "consignee"]):
                    return val
    return None


def is_dynamic_field(field_name: str) -> bool:
    name = str(field_name).lower().strip()
    dynamic_keywords = [
        "order_no", "date", "tax", "total", "po_number", "round_off", "charges",
        "qty", "price", "rate", "amount", "number", "description", "hsn", "product",
        "invoice_no", "invoice_num", "po_no", "subtotal", "cgst", "sgst", "igst",
        "quantity", "ref", "reference", "discount", "item", "row"
    ]
    for kw in dynamic_keywords:
        if kw in name:
            return True
    return False


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

    raw_text = str(mapped_result.get("raw_text") or "").strip()

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

        # Dynamic fields must not come from training dataset
        if is_dynamic_field(target):
            continue

        # Training value: use sample_value as the learned reference value
        trained_value = str(
            mapping.get("sample_value")
            or mapping.get("value")
            or ""
        ).strip()

        # Skip if no learned value
        if not trained_value:
            continue

        final_value = ""
        source_evidence = "training_template"

        if raw_text:
            src_key = mapping.get("source_key") or mapping.get("source_label")
            extracted_val = extract_value_for_label_from_text(raw_text, src_key) if src_key else None

            if not extracted_val and mapping.get("source_label"):
                extracted_val = extract_value_for_label_from_text(raw_text, mapping["source_label"])

            if extracted_val:
                final_value = extracted_val
                source_evidence = f"extracted_via_training_label: {src_key}"
            else:
                existing = mapped_fields.get(target)
                if existing and isinstance(existing, dict) and str(existing.get("value") or "").strip():
                    final_value = str(existing.get("value")).strip()
                    source_evidence = existing.get("source_evidence") or "existing_regex_value"
                elif existing and not isinstance(existing, dict) and str(existing).strip():
                    final_value = str(existing).strip()
                    source_evidence = "existing_regex_value"
                else:
                    final_value = trained_value
                    source_evidence = "training_sample_value_fallback"
        else:
            final_value = trained_value
            source_evidence = "training_sample_value_test"

        if not final_value:
            continue

        trained_confidence = float(mapping.get("confidence") or dataset_confidence or 0.90)
        trained_entry: dict[str, Any] = {
            "value": final_value,
            "confidence": trained_confidence,
            "method": "training_template",
            "source": "training",
            "training_dataset_id": dataset_id,
            "training_dataset_name": dataset_name,
            "status": "AUTO_FILLED",
            "source_evidence": source_evidence,
        }
        if source_evidence in {"training_sample_value_fallback", "training_sample_value_test"}:
            trained_entry["value_source"] = "training_dataset"

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
                "trained_value": final_value,
                "existing_value": existing_value,
                "existing_source": existing.get("source", ""),
                "action": "skipped_protected",
            })
            continue

        # Training data must never overwrite valid OCR values (non-empty current values)
        should_overwrite = not existing_value
        if should_overwrite:
            # Preserve audit trail — keep original value in _prev_
            merged = {**existing, **trained_entry}
            if existing_value and existing_value != final_value:
                merged["_prev_value"] = existing_value
                merged["_prev_source"] = existing.get("source", "")
                conflicts.append({
                    "field": target,
                    "trained_value": final_value,
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
                "trained_value": final_value,
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


def generate_field_level_trace(
    doc_type: str,
    raw_mapped_fields: dict[str, Any],     # regex/AI fields before training merge
    validation_result: dict[str, Any],     # final validation result after training merge
    matched_dataset_info: dict[str, Any] | None,  # the return value of match_training_dataset
    applied_fields: list[str],             # fields that were successfully applied
    conflicts: list[dict[str, Any]],        # conflicts from training merge
) -> list[dict[str, Any]]:
    from app.services.company_form_mapper import _DOC_TYPE_FIELDS

    # Get active fields for this doc_type
    target_fields = _DOC_TYPE_FIELDS.get((doc_type or "general").lower(), _DOC_TYPE_FIELDS.get("general", []))

    dataset_name = matched_dataset_info.get("dataset_name") if matched_dataset_info else None
    dataset_id = matched_dataset_info.get("dataset_id") if matched_dataset_info else None
    dataset_obj = matched_dataset_info.get("dataset") if matched_dataset_info else None

    mappings = list(getattr(dataset_obj, "mappings_json", None) or []) if dataset_obj else []
    if not mappings and isinstance(dataset_obj, dict):
        mappings = list(dataset_obj.get("mappings_json") or [])

    trace_list = []

    # Prefix for DOM IDs
    is_sales = doc_type.lower() in {"sales", "invoice", "export_invoice", "commercial_invoice", "tax_invoice"}
    dom_prefix = "sf_" if is_sales else "f_"

    # Build maps for mapping lookup and conflict lookup
    mapping_by_target = {}
    for m in mappings:
        if isinstance(m, dict):
            target = (
                m.get("target_erp_name")
                or m.get("target_field")
                or m.get("erp_field_name")
                or m.get("target")
                or m.get("canonical_name")
            )
            if target:
                mapping_by_target[str(target).strip()] = m

    conflict_by_field = {c["field"]: c for c in conflicts if isinstance(c, dict) and "field" in c}

    # We want to trace all possible target fields
    for field in target_fields:
        # 1. source value (value before training was merged)
        src_val = raw_mapped_fields.get(field)
        src_value_str = ""
        if src_val is not None:
            if isinstance(src_val, dict):
                src_value_str = str(src_val.get("value") or "").strip()
            else:
                src_value_str = str(src_val).strip()

        # 2. selected dataset
        selected_ds = dataset_name or "None"

        # 3. mapping found
        m_found = mapping_by_target.get(field)

        # 4. final target ID
        final_target_id = dom_prefix + field

        # 5. target exists
        target_exists = True

        # 6. validation status
        val_status = "VALID"
        field_warnings = []
        for w in validation_result.get("warnings", []):
            if f"'{field}'" in w or f" {field}:" in w or w.startswith(f"{field}:"):
                field_warnings.append(w)
        for e in validation_result.get("errors", []):
            if f"'{field}'" in e or f" {field}:" in e or e.startswith(f"{field}:"):
                field_warnings.append(e)
        if field_warnings:
            val_status = "; ".join(field_warnings)

        # 7. applied/skipped result & 8. exact skip reason
        applied_skipped = "skipped"
        skip_reason = ""

        if not dataset_name:
            applied_skipped = "no_training_matched"
            skip_reason = "No matching training layout dataset found for this document"
        elif field in applied_fields:
            applied_skipped = "applied"
            skip_reason = ""
        else:
            if field in conflict_by_field:
                action = conflict_by_field[field].get("action")
                if action == "skipped_protected":
                    skip_reason = "skipped_protected (priority >= 85)"
                elif action == "kept_existing":
                    skip_reason = "kept_existing (high-confidence value already present)"
                else:
                    skip_reason = action or "skipped"
            elif not m_found:
                skip_reason = "no_mapping_found_in_dataset"
            else:
                trained_value = m_found.get("sample_value") or m_found.get("value") or ""
                if not trained_value:
                    skip_reason = "no_source_value_in_trained_mapping"
                else:
                    skip_reason = "unknown"

        trace_list.append({
            "field": field,
            "source_value": src_value_str,
            "selected_dataset": selected_ds,
            "mapping_found": m_found,
            "final_target_id": final_target_id,
            "target_exists": target_exists,
            "validation_status": val_status,
            "applied_skipped": applied_skipped,
            "skip_reason": skip_reason
        })

    # For any fields that are in mapped fields but not in target_fields
    for field in sorted(raw_mapped_fields.keys()):
        if field in {"item_rows", "items"}:
            continue
        if any(t["field"] == field for t in trace_list):
            continue

        src_val = raw_mapped_fields[field]
        src_value_str = ""
        if isinstance(src_val, dict):
            src_value_str = str(src_val.get("value") or "").strip()
        else:
            src_value_str = str(src_val).strip()

        selected_ds = dataset_name or "None"
        m_found = mapping_by_target.get(field)
        final_target_id = dom_prefix + field
        target_exists = False

        val_status = "VALID"
        field_warnings = []
        for w in validation_result.get("warnings", []):
            if f"'{field}'" in w or f" {field}:" in w or w.startswith(f"{field}:"):
                field_warnings.append(w)
        for e in validation_result.get("errors", []):
            if f"'{field}'" in e or f" {field}:" in e or e.startswith(f"{field}:"):
                field_warnings.append(e)
        if field_warnings:
            val_status = "; ".join(field_warnings)

        applied_skipped = "skipped"
        skip_reason = "field_not_in_schema"

        if not dataset_name:
            applied_skipped = "no_training_matched"
            skip_reason = "No matching training layout dataset found for this document"
        elif field in applied_fields:
            applied_skipped = "applied"
            skip_reason = ""
        elif field in conflict_by_field:
            action = conflict_by_field[field].get("action")
            if action == "skipped_protected":
                skip_reason = "skipped_protected (priority >= 85)"
            elif action == "kept_existing":
                skip_reason = "kept_existing (high-confidence value already present)"
            else:
                skip_reason = action or "skipped"
        elif not m_found:
            skip_reason = "no_mapping_found_in_dataset"

        trace_list.append({
            "field": field,
            "source_value": src_value_str,
            "selected_dataset": selected_ds,
            "mapping_found": m_found,
            "final_target_id": final_target_id,
            "target_exists": target_exists,
            "validation_status": val_status,
            "applied_skipped": applied_skipped,
            "skip_reason": skip_reason
        })

    for trace in trace_list:
        logger.info(
            "FieldLevelTrace: field={} source_value={!r} selected_dataset={!r} applied_skipped={} skip_reason={!r} target_exists={} final_target_id={} validation_status={}",
            trace["field"], trace["source_value"], trace["selected_dataset"],
            trace["applied_skipped"], trace["skip_reason"], trace["target_exists"],
            trace["final_target_id"], trace["validation_status"]
        )

    return trace_list
