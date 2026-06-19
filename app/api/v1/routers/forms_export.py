"""
forms_export.py — Legacy export endpoints and PDF/Excel builder functions.
Extracted from forms.py for maintainability.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_roles
from app.models.enums import LogEventType, LogLevel, UserRole
from app.services.audit_service import write_audit_log
from app.services.ownership_service import get_owned_extracted_data
from app.services.export_precheck_engine import build_export_precheck
from app.services.validation_service import validate_mapped_fields
from app.services.company_form_mapper import normalize_mapped_fields

export_router = APIRouter()

from pydantic import BaseModel
from typing import Any, Dict, Optional

class ExportRequest(BaseModel):
    fields: Dict[str, Any]
    title: Optional[str] = "Company Invoice Form"
    extracted_data_id: Optional[str] = None
    skip_validation: bool = False


# ── Legacy export helpers ─────────────────────────────────────────────────────

async def _resolve_export_fields(req: ExportRequest, db, current_user: dict) -> Dict[str, Any]:
    """Task 3+4+7.1: Prefer DB validated_fields; fall back to client fields with server validation.
    TASK 7.1 FIX: Legacy export routes now reject exports with zero valid fields (prevents empty export bypass).
    Full export_precheck gate is enforced on workbench export routes (_workbench_export_payload).
    Legacy export uses validated_fields from DB which have already passed validation_service.
    """
    flat_fields: Dict[str, Any] = {}

    if req.extracted_data_id and not req.skip_validation:
        try:
            ext_id = uuid.UUID(req.extracted_data_id)
            extraction = await get_owned_extracted_data(db, ext_id, current_user)
            if extraction.validated_fields:
                flat_fields = {
                    k: (v["value"] if isinstance(v, dict) else str(v))
                    for k, v in extraction.validated_fields.items()
                }
        except ValueError:
            pass

    if not flat_fields:
        client_fields = normalize_mapped_fields({k: {"value": v, "confidence": 0.7, "source": "manual"} for k, v in req.fields.items() if v})
        validation = validate_mapped_fields(client_fields, raw_text="", doc_type="general")

        if not req.skip_validation and validation["errors"]:
            raise HTTPException(
                status_code=422,
                detail={"message": "Export blocked: validation errors", "errors": validation["errors"]},
            )

        flat_fields = {
            k: (v["value"] if isinstance(v, dict) else str(v))
            for k, v in validation["validated_fields"].items()
        } or req.fields

    precheck = build_export_precheck({
        "module": "general",
        "document_type": "general",
        "form_data": flat_fields,
        "mapped_fields": flat_fields,
        "item_rows": [],
        "resolved_masters": [],
        "unresolved_masters": [],
        "confidence_map": {},
        "warnings": [],
        "source_type": "legacy_export",
    })
    if not precheck["export_allowed"]:
        raise HTTPException(
            status_code=422,
            detail={
                "export_allowed": False,
                "quality_score": precheck["quality_score"],
                "quality_grade": precheck["quality_grade"],
                "blockers": precheck["blockers"],
                "missing_required_fields": precheck["missing_required_fields"],
                "unresolved_masters": precheck["unresolved_masters"],
                "row_issues": precheck["row_issues"],
                "message": "Export blocked by export precheck.",
            },
        )
    return flat_fields


async def write_export_audit(
    db: AsyncSession,
    req,
    request: Request,
    current_user: dict,
    export_format: str,
    filename: str,
    field_count: int,
    byte_count: int,
) -> None:
    await write_audit_log(
        db,
        action=f"export.{export_format}",
        event_type=LogEventType.DATA_EXPORT,
        status="success",
        user_id=current_user.get("user_id"),
        org_id=current_user.get("org_id"),
        resource_type="extracted_data" if req.extracted_data_id else "export",
        resource_id=req.extracted_data_id or filename,
        message=f"{export_format.upper()} export generated",
        metadata={
            "format": export_format,
            "filename": filename,
            "field_count": field_count,
            "byte_count": byte_count,
        },
        request=request,
    )


async def write_export_failure_audit(
    db: AsyncSession,
    req,
    request: Request,
    current_user: dict,
    export_format: str,
    exc: HTTPException,
) -> None:
    await write_audit_log(
        db,
        action="validation.failure",
        event_type=LogEventType.EXTRACTION,
        status="failure",
        user_id=current_user.get("user_id"),
        org_id=current_user.get("org_id"),
        resource_type="extracted_data" if req.extracted_data_id else "export",
        resource_id=req.extracted_data_id or export_format,
        level=LogLevel.WARNING,
        message="Export validation failed",
        metadata={"format": export_format, "status_code": exc.status_code, "detail": exc.detail},
        error=str(exc.detail),
        request=request,
    )


# ── Legacy export endpoints ───────────────────────────────────────────────────

@export_router.post("/export/pdf", summary="Export filled form as PDF")
async def export_pdf(
    req: ExportRequest,
    request: Request,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    try:
        fields = await _resolve_export_fields(req, db, current_user)
    except HTTPException as exc:
        await write_export_failure_audit(db, req, request, current_user, "pdf", exc)
        await db.commit()
        raise
    buf = _build_pdf(fields, req.title or "Company Form")
    filename = f"company-form-{uuid.uuid4().hex[:8]}.pdf"
    await write_export_audit(db, req, request, current_user, "pdf", filename, len(fields), len(buf))
    return StreamingResponse(
        io.BytesIO(buf),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@export_router.post("/export/excel", summary="Export filled form as Excel")
async def export_excel(
    req: ExportRequest,
    request: Request,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    try:
        fields = await _resolve_export_fields(req, db, current_user)
    except HTTPException as exc:
        await write_export_failure_audit(db, req, request, current_user, "excel", exc)
        await db.commit()
        raise
    buf = _build_excel(fields, req.title or "Company Form")
    filename = f"company-form-{uuid.uuid4().hex[:8]}.xlsx"
    await write_export_audit(db, req, request, current_user, "excel", filename, len(fields), len(buf))
    return StreamingResponse(
        io.BytesIO(buf),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Export builders ───────────────────────────────────────────────────────────

def _build_workbench_excel(payload: dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    workbook = Workbook()
    header_sheet = workbook.active
    header_sheet.title = "Header"
    header_sheet.append(["ERP Field", "Value"])
    for cell in header_sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4F46E5")
    for key, value in payload["header"].items():
        header_sheet.append([key, str(value)[:4000]])
    header_sheet.column_dimensions["A"].width = 30
    header_sheet.column_dimensions["B"].width = 70
    for row in header_sheet.iter_rows():
        row[1].alignment = Alignment(wrap_text=True, vertical="top")

    line_sheet = workbook.create_sheet("Lines")
    line_keys = list(payload["lines"][0].keys()) if payload["lines"] else []
    if line_keys:
        line_sheet.append(line_keys)
        for cell in line_sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="4F46E5")
        for line in payload["lines"]:
            line_sheet.append([str(line.get(key, ""))[:4000] for key in line_keys])
        for column in line_sheet.columns:
            line_sheet.column_dimensions[column[0].column_letter].width = 18

    validation_sheet = workbook.create_sheet("Validation")
    validation_sheet.append(["Property", "Value"])
    validation_rows = {
        "status": payload["validation"].get("validation_status"),
        "export_allowed": payload["validation"].get("export_allowed"),
        "blocked_fields": ", ".join(payload["validation"].get("blocked_fields", [])),
        "warnings": " | ".join(payload["validation"].get("warnings", [])),
        "dataset_id": payload["dataset"].get("id"),
        "dataset_name": payload["dataset"].get("name"),
        "dataset_match_reason": payload["dataset"].get("match_reason"),
    }
    for key, value in validation_rows.items():
        validation_sheet.append([key, str(value or "")[:4000]])
    validation_sheet.column_dimensions["A"].width = 28
    validation_sheet.column_dimensions["B"].width = 80

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _build_workbench_pdf(payload: dict[str, Any], title: str) -> bytes:
    from xml.sax.saxutils import escape

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        buffer = io.BytesIO()
        document = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
        styles = getSampleStyleSheet()
        story = [Paragraph(escape(title), styles["Title"]), Spacer(1, 8)]
        dataset_name = payload["dataset"].get("name") or "Generic mapping"
        story.append(Paragraph(f"Dataset: {escape(str(dataset_name))}", styles["Normal"]))
        story.append(Paragraph(f"Validation: {escape(str(payload['validation'].get('validation_status', '')))}", styles["Normal"]))
        story.append(Spacer(1, 8))
        header_rows = [[Paragraph("Field", styles["BodyText"]), Paragraph("Value", styles["BodyText"])]]
        for key, value in payload["header"].items():
            header_rows.append([Paragraph(escape(key), styles["BodyText"]), Paragraph(escape(str(value)[:1000]), styles["BodyText"])])
        header_table = Table(header_rows, colWidths=[150, 600], repeatRows=1)
        header_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E0E7FF")), ("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.extend([header_table, Spacer(1, 10)])
        if payload["lines"]:
            keys = list(payload["lines"][0].keys())
            line_rows = [[Paragraph(escape(key), styles["BodyText"]) for key in keys]]
            for line in payload["lines"]:
                line_rows.append([Paragraph(escape(str(line.get(key, ""))[:300]), styles["BodyText"]) for key in keys])
            line_table = Table(line_rows, repeatRows=1)
            line_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E0E7FF")), ("GRID", (0, 0), (-1, -1), 0.3, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 6), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.extend([Paragraph("Item Lines", styles["Heading2"]), line_table])
        document.build(story)
        return buffer.getvalue()
    except ImportError:
        fallback_fields = dict(payload["header"])
        fallback_fields["dataset"] = payload["dataset"].get("name") or "Generic mapping"
        fallback_fields["validation"] = payload["validation"].get("validation_status")
        fallback_fields["items"] = payload["lines"]
        return _pdf_fallback(fallback_fields, title)


def _build_pdf(fields: Dict[str, str], title: str) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=18*mm, rightMargin=18*mm,
                                topMargin=18*mm, bottomMargin=18*mm)
        styles = getSampleStyleSheet()
        brand = colors.HexColor("#6c63ff")
        dark  = colors.HexColor("#1a1d27")

        h1 = ParagraphStyle("H1", parent=styles["Heading1"], textColor=brand,
                             fontSize=18, spaceAfter=4, alignment=TA_CENTER)
        sub = ParagraphStyle("SUB", parent=styles["Normal"], textColor=colors.grey,
                             fontSize=9, spaceAfter=10, alignment=TA_CENTER)
        lbl = ParagraphStyle("LBL", parent=styles["Normal"], textColor=colors.grey,
                             fontSize=8, fontName="Helvetica-Bold")
        val = ParagraphStyle("VAL", parent=styles["Normal"], fontSize=10, textColor=dark)

        story = [
            Paragraph(title, h1),
            Paragraph("Generated by Aline X OCR Platform", sub),
            Spacer(1, 6*mm),
        ]

        LABELS = {
            "invoice_no": "Invoice #", "po_no": "PO Number", "po_date": "PO Date",
            "gstin": "GSTIN", "buyer_gstin": "Buyer GSTIN", "vendor_gstin": "Vendor GSTIN",
            "address": "Address", "hsn_code": "HSN/SAC", "rate": "Rate",
            "taxable_value": "Taxable Value", "cgst": "CGST", "sgst": "SGST",
            "igst": "IGST", "total_tax": "Total Tax", "amount_in_words": "Amount in Words",
            "bank_name": "Bank Name", "account_no": "Account Number", "ifsc": "IFSC",
            "document_type": "Document Type",
            "vendor_name": "Vendor Name", "customer_name": "Customer Name",
            "buyer_name": "Buyer Name", "consignee": "Consignee",
            "consignee_name": "Consignee Name",
            "invoice_number": "Invoice #", "bill_number": "Bill #",
            "purchase_order_number": "PO Number", "po_number": "PO Number",
            "invoice_date": "Invoice Date", "po_date": "PO Date",
            "grn_number": "GRN Number", "grn_date": "GRN Date",
            "due_date": "Due Date", "gst_number": "GST Number",
            "pan_number": "PAN Number", "phone": "Phone", "email": "Email",
            "billing_address": "Billing Address", "shipping_address": "Shipping Address",
            "item_name": "Item Name", "item_description": "Item Description",
            "quantity": "Quantity", "unit_price": "Unit Price",
            "subtotal": "Subtotal", "tax_amount": "Tax Amount",
            "discount": "Discount", "total_amount": "Total Amount",
            "grand_total": "Grand Total",
            "payment_status": "Payment Status", "notes": "Notes",
        }

        rows = []
        EXCLUDE_KEYS = {"item_rows", "items", "raw_text", "full_text", "text", "extracted_text", "raw_text_preview"}
        for key, label in LABELS.items():
            v = fields.get(key, "")
            if v:
                val_str = str(v)
                if len(val_str) > 1000:
                    val_str = val_str[:997] + "..."
                rows.append([Paragraph(label, lbl), Paragraph(val_str, val)])
        for key, v in fields.items():
            if v and key not in LABELS and key not in EXCLUDE_KEYS:
                val_str = str(v)
                if len(val_str) > 1000:
                    val_str = val_str[:997] + "..."
                rows.append([Paragraph(key.replace("_", " ").title(), lbl), Paragraph(val_str, val)])

        if rows:
            t = Table(rows, colWidths=[55*mm, 110*mm])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0efff")),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e0e0ee")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(t)
        elif not (fields.get("items") or fields.get("item_rows")):
            story.append(Paragraph("No fields provided.", styles["Normal"]))
        item_rows = fields.get("items") or fields.get("item_rows") or []
        if isinstance(item_rows, list) and item_rows:
            headers = ["Sno", "Order No", "Group", "Article", "Colour", "HSN/SAC", "Type", "Size", "UOM", "Qty", "Price", "Amount", "Total", "Act"]
            keys = ["sno", "order_no", "group", "article", "colour", "hsn_sac", "type", "size", "uom", "qty", "price", "amount", "total", "act"]
            item_table = [headers] + [[str(row.get(k, "")) if isinstance(row, dict) else "" for k in keys] for row in item_rows]
            story.append(Spacer(1, 6*mm))
            it = Table(item_table, repeatRows=1)
            it.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0efff")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e0e0ee")),
                ("FONTSIZE", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(it)

        doc.build(story)
        return buf.getvalue()
    except ImportError:
        return _pdf_fallback(fields, title)


def _pdf_fallback(fields: Dict[str, str], title: str) -> bytes:
    """Minimal PDF if reportlab not available."""
    lines = [f"  {k}: {v}" for k, v in fields.items() if v]
    body = "\n".join(lines)
    content = f"Company Form — {title}\n\n{body}\n"
    stream = content.encode()
    pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length " + str(len(stream) + 20).encode() + b">>\nstream\nBT /F1 12 Tf 40 750 Td\n"
        + stream + b"\nET\nendstream\nendobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"xref\n0 6\ntrailer<</Size 6/Root 1 0 R>>\n%%EOF\n"
    )
    return pdf


def _build_excel(fields: Dict[str, str], title: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Company Form"

    brand_fill = PatternFill("solid", fgColor="6C63FF")
    header_fill = PatternFill("solid", fgColor="EEEDff")
    thin = Border(
        left=Side(style="thin", color="CCCCEE"),
        right=Side(style="thin", color="CCCCEE"),
        top=Side(style="thin", color="CCCCEE"),
        bottom=Side(style="thin", color="CCCCEE"),
    )

    ws.merge_cells("A1:B1")
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=14, color="FFFFFF")
    ws["A1"].fill = brand_fill
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    ws.merge_cells("A2:B2")
    ws["A2"] = "Generated by Aline X OCR Platform"
    ws["A2"].font = Font(italic=True, size=9, color="888888")
    ws["A2"].alignment = Alignment(horizontal="center")

    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 48

    LABELS = {
        "invoice_no": "Invoice #", "po_no": "PO Number", "po_date": "PO Date",
        "gstin": "GSTIN", "buyer_gstin": "Buyer GSTIN", "vendor_gstin": "Vendor GSTIN",
        "address": "Address", "hsn_code": "HSN/SAC", "rate": "Rate",
        "taxable_value": "Taxable Value", "cgst": "CGST", "sgst": "SGST",
        "igst": "IGST", "total_tax": "Total Tax", "amount_in_words": "Amount in Words",
        "bank_name": "Bank Name", "account_no": "Account Number", "ifsc": "IFSC",
        "document_type": "Document Type",
        "vendor_name": "Vendor Name", "customer_name": "Customer Name",
        "buyer_name": "Buyer Name", "consignee": "Consignee",
        "consignee_name": "Consignee Name",
        "invoice_number": "Invoice #", "bill_number": "Bill #",
        "purchase_order_number": "PO Number", "po_number": "PO Number",
        "invoice_date": "Invoice Date", "po_date": "PO Date",
        "grn_number": "GRN Number", "grn_date": "GRN Date",
        "due_date": "Due Date", "gst_number": "GST Number",
        "pan_number": "PAN Number", "phone": "Phone", "email": "Email",
        "billing_address": "Billing Address", "shipping_address": "Shipping Address",
        "item_name": "Item Name", "item_description": "Item Description",
        "quantity": "Quantity", "unit_price": "Unit Price",
        "subtotal": "Subtotal", "tax_amount": "Tax Amount",
        "discount": "Discount", "total_amount": "Total Amount",
        "grand_total": "Grand Total",
        "payment_status": "Payment Status", "notes": "Notes",
    }

    row = 3
    for key, label in LABELS.items():
        v = fields.get(key, "")
        ws[f"A{row}"] = label
        ws[f"A{row}"].font = Font(bold=True, size=10, color="4B4B8F")
        ws[f"A{row}"].fill = header_fill
        ws[f"A{row}"].border = thin
        ws[f"A{row}"].alignment = Alignment(vertical="center")
        ws[f"B{row}"] = str(v) if v else ""
        ws[f"B{row}"].border = thin
        ws[f"B{row}"].alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[row].height = 18
        row += 1
    for key, v in fields.items():
        if not v or key in LABELS or key in {"item_rows", "items"}:
            continue
        ws[f"A{row}"] = key.replace("_", " ").title()
        ws[f"A{row}"].font = Font(bold=True, size=10, color="4B4B8F")
        ws[f"A{row}"].fill = header_fill
        ws[f"A{row}"].border = thin
        ws[f"B{row}"] = str(v)
        ws[f"B{row}"].border = thin
        ws[f"B{row}"].alignment = Alignment(wrap_text=True, vertical="center")
        row += 1
    item_rows = fields.get("items") or fields.get("item_rows") or []
    if isinstance(item_rows, list) and item_rows:
        row += 1
        headers = ["Sno", "Order No", "Group", "Article", "Colour", "HSN/SAC", "Type", "Size", "UOM", "Qty", "Price", "Amount", "Total", "Act"]
        keys = ["sno", "order_no", "group", "article", "colour", "hsn_sac", "type", "size", "uom", "qty", "price", "amount", "total", "act"]
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col, value=header)
            cell.font = Font(bold=True, size=10, color="4B4B8F")
            cell.fill = header_fill
            cell.border = thin
        for item in item_rows:
            row += 1
            for col, key in enumerate(keys, start=1):
                cell = ws.cell(row=row, column=col, value=str(item.get(key, "")) if isinstance(item, dict) else "")
                cell.border = thin
                cell.alignment = Alignment(wrap_text=True, vertical="center")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
