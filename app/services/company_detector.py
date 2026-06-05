"""company_detector.py — Detect company from OCR text.

Detection priority:
  1. GST number exact match (DB lookup)
  2. IEC code exact match (DB lookup)
  3. Company name fuzzy match (token set ratio)

Returns CompanyDetectionResult with company_id, confidence, match_method.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company

# ── Regex patterns ────────────────────────────────────────────────────────────
_GST_RE  = re.compile(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b")
_IEC_RE  = re.compile(r"\bIEC[:\s#]*([A-Z0-9]{10})\b", re.I)
_IEC2_RE = re.compile(r"\b([0-9]{10})\b")   # bare 10-digit IEC fallback

# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class CompanyDetectionResult:
    company_id:    Optional[uuid.UUID] = None
    company_name:  Optional[str]       = None
    match_method:  str                 = "none"          # gst | iec | name_fuzzy | none
    confidence:    float               = 0.0             # 0.0–1.0
    matched_value: Optional[str]       = None            # the token that matched
    is_known:      bool                = False
    candidates:    list                = field(default_factory=list)


# ── Text extraction helpers ──────────────────────────────────────────────────

def _extract_gst_candidates(text: str) -> list[str]:
    return [m.upper() for m in _GST_RE.findall(text)]


def _extract_iec_candidates(text: str) -> list[str]:
    hits: list[str] = []
    for m in _IEC_RE.findall(text):
        hits.append(m.upper())
    # Only use bare 10-digit if no labelled IEC found
    if not hits:
        for m in _IEC2_RE.findall(text):
            hits.append(m)
    return hits


def _tokenize(name: str) -> set[str]:
    """Lower-case word tokens, strip punctuation."""
    return set(re.sub(r"[^a-z0-9 ]", " ", name.lower()).split())


def _name_overlap_score(db_name: str, text: str) -> float:
    """Token-overlap ratio between company name and OCR text."""
    tokens = _tokenize(db_name)
    if not tokens:
        return 0.0
    text_tokens = _tokenize(text[:4000])   # cap to avoid O(n²) on long docs
    stopwords = {"pvt", "ltd", "limited", "private", "co", "inc", "llp", "and", "the", "of"}
    sig_tokens = tokens - stopwords
    if not sig_tokens:
        sig_tokens = tokens
    matched = sig_tokens & text_tokens
    score = len(matched) / len(sig_tokens)
    # Boost if 2+ tokens matched (reduces false positives)
    if len(matched) >= 2:
        score = min(1.0, score + 0.15)
    return round(score, 3)


# ── Main detector ─────────────────────────────────────────────────────────────

async def detect_company(
    raw_text: str,
    db: AsyncSession,
) -> CompanyDetectionResult:
    """
    Detect company from OCR raw_text by querying the companies table.
    Runs three passes: GST → IEC → name fuzzy.
    """
    # Load all active companies (table is small — <1000 rows in typical deployment)
    result = await db.execute(
        select(Company).where(Company.is_active == True)  # noqa: E712
    )
    companies: list[Company] = result.scalars().all()

    if not companies:
        return CompanyDetectionResult()

    # ── Pass 1: GST exact match ───────────────────────────────────────────────
    gst_candidates = _extract_gst_candidates(raw_text)
    if gst_candidates:
        gst_index = {(c.gst_number or "").upper(): c for c in companies if c.gst_number}
        for gst in gst_candidates:
            if gst in gst_index:
                c = gst_index[gst]
                return CompanyDetectionResult(
                    company_id=c.id,
                    company_name=c.company_name,
                    match_method="gst",
                    confidence=0.98,
                    matched_value=gst,
                    is_known=True,
                )

    # ── Pass 2: IEC exact match ───────────────────────────────────────────────
    iec_candidates = _extract_iec_candidates(raw_text)
    if iec_candidates:
        iec_index = {(c.iec_code or "").upper(): c for c in companies if c.iec_code}
        for iec in iec_candidates:
            if iec.upper() in iec_index:
                c = iec_index[iec.upper()]
                return CompanyDetectionResult(
                    company_id=c.id,
                    company_name=c.company_name,
                    match_method="iec",
                    confidence=0.92,
                    matched_value=iec,
                    is_known=True,
                )

    # ── Pass 3: Name fuzzy (token overlap) ───────────────────────────────────
    scored: list[tuple[float, Company]] = []
    for c in companies:
        score = _name_overlap_score(c.company_name, raw_text)
        if score >= 0.55:
            scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)

    if scored:
        top_score, top_company = scored[0]
        return CompanyDetectionResult(
            company_id=top_company.id,
            company_name=top_company.company_name,
            match_method="name_fuzzy",
            confidence=round(top_score * 0.85, 3),   # discount fuzzy confidence
            matched_value=top_company.company_name,
            is_known=True,
            candidates=[
                {"company_id": str(c.id), "company_name": c.company_name, "score": s}
                for s, c in scored[:5]
            ],
        )

    return CompanyDetectionResult(
        match_method="none",
        confidence=0.0,
        is_known=False,
    )
