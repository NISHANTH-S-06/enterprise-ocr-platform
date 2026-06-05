"""
PDF export engine — two modes:

1. Template fill (pypdf AcroForm):
   fill_config.pdf_fields: {field_name: acroform_widget_name}
   Fills AcroForm widgets in an existing PDF template.

2. Generated PDF (reportlab):
   Builds a professional invoice/document from scratch when no template is provided.
"""
from __future__ import annotations
import io
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from app.core.config import get_settings

settings = get_settings()

W, H = A4
MARGIN = 15 * mm
_BRAND   = colors.HexColor("#1F4E79")
_LIGHT   = colors.HexColor("#EBF3FB")
_GREY    = colors.HexColor("#666666")
_BLACK   = colors.black
_WHITE   = colors.white


def _styles():
    ss = getSampleStyleSheet()
    return {
        "title":   ParagraphStyle("title",   fontSize=18, textColor=_BRAND,  fontName="Helvetica-Bold",  alignment=TA_LEFT,  spaceAfter=4),
        "h2":      ParagraphStyle("h2",      fontSize=11, textColor=_BRAND,  fontName="Helvetica-Bold",  alignment=TA_LEFT,  spaceAfter=2),
        "label":   ParagraphStyle("label",   fontSize=8,  textColor=_GREY,   fontName="Helvetica-Bold",  alignment=TA_LEFT),
        "value":   ParagraphStyle("value",   fontSize=9,  textColor=_BLACK,  fontName="Helvetica",       alignment=TA_LEFT),
        "thhdr":   ParagraphStyle("thhdr",   fontSize=8,  textColor=_WHITE,  fontName="Helvetica-Bold",  alignment=TA_CENTER),
        "tcell":   ParagraphStyle("tcell",   fontSize=8,  textColor=_BLACK,  fontName="Helvetica",       alignment=TA_LEFT),
        "tright":  ParagraphStyle("tright",  fontSize=8,  textColor=_BLACK,  fontName="Helvetica",       alignment=TA_RIGHT),
        "footer":  ParagraphStyle("footer",  fontSize=7,  textColor=_GREY,   fontName="Helvetica",       alignment=TA_CENTER),
    }


class PDFExporter:
    def export(
        self,
        fields:        Dict[str, Any],
        line_items:    List[Dict[str, Any]],
        job_id:        uuid.UUID,
        fill_config:   Optional[Dict[str, Any]] = None,
        template_path: Optional[str]            = None,
    ) -> Path:
        dest = settings.output_path / f"{job_id}.pdf"

        if template_path and Path(template_path).exists() and fill_config:
            self._fill_acroform(template_path, fields, fill_config, dest)
        else:
            self._generate(fields, line_items, dest)

        logger.info(f"PDF export: {dest}")
        return dest

    # ── AcroForm fill ─────────────────────────────────────────────────────────

    def _fill_acroform(
        self,
        template_path: str,
        fields:       Dict[str, Any],
        fill_config:  Dict[str, Any],
        dest:         Path,
    ) -> None:
        try:
            from pypdf import PdfReader, PdfWriter
            reader = PdfReader(template_path)
            writer = PdfWriter()
            writer.append(reader)
            pdf_field_map: Dict[str, str] = fill_config.get("pdf_fields", {})
            fill_values = {
                pdf_field_map.get(fname, fname): str(val)
                for fname, val in fields.items()
                if val is not None
            }
            writer.update_page_form_field_values(writer.pages[0], fill_values)
            dest.write_bytes(b"".join(io.BytesIO(b).getvalue() for b in [writer.write_to_bytes()]))
        except Exception as exc:
            logger.warning(f"AcroForm fill failed ({exc}), falling back to generated PDF")
            self._generate(fields, [], dest)

    # ── generated PDF ─────────────────────────────────────────────────────────

    def _generate(self, fields: Dict[str, Any], line_items: List[Dict[str, Any]], dest: Path) -> None:
        s = _styles()
        story = []

        # ── Header ────────────────────────────────────────────────────────────
        vendor = str(fields.get("vendor_name", "VENDOR"))
        story.append(Paragraph(vendor, s["title"]))
        story.append(HRFlowable(width="100%", thickness=2, color=_BRAND, spaceAfter=6))

        # Meta row: invoice vs bill-to
        meta_data = [
            [Paragraph("INVOICE TO", s["label"]), Paragraph("INVOICE DETAILS", s["label"])],
            [Paragraph(str(fields.get("customer_name", "")), s["value"]),
             Paragraph(f"<b>Invoice #</b> {fields.get('invoice_number','—')}", s["value"])],
            [Paragraph(str(fields.get("customer_address", {}).get("raw", "") if isinstance(fields.get("customer_address"), dict) else fields.get("customer_address", "")), s["value"]),
             Paragraph(f"<b>Date:</b> {fields.get('invoice_date','—')}", s["value"])],
            ["",
             Paragraph(f"<b>Due Date:</b> {fields.get('due_date','—')}", s["value"])],
        ]
        meta_table = Table(meta_data, colWidths=[(W - 2*MARGIN)*0.55, (W - 2*MARGIN)*0.45])
        meta_table.setStyle(TableStyle([
            ("VALIGN",  (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0,0), (-1,-1), 2),
            ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 8))

        # GSTIN row
        if fields.get("vendor_gstin") or fields.get("customer_gstin"):
            def _gstin_val(v):
                return v.get("gstin", "") if isinstance(v, dict) else str(v or "")
            gst_data = [
                [Paragraph("Vendor GSTIN", s["label"]), Paragraph("Customer GSTIN", s["label"])],
                [Paragraph(_gstin_val(fields.get("vendor_gstin","")), s["value"]),
                 Paragraph(_gstin_val(fields.get("customer_gstin","")), s["value"])],
            ]
            gst_t = Table(gst_data, colWidths=[(W - 2*MARGIN)*0.5] * 2)
            gst_t.setStyle(TableStyle([("TOPPADDING",(0,0),(-1,-1),1),("BOTTOMPADDING",(0,0),(-1,-1),1)]))
            story.append(gst_t)
            story.append(Spacer(1, 6))

        # ── Line items ─────────────────────────────────────────────────────────
        if line_items:
            story.append(Paragraph("Items", s["h2"]))
            all_cols = list(dict.fromkeys(k for item in line_items for k in item if k != "confidence"))
            display_cols = [c for c in ["description","hsn_sac","quantity","unit_price","tax_rate","total"] if c in all_cols]
            if not display_cols: display_cols = all_cols

            header_row = [Paragraph(c.replace("_"," ").title(), s["thhdr"]) for c in display_cols]
            col_w_map  = {"description": 0.35, "hsn_sac": 0.10, "quantity": 0.08, "unit_price": 0.14, "tax_rate": 0.10, "total": 0.13}
            usable     = W - 2 * MARGIN
            col_widths = [usable * col_w_map.get(c, 0.12) for c in display_cols]

            li_rows = [header_row]
            for item in line_items:
                row = []
                for c in display_cols:
                    v = item.get(c, "")
                    st = s["tright"] if c in ("unit_price","total","tax_rate","quantity") else s["tcell"]
                    row.append(Paragraph(str(v) if v is not None else "", st))
                li_rows.append(row)

            li_table = Table(li_rows, colWidths=col_widths, repeatRows=1)
            li_table.setStyle(TableStyle([
                ("BACKGROUND",  (0, 0), (-1, 0), _BRAND),
                ("BACKGROUND",  (0, 2), (-1, -1), _LIGHT),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_WHITE, _LIGHT]),
                ("GRID",        (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
                ("TOPPADDING",  (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING",(0,0), (-1,-1), 3),
                ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(li_table)
            story.append(Spacer(1, 8))

        # ── Totals ─────────────────────────────────────────────────────────────
        totals_fields = [
            ("Subtotal",      fields.get("subtotal")),
            ("CGST",          fields.get("cgst")),
            ("SGST",          fields.get("sgst")),
            ("IGST",          fields.get("igst")),
            ("Total Tax",     fields.get("total_tax")),
            ("Discount",      fields.get("discount")),
            ("TOTAL AMOUNT",  fields.get("total_amount")),
        ]
        total_rows = []
        for label, val in totals_fields:
            if val is None: continue
            amt = val.get("amount") if isinstance(val, dict) else val
            bold = label.startswith("TOTAL")
            font = Font = "Helvetica-Bold" if bold else "Helvetica"
            total_rows.append([
                Paragraph(label, ParagraphStyle("_", fontSize=9 if not bold else 10, fontName=font, alignment=TA_RIGHT)),
                Paragraph(f"{amt:,.2f}" if isinstance(amt, (int, float)) else str(amt),
                          ParagraphStyle("_", fontSize=9 if not bold else 10, fontName=font, alignment=TA_RIGHT)),
            ])
        if total_rows:
            tot_w = (W - 2*MARGIN) * 0.4
            t_table = Table(total_rows, colWidths=[tot_w * 0.55, tot_w * 0.45], hAlign="RIGHT")
            t_table.setStyle(TableStyle([
                ("LINEABOVE",     (0, -1), (-1, -1), 1, _BRAND),
                ("TOPPADDING",    (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.append(t_table)

        # Amount in words
        if fields.get("amount_in_words"):
            story.append(Spacer(1, 4))
            story.append(Paragraph(f"<i>Amount in Words: {fields['amount_in_words']}</i>", s["value"]))

        # ── Bank / payment ─────────────────────────────────────────────────────
        bank_fields = {k: fields.get(k) for k in ("bank_account","ifsc_code","payment_terms") if fields.get(k)}
        if bank_fields:
            story.append(Spacer(1, 10))
            story.append(HRFlowable(width="100%", thickness=0.5, color=_GREY))
            story.append(Paragraph("Payment Details", s["h2"]))
            for k, v in bank_fields.items():
                story.append(Paragraph(f"<b>{k.replace('_',' ').title()}:</b> {v}", s["value"]))

        # Footer
        story.append(Spacer(1, 12))
        story.append(HRFlowable(width="100%", thickness=0.5, color=_GREY))
        story.append(Paragraph("Generated by OCR Platform", s["footer"]))

        doc = SimpleDocTemplate(str(dest), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                                topMargin=MARGIN, bottomMargin=MARGIN)
        doc.build(story)
