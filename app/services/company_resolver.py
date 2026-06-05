"""Resolve document company identity to Phase 1 company master rows."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company


GST_RE = re.compile(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b", re.I)
GST_LABEL_RE = re.compile(
    r"\b(?:GST(?:IN)?|GST\s*NO\.?)\s*[:#-]?\s*([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b",
    re.I,
)
IEC_LABEL_RE = re.compile(r"\bIEC(?:\s*CODE)?\s*[:#-]?\s*([A-Z0-9]{10})\b", re.I)
K2_RE = re.compile(r"\bK\s*2\s+FOOTPRINTS\b", re.I)


def normalize_gst(value: str) -> str:
    """Return an uppercase GSTIN-like token without separators."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def normalize_company_name(value: str) -> str:
    """Normalize company names for exact-ish matching."""
    cleaned = re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper())
    tokens = [token for token in cleaned.split() if token not in {"PVT", "LTD", "LIMITED", "PRIVATE", "LLP"}]
    return " ".join(tokens)


def _canonical_value(canonical_fields: dict[str, Any], field_name: str) -> str:
    raw = (canonical_fields or {}).get(field_name)
    if isinstance(raw, dict):
        return str(raw.get("value") or "").strip()
    return str(raw or "").strip()


def _valid_gst(value: str) -> bool:
    return bool(GST_RE.fullmatch(normalize_gst(value)))


def _extract_first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text or "")
    return match.group(1).upper() if match else ""


def extract_company_identity(
    raw_text: str,
    canonical_fields: dict,
    detected_company: dict | None = None,
) -> dict:
    """Extract the best available company identity from fields, detector, and OCR text."""
    detected_company = detected_company if isinstance(detected_company, dict) else {}
    raw_text = raw_text or ""

    gst_number = normalize_gst(
        _canonical_value(canonical_fields, "gst_number")
        or str(detected_company.get("gst_number") or "")
        or _extract_first(GST_LABEL_RE, raw_text)
        or _extract_first(GST_RE, raw_text)
    )
    iec_code = re.sub(
        r"[^A-Z0-9]",
        "",
        (
            _canonical_value(canonical_fields, "iec_code")
            or str(detected_company.get("iec_code") or "")
            or _extract_first(IEC_LABEL_RE, raw_text)
        ).upper(),
    )
    if not iec_code and _valid_gst(gst_number):
        iec_code = gst_number[2:12]

    company_name = (
        _canonical_value(canonical_fields, "company_name")
        or str(detected_company.get("company_name") or "")
    ).strip()
    if K2_RE.search(raw_text) or K2_RE.search(company_name):
        company_name = "K2 FOOTPRINTS"

    return {
        "company_name": company_name,
        "normalized_company_name": normalize_company_name(company_name),
        "gst_number": gst_number if _valid_gst(gst_number) else gst_number,
        "iec_code": iec_code,
        "has_valid_gst": _valid_gst(gst_number),
    }


def _response(
    *,
    company_id: str | None = None,
    company_name: str | None = None,
    gst_number: str | None = None,
    iec_code: str | None = None,
    match_method: str = "not_found",
    confidence: float = 0.0,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "company_id": company_id,
        "company_name": company_name,
        "gst_number": gst_number,
        "iec_code": iec_code,
        "match_method": match_method,
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "warnings": list(warnings or []),
    }


def _company_payload(company: Company, match_method: str, confidence: float, warnings: list[str]) -> dict:
    return _response(
        company_id=str(company.id),
        company_name=getattr(company, "company_name", None),
        gst_number=getattr(company, "gst_number", None),
        iec_code=getattr(company, "iec_code", None),
        match_method=match_method,
        confidence=confidence,
        warnings=warnings,
    )


async def _match_by_column(db: AsyncSession, column_name: str, value: str) -> tuple[Company | None, list[str]]:
    warnings: list[str] = []
    column = getattr(Company, column_name, None)
    if column is None or not value:
        return None, warnings
    result = await db.execute(select(Company).where(column == value).limit(2))
    rows = result.scalars().all()
    if len(rows) > 1:
        warnings.append(f"Duplicate company {column_name} match; using first active row")
    return (rows[0] if rows else None), warnings


async def resolve_company_id(
    db: AsyncSession,
    raw_text,
    canonical_fields,
    detected_company=None,
) -> dict:
    """Resolve a company id, guarded against missing DB/table/columns."""
    warnings: list[str] = []
    identity = extract_company_identity(str(raw_text or ""), canonical_fields or {}, detected_company)
    gst_number = identity["gst_number"]
    iec_code = identity["iec_code"]
    company_name = identity["company_name"]
    normalized_name = identity["normalized_company_name"]

    try:
        if identity["has_valid_gst"]:
            company, match_warnings = await _match_by_column(db, "gst_number", gst_number)
            warnings.extend(match_warnings)
            if company:
                return _company_payload(company, "gst_number", 0.99, warnings)
        elif gst_number:
            warnings.append("Invalid GST format; skipped GST company match")

        company, match_warnings = await _match_by_column(db, "iec_code", iec_code)
        warnings.extend(match_warnings)
        if company:
            return _company_payload(company, "iec_code", 0.94, warnings)

        if normalized_name:
            result = await db.execute(select(Company))
            companies = result.scalars().all()
            matches = [
                company for company in companies
                if normalize_company_name(getattr(company, "company_name", "")) == normalized_name
            ]
            if len(matches) > 1:
                warnings.append("Duplicate company_name match; using first row")
            if matches:
                return _company_payload(matches[0], "company_name", 0.88, warnings)

        strong_identity = identity["has_valid_gst"] or bool(company_name and iec_code)
        if strong_identity and company_name and hasattr(Company, "company_name"):
            company = Company(
                company_name=company_name,
                gst_number=gst_number if identity["has_valid_gst"] and hasattr(Company, "gst_number") else None,
                iec_code=iec_code if hasattr(Company, "iec_code") else None,
                is_active=True if hasattr(Company, "is_active") else None,
            )
            db.add(company)
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
                if identity["has_valid_gst"]:
                    company, match_warnings = await _match_by_column(db, "gst_number", gst_number)
                    warnings.extend(match_warnings)
                    if company:
                        return _company_payload(company, "gst_number", 0.99, warnings)
                raise
            payload = _company_payload(company, "auto_created", 0.9, warnings)
            await db.commit()
            warnings.append("Company auto-created from document identity")
            payload["warnings"] = warnings
            return payload

        return _response(
            company_name=company_name or None,
            gst_number=gst_number or None,
            iec_code=iec_code or None,
            warnings=warnings,
        )
    except Exception as exc:
        warnings.append(f"Company resolution failed: {exc}")
        return _response(
            company_name=company_name or None,
            gst_number=gst_number or None,
            iec_code=iec_code or None,
            warnings=warnings,
        )
