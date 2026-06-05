"""template_engine.py — Load active company template + field mappings.

Supports field mapping methods:
  fixed_from_company_db  — value pulled directly from Company master record
  regex                  — regex pattern applied to OCR text
  near_label             — text in proximity to a label keyword
  section_block          — text inside a named section
  table_column           — value from a detected table column
  manual_required        — must be entered by human; never auto-filled
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company, CompanyTemplate, FieldMapping

# ── Company DB field map (for fixed_from_company_db method) ─────────────────
_COMPANY_DB_FIELDS: dict[str, str] = {
    "company_name":         "company_name",
    "gst_number":           "gst_number",
    "iec_code":             "iec_code",
    "address":              "address",
    "bank_name":            "bank_name",
    "account_number":       "account_number",
    "ifsc_code":            "ifsc_code",
    "swift_code":           "swift_code",
    "default_currency":     "default_currency",
    "default_tax_type":     "default_tax_type",
    "default_port_loading": "default_port_loading",
    "default_port_discharge": "default_port_discharge",
}


# ── Result types ─────────────────────────────────────────────────────────────

@dataclass
class MappedFieldResult:
    field_name:  str
    value:       str
    confidence:  float
    method:      str
    source:      str       = "template"
    evidence:    str       = ""
    status:      str       = "AUTO_FILLED"   # AUTO_FILLED | NEED_REVIEW | MISSING | VALIDATION_ERROR | MANUAL_REQUIRED


@dataclass
class TemplateEngineResult:
    template_id:    Optional[uuid.UUID]
    template_name:  Optional[str]
    document_type:  str
    fields:         list[MappedFieldResult] = field(default_factory=list)
    missing_fields: list[str]               = field(default_factory=list)
    template_found: bool                    = False
    company_id:     Optional[uuid.UUID]     = None


# ── Text extraction helpers ───────────────────────────────────────────────────

def _apply_regex(pattern: str, text: str) -> tuple[str, str]:
    """Return (value, evidence_snippet). Empty string if no match."""
    try:
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if m:
            groups = m.groups()
            value = groups[0].strip() if groups else m.group(0).strip()
            start = max(0, m.start() - 30)
            end   = min(len(text), m.end() + 30)
            return value, text[start:end].replace("\n", " ").strip()
    except re.error:
        pass
    return "", ""


def _near_label(label: str, text: str, window: int = 120) -> tuple[str, str]:
    """Find text near a label keyword within `window` characters."""
    pattern = re.escape(label) + r"[\s:.\-]*([^\n]{1,60})"
    try:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            value = m.group(1).strip().rstrip(",;:")
            return value, m.group(0).strip()
    except re.error:
        pass
    return "", ""


def _section_block(section_name: str, text: str) -> tuple[str, str]:
    """Extract the first ~200 chars after a section header."""
    pattern = re.escape(section_name) + r"\s*[:\-]?\s*\n?(.{1,200})"
    try:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if m:
            block = m.group(1).strip()[:200]
            return block, block
    except re.error:
        pass
    return "", ""


def _table_column(column_label: str, text: str) -> tuple[str, str]:
    """Naive: find column header then grab next whitespace-separated token on the same/next line."""
    pattern = re.escape(column_label) + r"[\s|:]*([^\n\|]{1,60})"
    try:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            value = m.group(1).strip()
            return value, m.group(0).strip()
    except re.error:
        pass
    return "", ""


def _apply_fixed_from_company(field_name: str, company: Company) -> tuple[str, str]:
    """Pull static value from Company master record."""
    attr = _COMPANY_DB_FIELDS.get(field_name)
    if attr:
        value = str(getattr(company, attr, "") or "")
        return value, "company_master_db"
    return "", ""


# ── Main engine ───────────────────────────────────────────────────────────────

async def apply_template(
    company_id:    uuid.UUID,
    document_type: str,
    raw_text:      str,
    db:            AsyncSession,
) -> TemplateEngineResult:
    """
    Load the active template for (company_id, document_type) and apply
    all field mappings to raw_text.
    """
    # 1. Load company master
    company: Optional[Company] = await db.get(Company, company_id)

    # 2. Find active template
    result = await db.execute(
        select(CompanyTemplate)
        .where(
            CompanyTemplate.company_id  == company_id,
            CompanyTemplate.document_type == document_type,
            CompanyTemplate.is_active   == True,  # noqa: E712
        )
        .order_by(CompanyTemplate.template_version.desc())
        .limit(1)
    )
    template: Optional[CompanyTemplate] = result.scalar_one_or_none()

    if not template:
        return TemplateEngineResult(
            template_id=None,
            template_name=None,
            document_type=document_type,
            template_found=False,
            company_id=company_id,
        )

    # 3. Load field mappings (ordered by priority)
    fm_result = await db.execute(
        select(FieldMapping)
        .where(FieldMapping.template_id == template.id)
        .order_by(FieldMapping.priority, FieldMapping.field_name)
    )
    mappings: list[FieldMapping] = fm_result.scalars().all()

    mapped_fields: list[MappedFieldResult] = []
    missing: list[str] = []

    for fm in mappings:
        value, evidence = "", ""
        method_used = fm.method

        if fm.method == "fixed_from_company_db":
            if company:
                value, evidence = _apply_fixed_from_company(fm.field_name, company)

        elif fm.method == "regex" and fm.regex_pattern:
            value, evidence = _apply_regex(fm.regex_pattern, raw_text)

        elif fm.method == "near_label" and fm.label_text:
            value, evidence = _near_label(fm.label_text, raw_text)

        elif fm.method == "section_block" and (fm.section_name or fm.label_text):
            key = fm.section_name or fm.label_text or ""
            value, evidence = _section_block(key, raw_text)

        elif fm.method == "table_column" and fm.label_text:
            value, evidence = _table_column(fm.label_text, raw_text)

        elif fm.method == "manual_required":
            mapped_fields.append(MappedFieldResult(
                field_name=fm.field_name,
                value="",
                confidence=0.0,
                method="manual_required",
                status="MANUAL_REQUIRED",
                evidence="",
            ))
            missing.append(fm.field_name)
            continue

        # Determine status and confidence
        if value:
            confidence = 0.95 if fm.method == "fixed_from_company_db" else 0.80
            status = "AUTO_FILLED"
        else:
            confidence = 0.0
            status = "MISSING"
            if fm.required:
                missing.append(fm.field_name)

        mapped_fields.append(MappedFieldResult(
            field_name=fm.field_name,
            value=value,
            confidence=confidence,
            method=method_used,
            source="template" if fm.method != "fixed_from_company_db" else "company_db",
            evidence=evidence[:120],
            status=status,
        ))

    return TemplateEngineResult(
        template_id=template.id,
        template_name=template.template_name,
        document_type=document_type,
        fields=mapped_fields,
        missing_fields=missing,
        template_found=True,
        company_id=company_id,
    )
