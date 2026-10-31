import uuid
import re
from datetime import datetime
from typing import Any, Optional
from sqlalchemy import select
from loguru import logger

from app.models.job import OCRJob
from app.models.file import UploadedFile
from app.models.extracted_data import ExtractedData
from app.api.v1.routers.upload_helpers import OCR_DEMO_JOBS

DATE_RES = [
    re.compile(r"^(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})$"),
    re.compile(r"^(\d{4})[\/\-\.](\d{1,2})[\/\-\.](\d{1,2})$"),
]

def normalize_doc_number(val: Any) -> str:
    """Normalize invoice/order numbers: trim, uppercase, remove spaces/safe separators."""
    if not val:
        return ""
    val_str = str(val).strip().upper()
    return re.sub(r"[\s\-\/_\\#:]", "", val_str)

def normalize_company(val: Any) -> str:
    """Normalize company/vendor names: lowercase, strip punctuation/spaces/suffixes."""
    if not val:
        return ""
    val_str = str(val).strip().lower()
    cleaned = re.sub(r"[^\w\s]", "", val_str)
    cleaned = re.sub(r"\b(pvt|ltd|inc|corp|co|limited|private|corporation|incorporated)\b", "", cleaned)
    return "".join(cleaned.split())

def normalize_date(val: Any) -> str:
    """Normalize dates: format common formats to YYYY-MM-DD or fall back to digits-only."""
    if not val:
        return ""
    v = str(val).strip()
    for r in DATE_RES:
        m = r.match(v)
        if m:
            parts = [int(x) for x in m.groups()]
            if parts[0] > 31:  # YYYY-MM-DD
                y, mo, d = parts[0], parts[1], parts[2]
            else:              # DD-MM-YYYY or MM-DD-YYYY
                p0, p1, y = parts[0], parts[1], parts[2]
                if y < 100:
                    y += 2000
                if p0 > 12:    # p0 must be day
                    d, mo = p0, p1
                elif p1 > 12:  # p1 must be day
                    d, mo = p1, p0
                else:          # ambiguous, assume DD-MM-YYYY
                    d, mo = p0, p1
            try:
                return f"{y:04d}-{mo:02d}-{d:02d}"
            except Exception:
                pass
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(v, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Keep only digits
    return re.sub(r"\D", "", v)

def normalize_amount(val: Any) -> str:
    """Normalize amounts: strip currency letters/resolve extra dots/round to 2 decimals."""
    if val is None:
        return ""
    val_str = str(val).strip()
    # Remove characters that are not digits, dots, commas, or signs
    cleaned = re.sub(r"[^\d.,-]", "", val_str)
    cleaned = cleaned.replace(",", "")
    if "." in cleaned:
        parts = cleaned.split(".")
        prefix = "".join(re.sub(r"\D", "", p) for p in parts[:-1])
        suffix = re.sub(r"\D", "", parts[-1])
        cleaned = f"{prefix}.{suffix}"
    else:
        cleaned = re.sub(r"\D", "", cleaned)
        
    if not cleaned:
        return ""
    try:
        return f"{float(cleaned):.2f}"
    except ValueError:
        return ""

def extract_keys_from_dict(fields: dict, document_type: str = "general") -> dict:
    """Extract standard keys from validated fields / mapped output dictionary."""
    if not fields:
        fields = {}
    def get_val(*keys) -> str:
        for k in keys:
            if k in fields:
                entry = fields[k]
                if isinstance(entry, dict):
                    v = entry.get("value")
                else:
                    v = entry
                if v:
                    return str(v)
        return ""
    
    vendor = get_val("company_name", "vendor_name", "supplier_name", "vendor", "supplier")
    doc_no = get_val("invoice_number", "po_number", "grn_number", "invoice_no", "po_no", "grn_no", "order_number", "order_no")
    doc_date = get_val("invoice_date", "po_date", "grn_date", "invoice_dt", "bill_date", "document_date")
    total = get_val("grand_total", "total_amount", "net_amount", "invoice_total")
    currency = get_val("currency")
    
    return {
        "document_type": str(document_type or "general").strip().lower(),
        "vendor": vendor,
        "document_number": doc_no,
        "document_date": doc_date,
        "total_amount": total,
        "currency": currency,
    }

async def detect_duplicates(
    current_job_id: uuid.UUID,
    checksum: str,
    validated_fields: dict,
    document_type: str,
    org_id: Optional[uuid.UUID],
    created_by_id: uuid.UUID,
    db: Optional[Any] = None,
) -> dict:
    """
    Scans the database and the in-memory demo cache to find duplicate documents.
    """
    default_result = {
        "status": "not_duplicate",
        "confidence": 0.0,
        "matched_job_id": None,
        "matched_document_name": None,
        "matched_created_at": None,
        "reasons": [],
        "blocking": False,
        "review_required": False
    }

    current_keys = extract_keys_from_dict(validated_fields or {}, document_type or "general")
    norm_curr_num = normalize_doc_number(current_keys["document_number"])
    norm_curr_vendor = normalize_company(current_keys["vendor"])
    norm_curr_date = normalize_date(current_keys["document_date"])
    norm_curr_total = normalize_amount(current_keys["total_amount"])

    candidates = []

    # 1. Query candidates from Database
    if db is not None:
        try:
            stmt = (
                select(OCRJob, UploadedFile, ExtractedData)
                .join(UploadedFile, OCRJob.file_id == UploadedFile.id)
                .outerjoin(ExtractedData, OCRJob.id == ExtractedData.job_id)
                .where(OCRJob.id != current_job_id)
                .where(OCRJob.status.in_(["completed", "review_required"]))
            )
            if org_id:
                stmt = stmt.where(OCRJob.org_id == org_id)
            else:
                stmt = stmt.where(OCRJob.created_by_id == created_by_id, OCRJob.org_id.is_(None))
            
            res = await db.execute(stmt)
            for row in res.all():
                cand_job, cand_file, cand_extracted = row
                cand_fields = {}
                if cand_extracted:
                    cand_fields = cand_extracted.validated_fields or cand_extracted.mapped_output or {}
                
                cand_doc_type = cand_file.document_type or "general"
                cand_keys = extract_keys_from_dict(cand_fields, cand_doc_type)
                
                candidates.append({
                    "job_id": str(cand_job.id),
                    "document_name": cand_file.original_filename,
                    "checksum": cand_file.checksum_sha256,
                    "created_at": cand_job.created_at.isoformat() if cand_job.created_at else None,
                    "keys": cand_keys
                })
        except Exception as db_exc:
            logger.warning(f"Database query for duplicates failed: {db_exc}")

    # 2. Query candidates from In-Memory cache OCR_DEMO_JOBS
    for job_id_str, demo_job in OCR_DEMO_JOBS.items():
        if job_id_str == str(current_job_id):
            continue
        
        # Check status
        if demo_job.get("status") not in ("completed", "review_required"):
            continue
            
        # Check scope
        job_org_id = demo_job.get("org_id")
        job_owner = demo_job.get("owner_user_id") or demo_job.get("created_by_id")
        
        if org_id:
            if str(job_org_id) != str(org_id):
                continue
        else:
            if job_org_id is not None:
                continue
            if str(job_owner) != str(created_by_id):
                continue
        
        cand_fields = demo_job.get("validated_fields") or demo_job.get("fields") or {}
        cand_doc_type = demo_job.get("document_type") or "general"
        cand_keys = extract_keys_from_dict(cand_fields, cand_doc_type)
        
        candidates.append({
            "job_id": job_id_str,
            "document_name": demo_job.get("filename") or "Unknown",
            "checksum": demo_job.get("checksum_sha256") or demo_job.get("checksum"),
            "created_at": demo_job.get("created_at") or demo_job.get("uploaded_at"),
            "keys": cand_keys
        })

    # 3. Score each candidate and find the best match
    best_match = None
    best_severity = -1
    severity_map = {
        "not_duplicate": 0,
        "possible_duplicate": 1,
        "likely_duplicate": 2,
        "exact_duplicate": 3
    }

    for cand in candidates:
        norm_cand_num = normalize_doc_number(cand["keys"]["document_number"])
        norm_cand_vendor = normalize_company(cand["keys"]["vendor"])
        norm_cand_date = normalize_date(cand["keys"]["document_date"])
        norm_cand_total = normalize_amount(cand["keys"]["total_amount"])
        
        checksum_match = bool(checksum and checksum == cand["checksum"])
        vendor_match = bool(norm_curr_vendor and norm_curr_vendor == norm_cand_vendor)
        number_match = bool(norm_curr_num and norm_curr_num == norm_cand_num)
        date_match = bool(norm_curr_date and norm_curr_date == norm_cand_date)
        total_match = bool(norm_curr_total and norm_curr_total == norm_cand_total)
        doc_type_match = bool(document_type and document_type.lower() == cand["keys"]["document_type"].lower())
        
        status = "not_duplicate"
        confidence = 0.0
        reasons = []
        
        if checksum_match:
            status = "exact_duplicate"
            confidence = 1.0
            reasons.append("same file checksum")
        elif doc_type_match and vendor_match and number_match and total_match:
            status = "exact_duplicate"
            confidence = 0.95
            reasons.extend(["same invoice number", "same vendor", "same total amount"])
        elif number_match and vendor_match:
            status = "likely_duplicate"
            confidence = 0.8
            reasons.extend(["same invoice number", "same vendor"])
            if date_match:
                reasons.append("same document date")
            if total_match:
                reasons.append("same total amount")
        elif vendor_match and date_match and total_match:
            status = "possible_duplicate"
            confidence = 0.5
            reasons.extend(["same vendor", "same total amount"])
            
        severity = severity_map.get(status, 0)
        if severity > best_severity:
            best_severity = severity
            best_match = {
                "status": status,
                "confidence": confidence,
                "matched_job_id": cand["job_id"],
                "matched_document_name": cand["document_name"],
                "matched_created_at": cand["created_at"],
                "reasons": reasons,
                "blocking": status == "exact_duplicate",
                "review_required": status in ("exact_duplicate", "likely_duplicate")
            }
        elif severity == best_severity and severity > 0:
            if confidence > best_match["confidence"]:
                best_match = {
                    "status": status,
                    "confidence": confidence,
                    "matched_job_id": cand["job_id"],
                    "matched_document_name": cand["document_name"],
                    "matched_created_at": cand["created_at"],
                    "reasons": reasons,
                    "blocking": status == "exact_duplicate",
                    "review_required": status in ("exact_duplicate", "likely_duplicate")
                }

    if best_match:
        return best_match
    return default_result
