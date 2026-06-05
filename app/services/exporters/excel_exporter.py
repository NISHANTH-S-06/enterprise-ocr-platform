"""
Excel export engine.

fill_config shape (per template):
{
  "fields": {
    "invoice_number": {"sheet": "Invoice", "cell": "B3"},
    "total_amount":   {"sheet": "Invoice", "cell": "E40", "format": "#,##0.00"}
  },
  "line_items": {
    "sheet":      "Invoice",
    "start_row":  15,
    "columns": {
      "description": 1,
      "quantity":    2,
      "unit_price":  3,
      "tax_rate":    4,
      "total":       5
    }
  }
}

If no fill_config / template file is provided, a generic invoice sheet is generated.
"""
from __future__ import annotations
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from app.core.config import get_settings

settings = get_settings()

_HEADER_FILL  = PatternFill("solid", fgColor="1F4E79")
_HEADER_FONT  = Font(bold=True, color="FFFFFF", size=10)
_BORDER_SIDE  = Side(style="thin", color="D9D9D9")
_CELL_BORDER  = Border(left=_BORDER_SIDE, right=_BORDER_SIDE, top=_BORDER_SIDE, bottom=_BORDER_SIDE)
_LABEL_FONT   = Font(bold=True, size=10)
_VALUE_FONT   = Font(size=10)
_ALT_FILL     = PatternFill("solid", fgColor="F2F2F2")


class ExcelExporter:
    def export(
        self,
        fields:       Dict[str, Any],
        line_items:   List[Dict[str, Any]],
        job_id:       uuid.UUID,
        fill_config:  Optional[Dict[str, Any]] = None,
        template_path: Optional[str]           = None,
    ) -> Path:
        dest = settings.output_path / f"{job_id}.xlsx"

        if template_path and Path(template_path).exists() and fill_config:
            wb = load_workbook(template_path)
            self._fill_template(wb, fields, line_items, fill_config)
        else:
            wb = self._build_generic(fields, line_items)

        wb.save(dest)
        logger.info(f"Excel export: {dest}")
        return dest

    # ── template-based fill ───────────────────────────────────────────────────

    def _fill_template(
        self,
        wb:         Workbook,
        fields:     Dict[str, Any],
        line_items: List[Dict[str, Any]],
        cfg:        Dict[str, Any],
    ) -> None:
        field_cfg = cfg.get("fields", {})
        for field_name, coord in field_cfg.items():
            val = fields.get(field_name)
            if val is None:
                continue
            sheet_name = coord.get("sheet") if isinstance(coord, dict) else None
            cell_ref   = coord.get("cell")  if isinstance(coord, dict) else coord
            ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
            if cell_ref:
                if isinstance(val, dict):
                    val = val.get('amount', val.get('value', str(val)))
                ws[cell_ref] = val

        li_cfg = cfg.get("line_items", {})
        if li_cfg and line_items:
            sheet_name = li_cfg.get("sheet")
            start_row  = li_cfg.get("start_row", 1)
            col_map    = li_cfg.get("columns", {})
            ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
            for r_off, item in enumerate(line_items):
                for fname, col_idx in col_map.items():
                    ws.cell(row=start_row + r_off, column=col_idx, value=item.get(fname))

    # ── generic document builder ──────────────────────────────────────────────

    def _build_generic(self, fields: Dict[str, Any], line_items: List[Dict[str, Any]]) -> Workbook:
        wb = Workbook()
        ws = wb.active
        ws.title = "Document"
        ws.column_dimensions["A"].width = 28
        ws.column_dimensions["B"].width = 42

        # Title
        ws.merge_cells("A1:B1")
        title_cell = ws["A1"]
        title_cell.value = str(fields.get("vendor_name", "DOCUMENT"))
        title_cell.font  = Font(bold=True, size=14, color="1F4E79")
        title_cell.alignment = Alignment(horizontal="center")

        # Field key/value pairs
        row = 3
        priority_fields = [
            "invoice_number", "invoice_date", "due_date", "po_number",
            "vendor_name", "customer_name", "vendor_gstin", "customer_gstin",
            "subtotal", "cgst", "sgst", "igst", "total_tax", "total_amount",
            "amount_in_words", "payment_terms", "bank_account", "ifsc_code",
        ]
        rendered = set()

        def _write_field(label: str, value: Any, alt: bool = False) -> None:
            nonlocal row
            fill = _ALT_FILL if alt else PatternFill()
            for col in (1, 2):
                ws.cell(row=row, column=col).fill   = fill
                ws.cell(row=row, column=col).border = _CELL_BORDER
            ws.cell(row=row, column=1).value = label.replace("_", " ").title()
            ws.cell(row=row, column=1).font  = _LABEL_FONT
            ws.cell(row=row, column=2).value = str(value) if value is not None else ""
            ws.cell(row=row, column=2).font  = _VALUE_FONT
            row += 1

        alt = False
        for fname in priority_fields:
            if fname in fields and fields[fname] is not None:
                _write_field(fname, fields[fname], alt)
                rendered.add(fname)
                alt = not alt

        for fname, fval in fields.items():
            if fname not in rendered and fval is not None:
                _write_field(fname, fval, alt)
                alt = not alt

        # Line items table
        if line_items:
            row += 1
            all_cols = list(dict.fromkeys(k for item in line_items for k in item))
            # Header
            for ci, col_name in enumerate(all_cols, 1):
                c = ws.cell(row=row, column=ci, value=col_name.replace("_", " ").title())
                c.fill, c.font, c.border = _HEADER_FILL, _HEADER_FONT, _CELL_BORDER
                c.alignment = Alignment(horizontal="center")
                ws.column_dimensions[get_column_letter(ci)].width = max(15, len(col_name) + 4)
            row += 1
            for idx, item in enumerate(line_items):
                alt_li = PatternFill("solid", fgColor="EBF3FB") if idx % 2 == 0 else PatternFill()
                for ci, col_name in enumerate(all_cols, 1):
                    c = ws.cell(row=row, column=ci, value=item.get(col_name, ""))
                    c.border = _CELL_BORDER
                    c.fill   = alt_li
                    c.font   = _VALUE_FONT
                row += 1

        return wb
