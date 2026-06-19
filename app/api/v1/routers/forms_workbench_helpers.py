"""
forms_workbench_helpers.py — Workbench field-mapping utilities and core business logic.
Extracted from forms.py for maintainability.

Public API (imported by forms.py and forms_workbench_routes.py):
  WORKBENCH_TARGET_DOM_IDS, WORKBENCH_ALIASES, WORKBENCH_ROW_ALIASES
  NUMERIC_WORKBENCH_TARGETS, IDENTIFIER_WORKBENCH_TARGETS, LABEL_ONLY_WORKBENCH_VALUES
  SALES_HEADER_KEYS, PURCHASE_HEADER_KEYS, SALES_LINE_KEYS, PURCHASE_LINE_KEYS
  normalize_workbench_key, clean_workbench_value, is_valid_workbench_value
  normalize_dataset_mappings, first_row_identifier
  extract_workbench_ocr_fields, extract_workbench_rows, _workbench_document_type
  build_workbench_auto_fill, _normalize_workbench_payload, validate_workbench_payload
  _wb_value, _wb_number, _wb_key, _field_entries
  _workbench_export_payload, build_erp_workbench_payload, _pick, _erp_line
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, List, Optional

from fastapi import HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.services.export_precheck_engine import build_export_precheck


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class WorkbenchRequest(BaseModel):
    module: str
    document_type: Optional[str] = None
    dataset_id: Optional[str] = None
    source_job_id: Optional[str] = None
    fields: dict[str, Any] = Field(default_factory=dict)
    rows: List[dict[str, Any]] = Field(default_factory=list)
    form_data: dict[str, Any] = Field(default_factory=dict)
    item_rows: List[dict[str, Any]] = Field(default_factory=list)
    validation_result: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    ocr_fields: dict[str, Any] = Field(default_factory=dict)
    ocr_rows: List[dict[str, Any]] = Field(default_factory=list)
    mappings_json: List[dict[str, Any]] = Field(default_factory=list)
    row_mappings_json: List[dict[str, Any]] = Field(default_factory=list)
    selected_dataset: dict[str, Any] = Field(default_factory=dict)
    mapped_fields: dict[str, Any] = Field(default_factory=dict)
    resolved_masters: List[dict[str, Any]] = Field(default_factory=list)
    unresolved_masters: List[dict[str, Any]] = Field(default_factory=list)
    confidence_map: dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    source_type: Optional[str] = None
    title: Optional[str] = None


class WorkbenchExportRequest(WorkbenchRequest):
    pass


class WorkbenchAutoFillRequest(BaseModel):
    module: str
    job_id: Optional[str] = None
    ocr_result: Optional[dict[str, Any]] = None
    dataset_id: Optional[str] = None
    overwrite: bool = True
    raw_text: Optional[str] = None


class WorkbenchMatchTrainingRequest(BaseModel):
    module: str
    raw_text: str
    document_type: Optional[str] = None
    company_name: Optional[str] = None
    vendor_name: Optional[str] = None
    customer_name: Optional[str] = None
    gst_number: Optional[str] = None


# ── Constants ─────────────────────────────────────────────────────────────────

WORKBENCH_TARGET_DOM_IDS = {
    "sales": {
        "sales_order_no": "sales_form_sales_order_no", "sales_order_date": "sales_form_sales_order_date",
        "order_type_id": "sales_form_order_type_id", "reference_number": "sales_form_reference_number",
        "order_tax": "sales_form_order_tax", "order_total": "sales_form_order_total",
        "qty_total": "sales_form_qty_total", "source": "sales_form_source", "created_by": "sales_form_created_by",
        "customer_type_id": "sales_form_customer_type_id", "pricelist_id": "sales_form_pricelist_id",
        "customer_id": "sales_form_customer_id", "customer_name": "sales_form_customer_name",
        "customer_po_number": "sales_form_customer_po_number", "bill_to_address": "sales_form_bill_to_address",
        "ship_to_address": "sales_form_ship_to_address", "delivery_date": "sales_form_delivery_date",
        "remarks": "sales_form_remarks", "contact_number": "sales_form_contact_number",
        "contact_person": "sales_form_contact_person", "project_id": "sales_form_project_id",
        "salesperson_id": "sales_form_salesperson_id", "payment_method": "sales_form_payment_method",
        "ar_payment_term_id": "sales_form_payment_term", "freight_term": "sales_form_freight_term",
        "delivery_name": "sales_form_delivery_name", "carrier_name": "sales_form_carrier_name",
        "invoice_currency": "sales_form_invoice_currency", "discount_id": "sales_form_discount_id",
        "other_tax_amount": "sales_form_other_tax_amount", "other_frieght_amount": "sales_form_other_frieght_amount",
        "transport_charges": "sales_form_transport_charges", "insurance_charges": "sales_form_insurance_charges",
        "packaging_charges": "sales_form_packaging_charges",
    },
    "purchase": {
        "po_number": "purchase_form_po_number", "po_date": "purchase_form_po_date",
        "po_type": "purchase_form_po_type", "reference_number": "purchase_form_reference_number",
        "po_tax_total": "purchase_form_po_tax_total", "po_grand_total": "purchase_form_po_grand_total",
        "round_off": "purchase_form_round_off", "source": "purchase_form_source",
        "created_by": "purchase_form_created_by", "supplier_id": "purchase_form_supplier_id",
        "supplier_name": "purchase_form_supplier_name", "supplier_site": "purchase_form_supplier_site",
        "reverse_charge": "purchase_form_reverse_charge", "delivery_date": "purchase_form_delivery_date",
        "purchase_type": "purchase_form_purchase_type", "project_id": "purchase_form_project_id",
        "delivery_term": "purchase_form_delivery_term", "payment_term_id": "purchase_form_payment_term",
        "supplier_reference_no": "purchase_form_supplier_reference_no", "freight_term": "purchase_form_freight_term",
        "remarks": "purchase_form_remarks", "insurance_term": "purchase_form_insurance_term",
        "currency": "purchase_form_currency", "bill_to_location": "purchase_form_bill_to_location",
        "bill_to_address": "purchase_form_bill_to_address", "ship_to_location": "purchase_form_ship_to_location",
        "ship_to_address": "purchase_form_ship_to_address", "payment_method": "purchase_form_payment_method",
        "freight_carrier": "purchase_form_freight_carrier", "packing_charges": "purchase_form_packing_charges",
        "insurance_charges": "purchase_form_insurance_charges", "unloading_charges": "purchase_form_unloading_charges",
        "transport_charges": "purchase_form_transport_charges", "other_frieght_amount": "purchase_form_other_frieght_amount",
        "other_tax_amount": "purchase_form_other_tax_amount",
    },
}

WORKBENCH_ALIASES = {
    "sales": {
        "buyer_name": "customer_name", "buyer": "customer_name", "customer": "customer_name",
        "customer_name": "customer_name", "delivery_name": "delivery_name",
        "buyer_order_no": "customer_po_number", "buyers_order_no": "customer_po_number",
        "po_number": "customer_po_number", "purchase_order_no": "customer_po_number",
        "invoice_no": "reference_number", "invoice_number": "reference_number",
        "order_no": "sales_order_no", "order_number": "sales_order_no", "sales_order_no": "sales_order_no",
        "invoice_date": "sales_order_date", "order_date": "sales_order_date", "date": "sales_order_date",
        "grand_total": "order_total", "total_amount": "order_total", "amount_chargeable": "order_total",
        "tax_amount": "order_tax", "igst_amount": "order_tax", "payment_terms": "ar_payment_term_id",
        "payment_term": "ar_payment_term_id", "delivery_terms": "delivery_name",
        "currency": "invoice_currency", "invoice_currency": "invoice_currency",
        "remarks": "remarks", "notes": "remarks", "billing_address": "bill_to_address",
        "buyer_address": "bill_to_address", "shipping_address": "ship_to_address",
        "consignee_address": "ship_to_address", "ship_to_address": "ship_to_address",
        "order_type": "order_type_id", "order_type_id": "order_type_id",
        "qty_total": "qty_total", "quantity_total": "qty_total",
        "source": "source", "created_by": "created_by",
        "customer_type": "customer_type_id", "customer_type_id": "customer_type_id",
        "pricelist_id": "pricelist_id", "price_list": "pricelist_id",
        "contact_number": "contact_number", "contact_person": "contact_person",
        "project": "project_id", "project_id": "project_id",
        "salesperson": "salesperson_id", "salesperson_id": "salesperson_id", "sales_person": "salesperson_id",
        "payment_method": "payment_method", "freight_term": "freight_term", "carrier_name": "carrier_name",
        "discount": "discount_id", "discount_id": "discount_id",
        "other_tax_amount": "other_tax_amount", "other_freight_amount": "other_frieght_amount",
        "other_frieght_amount": "other_frieght_amount", "transport_charges": "transport_charges",
        "insurance_charges": "insurance_charges", "packaging_charges": "packaging_charges",
        "customer_id": "customer_id", "customer_code": "customer_id", "client_id": "customer_id", "client_code": "customer_id",
    },
    "purchase": {
        "po_no": "po_number", "po_number": "po_number", "purchase_order_no": "po_number",
        "purchase_order_number": "po_number", "po_date": "po_date",
        "supplier": "supplier_name", "supplier_name": "supplier_name", "vendor": "supplier_name",
        "vendor_name": "supplier_name", "grand_total": "po_grand_total", "total_amount": "po_grand_total",
        "total": "po_grand_total", "tax_total": "po_tax_total", "tax_amount": "po_tax_total",
        "roundoff": "round_off", "round_off": "round_off", "delivery_date": "delivery_date",
        "payment_terms": "payment_term_id", "payment_term": "payment_term_id", "currency": "currency",
        "remarks": "remarks", "notes": "remarks", "reference_number": "reference_number",
        "supplier_reference_no": "supplier_reference_no", "billing_address": "bill_to_address",
        "supplier_address": "supplier_site", "shipping_address": "ship_to_address",
        "po_type": "po_type", "source": "source", "created_by": "created_by",
        "supplier_site": "supplier_site", "reverse_charge": "reverse_charge",
        "purchase_type": "purchase_type", "project": "project_id", "project_id": "project_id",
        "delivery_term": "delivery_term", "delivery_terms": "delivery_term",
        "supplier_ref": "supplier_reference_no", "supplier_ref_no": "supplier_reference_no",
        "freight_term": "freight_term", "insurance_term": "insurance_term",
        "bill_to_location": "bill_to_location", "ship_to_location": "ship_to_location",
        "payment_method": "payment_method", "freight_carrier": "freight_carrier",
        "packing_charges": "packing_charges", "packaging_charges": "packing_charges",
        "insurance_charges": "insurance_charges", "unloading_charges": "unloading_charges",
        "transport_charges": "transport_charges", "other_freight_amount": "other_frieght_amount",
        "other_frieght_amount": "other_frieght_amount", "other_tax_amount": "other_tax_amount",
        "supplier_id": "supplier_id", "vendor_id": "supplier_id", "supplier_code": "supplier_id", "vendor_code": "supplier_id",
    },
}

WORKBENCH_ROW_ALIASES = {
    "sales": {
        "description": "description", "product": "product", "item": "product", "article": "product",
        "hsn": "bulk_hsn_code", "hsn_code": "bulk_hsn_code", "qty": "bulk_qty", "quantity": "bulk_qty",
        "rate": "bulk_unit_price", "price": "bulk_unit_price", "unit_price": "bulk_unit_price",
        "amount": "bulk_line_total", "line_total": "bulk_line_total", "total": "bulk_line_total",
        "tax": "bulk_tax_amount", "igst": "bulk_tax_amount", "gst": "bulk_tax_amount",
        "tax_amount": "bulk_tax_amount", "uom": "bulk_uom_code", "uom_code": "bulk_uom_code",
        "size": "size", "colour": "colour", "color": "colour",
        "line_no": "line_no", "sno": "line_no",
        "product_id": "product_id",
        "discount_percent": "bulk_discount_percentage", "discount_percentage": "bulk_discount_percentage",
        "discount_amount": "bulk_discount_amount",
        "tax_exemption": "bulk_tax_excemption", "tax_excemption": "bulk_tax_excemption",
        "tax_group": "bulk_tax_group", "tax_group_id": "tax_group_id",
        "qoh_qty": "bulk_qoh_qty", "delivery_date": "bulk_delivery_date",
    },
    "purchase": {
        "description": "description", "product": "product", "item": "product", "article": "product",
        "part_no": "supplier_part_no", "hsn": "bulk_hsn_code", "hsn_code": "bulk_hsn_code",
        "qty": "bulk_qty", "quantity": "bulk_qty", "rate": "bulk_unit_price", "price": "bulk_unit_price",
        "unit_price": "bulk_unit_price", "amount": "bulk_line_total", "line_total": "bulk_line_total",
        "total": "bulk_line_total", "tax": "bulk_tax_amount", "gst": "bulk_tax_amount",
        "tax_amount": "bulk_tax_amount", "uom": "bulk_uom_code", "uom_code": "bulk_uom_code",
        "comments": "comments", "remarks": "comments",
        "line_no": "line_no", "sno": "line_no",
        "product_id": "product_id",
        "supplier_part_no": "supplier_part_no", "part_number": "supplier_part_no",
        "discount_percent": "bulk_discount_percentage", "discount_percentage": "bulk_discount_percentage",
        "discount_amount": "bulk_discount_amount",
        "tax_group": "bulk_tax_group", "tax_group_id": "tax_group_id",
        "sub_total": "bulk_line_sub_total", "line_sub_total": "bulk_line_sub_total",
        "conv_cost": "bulk_conv_cost", "conversion_cost": "bulk_conv_cost",
        "blistering_cost": "bulk_blistering_cost",
        "delivery_date": "promised_date", "promised_date": "promised_date",
    },
}

NUMERIC_WORKBENCH_TARGETS = {
    "order_total", "order_tax", "qty_total", "po_grand_total", "po_tax_total", "round_off",
    "other_tax_amount", "other_frieght_amount", "transport_charges", "insurance_charges", "packing_charges",
    "packaging_charges", "unloading_charges", "bulk_qty", "bulk_unit_price", "bulk_line_total", "bulk_tax_amount",
    "bulk_discount_percentage", "bulk_discount_amount", "bulk_line_sub_total", "bulk_conv_cost", "bulk_blistering_cost", "bulk_qoh_qty",
}
IDENTIFIER_WORKBENCH_TARGETS = {
    "sales_order_no", "customer_po_number", "reference_number",
    "po_number", "supplier_reference_no",
}
LABEL_ONLY_WORKBENCH_VALUES = {
    "group", "order", "order_no", "order_number", "po", "po_no",
    "invoice", "invoice_no", "invoice_number", "description", "amount", "total",
}
CURRENT_VALUE_REQUIRED_TARGETS = {
    "sales_order_no", "customer_po_number", "reference_number", "po_number",
    "po_date", "sales_order_date", "delivery_date", "supplier_reference_no",
    "customer_name", "supplier_name", "order_total", "order_tax", "qty_total",
    "po_grand_total", "po_tax_total", "packing_charges", "packaging_charges",
}

SALES_HEADER_KEYS = (
    "sales_order_no", "sales_order_date", "order_type_id", "customer_type_id",
    "customer_id", "ship_to_customer_id", "bill_to_address", "ship_to_address",
    "delivery_date", "customer_po_number", "currency_code_id", "payment_term_id",
    "freight_terms_id", "delivery_terms_id", "remarks", "order_tax", "order_total",
    "qty_total",
)
PURCHASE_HEADER_KEYS = (
    "po_number", "po_date", "po_type", "supplier_id", "suppliername", "suppliersite_id",
    "delivery_date", "payment_term_id", "freight_terms_id", "freight_carrier_id",
    "delivery_terms_id", "project_id", "currency", "supplier_reference_no",
    "bill_to_address", "ship_to_address", "transport_charges", "unloading_charges",
    "insurance_charges", "packing_charges", "po_tax_total", "po_grand_total",
    "round_off", "remarks",
)
SALES_LINE_KEYS = (
    "line_no", "product", "product_id", "description", "uom_code_id", "qty",
    "unit_price", "discount_percentage", "discount_amount", "hsn_code", "tax_group_id",
    "tax_amount", "line_total", "delivery_date",
)
PURCHASE_LINE_KEYS = (
    "line_no", "product", "product_id", "part_no", "description", "uom_code_id", "qty",
    "unit_price", "discount_percentage", "discount_amount", "hsn_code", "tax_group_id",
    "tax_amount", "line_sub_total", "line_total", "promised_date", "comments",
)


# ── Utility functions ─────────────────────────────────────────────────────────

def _wb_value(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value", value.get("text", value.get("raw_value", "")))
        if isinstance(value, dict):
            return _wb_value(value)
    return str(value or "").strip()


def _wb_number(value: Any) -> float | None:
    text = _wb_value(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _wb_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def normalize_workbench_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).replace("\u2018", "'").replace("\u2019", "'")
    text = text.lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def clean_workbench_value(value: Any, target: str = "") -> str:
    seen: set[int] = set()
    while isinstance(value, dict):
        marker = id(value)
        if marker in seen:
            return ""
        seen.add(marker)
        next_value = None
        for key in ("value", "text", "raw_value", "normalized_value", "extracted_value"):
            if key in value and value[key] is not None:
                next_value = value[key]
                break
        if next_value is None:
            return ""
        value = next_value
    if isinstance(value, (list, tuple, set)) or value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text or text == "[object Object]":
        return ""
    if target in NUMERIC_WORKBENCH_TARGETS:
        text = re.sub(r"(?:INR|USD|EUR|GBP|Rs\.?)(?=\s|\d)", "", text, flags=re.IGNORECASE)
        text = re.sub(r"[$€£₹]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
    return text


def is_valid_date(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    if len(text) > 30:
        return False
    months = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    date_pattern = re.compile(
        r"(?:"
        r"\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}"
        r"|\b\d{1,2}\s+" + months + r"\w*\s+\d{2,4}\b"
        r"|\b" + months + r"\w*\s+\d{1,2},?\s+\d{2,4}\b"
        r")",
        re.IGNORECASE
    )
    return bool(date_pattern.search(text))


def is_valid_name(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    if is_valid_date(text):
        return False
    if len(text) > 100 or re.search(r"\b(?:road|street|st|ave|blvd|lane|ln|floor|fl|building|bldg|sector|phase|near|opp|opposite|city|state|dist|district|pin|zip|india)\b", text.lower()):
        return False
    if re.match(r"^(?:invoice|po|date|gstin|total|amount|rate|qty|tax|hsn|uom)", text.lower()):
        return False
    letters = sum(1 for c in text if c.isalpha())
    digits = sum(1 for c in text if c.isdigit())
    if digits > letters and digits > 3:
        return False
    return True


def is_valid_id(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    if is_valid_date(text):
        return False
    if text.count(" ") > 3:
        return False
    if len(text) > 40:
        return False
    return True


def is_valid_amount(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    cleaned = re.sub(r"(?:INR|USD|EUR|GBP|Rs\.?)(?=\s|\d)", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"[$€£₹,]", "", cleaned).strip()
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


def field_label_supports_target(label: Any, target: str) -> bool:
    key = normalize_workbench_key(label)
    target = normalize_workbench_key(target)
    if not key or not target:
        return False
    if key == target:
        return True
    for mod in ("sales", "purchase"):
        if target in WORKBENCH_TARGET_DOM_IDS[mod]:
            if WORKBENCH_ALIASES[mod].get(key) == target:
                return True
    if target == "po_date":
        return key in {"po_date", "po_dt", "purchase_order_date", "purchase_order_dt"}
    if target == "sales_order_date":
        return key in {"sales_order_date", "order_date", "invoice_date", "document_date", "doc_date"}
    if target == "delivery_date":
        return key in {"delivery_date", "dispatch_date", "ship_date", "shipment_date", "promised_date"}
    if target == "supplier_name":
        return key in {"supplier", "supplier_name", "vendor", "vendor_name", "seller", "seller_name", "billed_by", "exporter"}
    if target == "customer_name":
        return key in {"customer", "customer_name", "buyer", "buyer_name", "client", "client_name", "consignee", "consignee_name", "billed_to", "bill_to", "sold_to"}
    if target in {"po_number", "customer_po_number", "sales_order_no", "reference_number", "supplier_reference_no"}:
        allowed = {
            "po_number", "po_no", "purchase_order_no", "purchase_order_number",
            "customer_po_number", "buyer_order_no", "buyers_order_no",
            "sales_order_no", "order_no", "order_number", "invoice_no", "invoice_number",
            "reference_number", "ref_no", "supplier_reference_no", "supplier_ref_no",
        }
        return key in allowed
    if target in NUMERIC_WORKBENCH_TARGETS:
        if target in {"packing_charges", "packaging_charges"}:
            return key in {"packing_charges", "packaging_charges", "package_charges"}
        numeric_aliases = {
            "bulk_qty": {"qty", "quantity", "bulk_qty"},
            "bulk_unit_price": {"rate", "price", "unit_price", "bulk_unit_price"},
            "bulk_line_total": {"amount", "total", "line_total", "bulk_line_total"},
            "bulk_tax_amount": {"tax", "gst", "igst", "tax_amount", "bulk_tax_amount"},
            "bulk_discount_percentage": {"discount_percent", "discount_percentage", "bulk_discount_percentage"},
            "bulk_discount_amount": {"discount_amount", "bulk_discount_amount"},
            "bulk_line_sub_total": {"sub_total", "line_sub_total", "bulk_line_sub_total"},
        }
        if target in numeric_aliases:
            return key in numeric_aliases[target]
        return key == target or key.endswith(target)
    return True


def is_valid_row_value(value: Any, target: str) -> bool:
    text = clean_workbench_value(value, target)
    if not text:
        return False
    if target in NUMERIC_WORKBENCH_TARGETS:
        return is_valid_amount(text)
    if target in {"product", "description"}:
        return is_valid_name(text) or bool(re.search(r"[A-Za-z]", text))
    if target in {"product_id", "tax_group_id"}:
        return is_valid_id(text)
    if target in {"bulk_delivery_date", "promised_date"}:
        return is_valid_date(text)
    return True


def is_valid_workbench_value(value: Any, target: str) -> bool:
    text = clean_workbench_value(value, target)
    if not text:
        return False
    normalized = normalize_workbench_key(text)
    if target in IDENTIFIER_WORKBENCH_TARGETS:
        if normalized in LABEL_ONLY_WORKBENCH_VALUES:
            return False
        if not re.search(r"\d", text):
            return False

    # Check Date fields
    if target in {
        "sales_order_date", "delivery_date", "po_date", "bulk_delivery_date", "promised_date"
    }:
        if not is_valid_date(text):
            return False

    # Check Name fields
    if target in {
        "customer_name", "supplier_name", "delivery_name", "carrier_name", "contact_person", "supplier_site"
    }:
        if not is_valid_name(text):
            return False

    # Check ID fields
    if target in {
        "sales_order_no", "customer_po_number", "reference_number", "order_type_id", "customer_type_id",
        "pricelist_id", "customer_id", "project_id", "salesperson_id", "discount_id", "po_number",
        "supplier_id", "payment_term_id", "supplier_reference_no", "bill_to_location",
        "ship_to_location", "product_id", "tax_group_id"
    }:
        if not is_valid_id(text):
            return False

    # Check Amount fields
    if target in NUMERIC_WORKBENCH_TARGETS:
        if not is_valid_amount(text):
            return False

    return True


def first_row_identifier(rows: list[dict[str, Any]], keys: list[str]) -> str:
    seen: list[str] = []
    key_set = {normalize_workbench_key(key) for key in keys}
    for row in rows:
        for raw_key, raw_value in (row.items() if isinstance(row, dict) else []):
            if normalize_workbench_key(raw_key) not in key_set:
                continue
            value = clean_workbench_value(raw_value, "customer_po_number")
            if value and is_valid_workbench_value(value, "customer_po_number") and value not in seen:
                seen.append(value)
    return ", ".join(seen)


def normalize_dataset_mappings(raw: Any) -> list[dict[str, str]]:
    """Normalize any historical or canonical mapping format into the canonical list schema."""
    mappings: list[dict[str, str]] = []
    if isinstance(raw, dict):
        first_val = next(iter(raw.values()), None) if raw else None
        if isinstance(first_val, dict) and ("label" in first_val or "confidence" in first_val or "value" in first_val):
            for target, meta in raw.items():
                if not isinstance(meta, dict):
                    continue
                source_label = str(meta.get("label") or target).strip()
                if not source_label or not target:
                    continue
                mappings.append({
                    "source_key": source_label,
                    "source_label": source_label,
                    "normalized_source_key": normalize_workbench_key(source_label),
                    "target_erp_name": normalize_workbench_key(target),
                    "target_dom_id": "",
                    "sample_value": clean_workbench_value(meta.get("value") or meta.get("sample_value") or ""),
                    "confidence": str(meta.get("confidence") or ""),
                })
        else:
            raw = [{"source_key": s, "target_erp_name": t} for s, t in raw.items()]
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        source = (
            item.get("source_key") or item.get("ocr_key") or item.get("label")
            or item.get("source_label") or item.get("field") or item.get("source")
        )
        target = (
            item.get("target_erp_name") or item.get("target_field") or item.get("target")
            or item.get("erp_field_name") or item.get("erp_field") or item.get("value")
        )
        source_text = str(source or "").strip()
        target_text = str(target or "").strip()
        if not source_text or not target_text:
            continue
        if source_text == target_text and source_text in {"", "-", "none", "null"}:
            continue
        source_label = str(item.get("source_label") or item.get("label") or source_text)
        mappings.append({
            "source_key": source_text,
            "source_label": source_label,
            "normalized_source_key": normalize_workbench_key(source_text),
            "target_erp_name": normalize_workbench_key(target_text),
            "canonical_name": str(item.get("canonical_name") or normalize_workbench_key(target_text)),
            "target_dom_id": str(item.get("target_dom_id") or ""),
            "sample_value": clean_workbench_value(item.get("sample_value") or item.get("sample") or ""),
            "confidence": str(item.get("confidence") or ""),
        })
    return mappings


def _field_entries(source: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if isinstance(source, dict):
        for key, value in source.items():
            if normalize_workbench_key(key) in {"items", "item_rows", "line_items", "rows", "tables", "table_rows"}:
                continue
            entries.append({"ocr_key": str(key), "ocr_value": value, "normalized_key": normalize_workbench_key(key)})
    elif isinstance(source, list):
        for item in source:
            if not isinstance(item, dict):
                continue
            key = item.get("field_name") or item.get("name") or item.get("key") or item.get("label")
            if key:
                entries.append({"ocr_key": str(key), "ocr_value": item, "normalized_key": normalize_workbench_key(key)})
    return entries


def extract_workbench_ocr_fields(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    containers = [ocr_result]
    for key in ("ocr_response", "ocr_result"):
        if isinstance(ocr_result.get(key), dict):
            containers.append(ocr_result[key])
    source_names = ("fields", "extracted_fields", "review_fields", "mapped_fields", "canonical_fields", "validated_fields", "p2_fields")
    for container in containers:
        for name in source_names:
            for entry in _field_entries(container.get(name)):
                value = clean_workbench_value(entry["ocr_value"])
                if re.match(r"^(?:po_)?item_?\d+_", entry["normalized_key"]):
                    continue
                if not value and not isinstance(entry["ocr_value"], dict):
                    continue
                signature = (entry["normalized_key"], value)
                if entry["normalized_key"] and signature not in seen:
                    seen.add(signature)
                    entries.append(entry)
    return entries


def extract_workbench_rows(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("rows"), list):
                    add(item["rows"])
                elif isinstance(item, dict):
                    rows.append(item)
                elif isinstance(item, list):
                    add(item)
        elif isinstance(value, dict):
            if isinstance(value.get("value"), list):
                add(value["value"])
            elif isinstance(value.get("rows"), list):
                add(value["rows"])

    containers = [ocr_result]
    for key in ("ocr_response", "ocr_result"):
        if isinstance(ocr_result.get(key), dict):
            containers.append(ocr_result[key])
    for container in containers:
        for key in ("items", "item_rows", "line_items", "rows", "table_rows", "tables"):
            add(container.get(key))
        for key in ("mapped_fields", "canonical_fields", "validated_fields"):
            nested = container.get(key)
            if isinstance(nested, dict):
                add(nested.get("items") or nested.get("item_rows") or nested.get("line_items"))
    unique: list[dict[str, Any]] = []
    signatures: set[str] = set()
    for row in rows:
        signature = json.dumps(row, sort_keys=True, default=str)
        if signature not in signatures:
            signatures.add(signature)
            unique.append(row)
    return unique


def _workbench_document_type(ocr_result: dict[str, Any]) -> str:
    for source in (ocr_result, ocr_result.get("ocr_response", {}), ocr_result.get("ocr_result", {})):
        if isinstance(source, dict):
            value = source.get("document_type") or source.get("doc_type")
            if value:
                return normalize_workbench_key(value)
    return ""


# ── Core workbench logic ──────────────────────────────────────────────────────

def build_workbench_auto_fill(
    req: WorkbenchAutoFillRequest,
    ocr_result: dict[str, Any],
    dataset: Optional[dict[str, Any]] = None,
    corrections: Optional[list[dict[str, Any]]] = None,
    erp_mappings: Optional[list[Any]] = None,
) -> dict[str, Any]:
    module = "purchase" if normalize_workbench_key(req.module).startswith("purchase") else "sales"
    document_type = _workbench_document_type(ocr_result)
    if module == "sales" and document_type in {"purchase_order", "purchase", "grn", "goods_receipt_note"}:
        raise HTTPException(status_code=422, detail={"message": "Sales form cannot accept a purchase document", "document_type": document_type})
    if module == "purchase" and document_type in {"sales_invoice", "commercial_invoice", "export_invoice", "tax_invoice", "sales_order"}:
        raise HTTPException(status_code=422, detail={"message": "Purchase form cannot accept a sales document", "document_type": document_type})

    targets = WORKBENCH_TARGET_DOM_IDS[module]
    aliases = WORKBENCH_ALIASES[module]
    dataset_mappings = normalize_dataset_mappings((dataset or {}).get("mappings_json") or [])
    entries = extract_workbench_ocr_fields(ocr_result)
    rows = extract_workbench_rows(ocr_result)
    lookup: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        lookup.setdefault(entry["normalized_key"], []).append(entry)

    form_data: dict[str, str] = {}
    matched: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    consumed: set[int] = set()
    dataset_count = fallback_count = direct_count = correction_count = 0

    def canonical_target(target: Any) -> str:
        normalized = normalize_workbench_key(target)
        if normalized in targets:
            return normalized
        for erp_name, dom_id in targets.items():
            if normalize_workbench_key(dom_id) == normalized:
                return erp_name
        return aliases.get(normalized, normalized)

    def apply(entry: dict[str, Any], target: str, source: str) -> bool:
        nonlocal dataset_count, fallback_count, direct_count
        target = canonical_target(target)
        value = clean_workbench_value(entry["ocr_value"], target)
        reason = ""
        is_dataset_ev = False
        if source == "dataset" and dataset_mappings and target != "po_date":
            entry_key = normalize_workbench_key(entry.get("ocr_key") or entry.get("normalized_key"))
            for mapping in dataset_mappings:
                map_target = canonical_target(mapping["target_erp_name"])
                if map_target == target:
                    if entry_key in {mapping["normalized_source_key"], normalize_workbench_key(mapping.get("source_label") or "")}:
                        is_dataset_ev = True
                        break
        if not value:
            reason = "object_value" if isinstance(entry["ocr_value"], dict) else "empty_value"
        elif not is_dataset_ev and not field_label_supports_target(entry.get("ocr_key") or entry.get("normalized_key"), target):
            reason = "label_evidence_mismatch"
        elif not is_valid_workbench_value(value, target):
            reason = "label_only_or_suspicious_value"
        elif target not in targets:
            reason = "target_field_not_found"
        elif target in form_data and form_data[target]:
            consumed.add(id(entry))
            return False
        if reason:
            skipped.append({"ocr_key": entry["ocr_key"], "ocr_value": value, "normalized_key": entry["normalized_key"], "reason": reason})
            return False
        form_data[target] = value
        matched.append({"ocr_key": entry["ocr_key"], "ocr_value": value, "normalized_key": entry["normalized_key"],
                        "target_erp_name": target, "target_dom_id": targets[target], "source": source})
        consumed.add(id(entry))
        if source == "dataset": dataset_count += 1
        elif source == "fallback": fallback_count += 1
        else: direct_count += 1
        return True

    def has_current_document_value(target: str) -> bool:
        target = canonical_target(target)
        for entry in entries:
            direct_target = entry["normalized_key"] if entry["normalized_key"] in targets else aliases.get(entry["normalized_key"])
            if canonical_target(direct_target or "") != target:
                continue
            value = clean_workbench_value(entry["ocr_value"], target)
            if value and field_label_supports_target(entry.get("ocr_key") or entry.get("normalized_key"), target) and is_valid_workbench_value(value, target):
                return True
        return False

    # Step 1: Correction memory (highest priority user-trained values)
    if corrections:
        ocr_fields_flat: dict[str, Any] = {}
        for e in entries:
            ocr_fields_flat[e["ocr_key"]] = e.get("ocr_value", "")
        for raw_row in rows:
            for k, v in (raw_row.items() if isinstance(raw_row, dict) else {}):
                ocr_fields_flat[k] = v
        company_name_val = ocr_result.get("company_name") or ""
        vendor_name_val = ocr_result.get("vendor_name") or ocr_result.get("supplier_name") or ""
        customer_name_val = ocr_result.get("customer_name") or ocr_result.get("buyer_name") or ""

        from app.services.workbench_correction_apply import apply_workbench_corrections
        correction_result = apply_workbench_corrections(
            module=module,
            document_type=document_type,
            company_name=company_name_val,
            vendor_name=vendor_name_val,
            customer_name=customer_name_val,
            ocr_fields=ocr_fields_flat,
            form_data=form_data,
            item_rows=[],
            corrections=corrections,
        )
        for ca in correction_result.get("applied_corrections", []):
            tgt = ca["target_erp_name"]
            val = ca["corrected_value"]
            canonical = canonical_target(tgt)
            if not is_valid_workbench_value(val, canonical):
                skipped.append({"ocr_key": ca["source_ocr_key"], "ocr_value": clean_workbench_value(val, canonical),
                                "normalized_key": ca["normalized_source_key"], "reason": "correction_suspicious_value"})
                continue
            if tgt in targets:
                form_data[tgt] = val
                matched.append({"ocr_key": ca["source_ocr_key"], "ocr_value": val,
                                "normalized_key": ca["normalized_source_key"],
                                "target_erp_name": tgt, "target_dom_id": targets.get(tgt, ""),
                                "source": "correction"})
                correction_count += 1
                for entry in entries:
                    if entry["normalized_key"] == ca["normalized_source_key"] or entry["ocr_key"] == ca["source_ocr_key"]:
                        consumed.add(id(entry))
            elif tgt in aliases:
                canonical = aliases[tgt]
                if canonical in targets:
                    form_data[canonical] = val
                    matched.append({"ocr_key": ca["source_ocr_key"], "ocr_value": val,
                                    "normalized_key": ca["normalized_source_key"],
                                    "target_erp_name": canonical, "target_dom_id": targets.get(canonical, ""),
                                    "source": "correction"})
                    correction_count += 1
                    for entry in entries:
                        if entry["normalized_key"] == ca["normalized_source_key"] or entry["ocr_key"] == ca["source_ocr_key"]:
                            consumed.add(id(entry))
        for cs in correction_result.get("skipped_corrections", []):
            skipped.append({"ocr_key": cs.get("source_ocr_key", ""), "ocr_value": "",
                            "normalized_key": normalize_workbench_key(cs.get("source_ocr_key", "")),
                            "reason": f"correction_skipped:{cs.get('reason', 'unknown')}"})

    # Step 2: Direct matches & Fallback aliases (Current document values get next priority)
    canonical_names = {normalize_workbench_key(key) for key in (ocr_result.get("canonical_fields") or {}).keys()} if isinstance(ocr_result.get("canonical_fields"), dict) else set()
    # 2A. Direct via canonical names
    for entry in entries:
        if id(entry) in consumed:
            continue
        key = entry["normalized_key"]
        if key in targets and key in canonical_names:
            apply(entry, key, "direct")
    # 2B. Fallback aliases
    for entry in entries:
        if id(entry) in consumed:
            continue
        target = aliases.get(entry["normalized_key"])
        if target:
            apply(entry, target, "fallback")
    # 2C. Direct ERP key match
    for entry in entries:
        if id(entry) in consumed:
            continue
        if entry["normalized_key"] in targets:
            apply(entry, entry["normalized_key"], "direct")

    # Step 3: Dataset layout mappings (extracting OCR fields from the current document using dataset template keys)
    from app.services.training_template_engine import is_dynamic_field

    # Strictly check dataset module compatibility if it exists
    dataset_module = getattr(dataset, "module", None) or (dataset or {}).get("module")
    if dataset_module:
        dataset_module_norm = normalize_workbench_key(dataset_module)
        if module == "sales" and "purchase" in dataset_module_norm:
            raise HTTPException(status_code=422, detail={"message": "Sales form cannot accept a purchase dataset", "dataset_module": dataset_module})
        if module == "purchase" and "sales" in dataset_module_norm:
            raise HTTPException(status_code=422, detail={"message": "Purchase form cannot accept a sales dataset", "dataset_module": dataset_module})

    for mapping in dataset_mappings:
        target_field = mapping["target_erp_name"]
        target_canonical = canonical_target(target_field)
        if target_canonical in form_data and form_data[target_canonical]:
            continue
        candidates = lookup.get(mapping["normalized_source_key"], [])
        if not candidates and mapping.get("source_label"):
            candidates = lookup.get(normalize_workbench_key(mapping["source_label"]), [])
        if candidates:
            apply(candidates[-1], target_canonical, "dataset")

    # Step 4: Dataset fallback/training sample values (only if still empty and approved reusable stable fields)
    for mapping in dataset_mappings:
        target_field = mapping["target_erp_name"]
        target_canonical = canonical_target(target_field)
        if target_canonical in form_data and form_data[target_canonical]:
            continue
        candidates = lookup.get(mapping["normalized_source_key"], [])
        if not candidates and mapping.get("source_label"):
            candidates = lookup.get(normalize_workbench_key(mapping["source_label"]), [])
        if not candidates:
            if not is_dynamic_field(target_canonical):
                fallback_val = str(mapping.get("sample_value") or mapping.get("value") or "").strip()
                if fallback_val and target_canonical in targets:
                    cleaned_fallback = clean_workbench_value(fallback_val, target_canonical)
                    if cleaned_fallback and is_valid_workbench_value(cleaned_fallback, target_canonical):
                        form_data[target_canonical] = cleaned_fallback
                        matched.append({
                            "ocr_key": mapping["source_key"],
                            "ocr_value": cleaned_fallback,
                            "normalized_key": mapping["normalized_source_key"],
                            "target_erp_name": target_canonical,
                            "target_dom_id": targets[target_canonical],
                            "source": "training_dataset",
                            "value_source": "training_dataset",
                        })
                        dataset_count += 1
                    else:
                        skipped.append({"ocr_key": mapping["source_key"], "ocr_value": fallback_val,
                                        "normalized_key": mapping["normalized_source_key"], "reason": "invalid_fallback_type"})
            else:
                skipped.append({"ocr_key": mapping["source_key"], "ocr_value": "",
                                "normalized_key": mapping["normalized_source_key"], "reason": "source_key_not_found"})

    for entry in entries:
        if id(entry) not in consumed and clean_workbench_value(entry["ocr_value"]):
            skipped.append({"ocr_key": entry["ocr_key"], "ocr_value": clean_workbench_value(entry["ocr_value"]),
                            "normalized_key": entry["normalized_key"], "reason": "no_alias_match"})

    # Step 6: Row mapping
    item_rows: list[dict[str, str]] = []
    row_aliases = WORKBENCH_ROW_ALIASES[module]
    row_dataset = normalize_dataset_mappings((dataset or {}).get("row_mappings_json") or [])
    for row_index, raw_row in enumerate(rows):
        clean_row: dict[str, str] = {"row_index": str(row_index)}
        row_entries = _field_entries(raw_row)
        row_lookup = {entry["normalized_key"]: entry for entry in row_entries}
        row_consumed: set[int] = set()
        for mapping in row_dataset:
            entry = row_lookup.get(mapping["normalized_source_key"])
            if not entry:
                continue
            raw_target = normalize_workbench_key(mapping["target_erp_name"])
            target = row_aliases.get(raw_target, raw_target)
            value = clean_workbench_value(entry["ocr_value"], target)
            if value and field_label_supports_target(entry.get("ocr_key") or entry.get("normalized_key"), target) and is_valid_row_value(value, target):
                clean_row[target] = value
                row_consumed.add(id(entry))
                dataset_count += 1
                matched.append({"ocr_key": f"row {row_index + 1}.{entry['ocr_key']}", "ocr_value": value,
                                "normalized_key": entry["normalized_key"], "target_erp_name": target,
                                "target_dom_id": f"{module}_form_item_{row_index}_{target}", "source": "dataset"})
        for entry in row_entries:
            if id(entry) in row_consumed:
                continue
            target = row_aliases.get(entry["normalized_key"])
            value = clean_workbench_value(entry["ocr_value"], target or "")
            if target and value and field_label_supports_target(entry.get("ocr_key") or entry.get("normalized_key"), target) and is_valid_row_value(value, target):
                clean_row[target] = value
                row_consumed.add(id(entry))
                fallback_count += 1
                matched.append({"ocr_key": f"row {row_index + 1}.{entry['ocr_key']}", "ocr_value": value,
                                "normalized_key": entry["normalized_key"], "target_erp_name": target,
                                "target_dom_id": f"{module}_form_item_{row_index}_{target}", "source": "fallback"})
            elif value:
                skipped.append({"ocr_key": f"row {row_index + 1}.{entry['ocr_key']}", "ocr_value": value,
                                "normalized_key": entry["normalized_key"], "reason": "no_alias_match"})
        if "description" in clean_row and "product" not in clean_row:
            clean_row["product"] = clean_row["description"]
        if len(clean_row) > 1:
            item_rows.append(clean_row)

    if module == "sales":
        row_po = first_row_identifier(rows, ["order_no", "po_no", "buyer_order_no", "customer_po_number"])
        current_po = form_data.get("customer_po_number", "")
        if row_po and (not current_po or not is_valid_workbench_value(current_po, "customer_po_number")):
            form_data["customer_po_number"] = row_po
            matched.append({"ocr_key": "item_rows.order_no", "ocr_value": row_po,
                            "normalized_key": "order_no", "target_erp_name": "customer_po_number",
                            "target_dom_id": targets["customer_po_number"], "source": "row_header_fallback"})
            if not current_po:
                fallback_count += 1

    # Step 7: ERP master resolver
    unresolved_masters: list[dict[str, Any]] = []
    resolved_masters: list[dict[str, Any]] = []
    _mappings = erp_mappings or []

    if module == "sales":
        _header_resolve = [
            (["customer_name", "buyer_name", "customer", "bill_to_customer"], "customer_id", "customer", {}),
            (["ship_to_customer_name", "consignee_name", "delivery_name"], "ship_to_customer_id", "customer", {}),
            (["payment_terms", "payment_term"], "ar_payment_term_id", "payment_term", {}),
            (["delivery_terms", "delivery_term"], "ar_delivery_terms_id", "delivery_term", {}),
            (["freight_terms", "freight_term"], "ar_frieghtterm_id", "freight_term", {}),
            (["currency", "invoice_currency"], "currency_code_id", "currency", {}),
            (["salesperson", "sales_person"], "salesperson_id", "salesperson", {}),
            (["carrier", "transporter_name"], "ar_frieghtcarriers_hdr_id", "carrier", {}),
            (["project"], "project_id", "project", {}),
        ]
    else:
        _header_resolve = [
            (["supplier_name", "vendor_name", "supplier"], "supplier_id", "supplier", {}),
            (["payment_terms", "payment_term"], "payment_term_id", "payment_term", {}),
            (["delivery_terms", "delivery_term"], "delivery_terms_id", "delivery_term", {}),
            (["freight_terms", "freight_term"], "freight_terms_id", "freight_term", {}),
            (["currency", "currency_code"], "currency", "currency", {}),
            (["project"], "project_id", "project", {}),
            (["carrier", "transporter_name"], "freight_carrier_id", "carrier", {}),
        ]

    def _do_resolve(source_fields, target_field, master_type, context):
        from app.api.v1.routers.companies import resolve_erp_master_scored
        for src in source_fields:
            val = form_data.get(src) or ""
            if not val:
                continue
            if form_data.get(target_field):
                resolved_masters.append({
                    "master_type": master_type, "source_field": src, "source_value": val,
                    "target_field": target_field, "erp_id": form_data[target_field],
                    "erp_code": form_data[target_field], "display_name": val,
                    "score": 100, "match_reason": "already_filled",
                })
                return
            ctx = dict(context)
            for gst_key in ("gst_number", "gstin", "buyer_gst", "supplier_gst"):
                if form_data.get(gst_key):
                    ctx["gst_number"] = form_data[gst_key]; break
            result = resolve_erp_master_scored(master_type, val, ctx, _mappings, f"{module}_order")
            if result["resolved"]:
                form_data[target_field] = result["erp_id"]
                resolved_masters.append({
                    "master_type": master_type, "source_field": src, "source_value": val,
                    "target_field": target_field, "erp_id": result["erp_id"],
                    "erp_code": result.get("erp_code") or result["erp_id"],
                    "display_name": result.get("display_name") or val,
                    "score": result["score"], "match_reason": result["match_reason"],
                })
            else:
                unresolved_masters.append({
                    "master_type": master_type, "source_field": src, "source_value": val,
                    "target_field": target_field,
                    "reason": result.get("unresolved_reason") or "no_match",
                    "score": result.get("score", 0),
                    "candidates": result.get("candidates") or [],
                })
            return
        if not form_data.get(target_field):
            unresolved_masters.append({
                "master_type": master_type, "source_field": source_fields[0],
                "source_value": "", "target_field": target_field,
                "reason": "missing_source_value", "score": 0, "candidates": [],
            })

    def _do_row_resolve(row, row_index):
        from app.api.v1.routers.companies import resolve_erp_master_scored
        if not row.get("product_id"):
            product_val = row.get("product") or row.get("description") or row.get("article") or row.get("part_no") or ""
            if product_val:
                ctx = {}
                if row.get("hsn_code"):
                    ctx["hsn_code"] = row["hsn_code"]
                if row.get("part_no") or row.get("part_number"):
                    ctx["part_number"] = row.get("part_no") or row.get("part_number")
                result = resolve_erp_master_scored("product", product_val, ctx, _mappings, f"{module}_order")
                if result["resolved"]:
                    row["product_id"] = result["erp_id"]
                    resolved_masters.append({
                        "master_type": "product", "source_field": "product",
                        "source_value": product_val,
                        "target_field": f"item_rows.{row_index}.product_id",
                        "erp_id": result["erp_id"],
                        "erp_code": result.get("erp_code") or result["erp_id"],
                        "display_name": result.get("display_name") or product_val,
                        "score": result["score"], "match_reason": result["match_reason"],
                    })
                else:
                    unresolved_masters.append({
                        "master_type": "product", "source_field": "product",
                        "source_value": product_val,
                        "target_field": f"item_rows.{row_index}.product_id",
                        "reason": result.get("unresolved_reason") or "no_match",
                        "score": result.get("score", 0),
                        "candidates": result.get("candidates") or [],
                    })
        if not row.get("tax_group_id"):
            tax_val = row.get("tax_group") or row.get("igst") or row.get("cgst") or row.get("gst") or ""
            if tax_val:
                ctx = {}
                if row.get("hsn_code"):
                    ctx["hsn_code"] = row["hsn_code"]
                result = resolve_erp_master_scored("tax_group", str(tax_val), ctx, _mappings, f"{module}_order")
                if result["resolved"]:
                    row["tax_group_id"] = result["erp_id"]
                    resolved_masters.append({
                        "master_type": "tax_group", "source_field": "tax_group",
                        "source_value": str(tax_val),
                        "target_field": f"item_rows.{row_index}.tax_group_id",
                        "erp_id": result["erp_id"],
                        "erp_code": result.get("erp_code") or result["erp_id"],
                        "display_name": result.get("display_name") or str(tax_val),
                        "score": result["score"], "match_reason": result["match_reason"],
                    })
                else:
                    unresolved_masters.append({
                        "master_type": "tax_group", "source_field": "tax_group",
                        "source_value": str(tax_val),
                        "target_field": f"item_rows.{row_index}.tax_group_id",
                        "reason": result.get("unresolved_reason") or "no_match",
                        "score": result.get("score", 0),
                        "candidates": result.get("candidates") or [],
                    })

    for src_fields, tgt_field, mtype, ctx in _header_resolve:
        _do_resolve(src_fields, tgt_field, mtype, ctx)
    for idx, row in enumerate(item_rows):
        _do_row_resolve(row, idx)

    detected_count = len(entries) + sum(len(_field_entries(row)) for row in rows)
    filled_count = len(matched)
    warnings: list[str] = []
    if document_type in {"", "general", "unknown"}:
        warnings.append("Document type is unknown; module guard was not applied.")
    response = {
        "module": f"{module}_order", "document_type": document_type, "form_data": form_data, "item_rows": item_rows,
        "detected_count": detected_count, "filled_count": filled_count, "skipped_count": len(skipped),
        "row_count": len(item_rows), "dataset_mapped_count": dataset_count, "fallback_mapped_count": fallback_count,
        "direct_mapped_count": direct_count, "correction_applied_count": correction_count,
        "missing_fields": list(ocr_result.get("missing_fields") or []),
        "unresolved_masters": unresolved_masters, "resolved_masters": resolved_masters,
        "resolved_master_count": len(resolved_masters), "unresolved_master_count": len(unresolved_masters),
        "matched_targets": matched, "skipped_keys": skipped,
        "warnings": warnings, "dataset_mapping_used": dataset_mappings,
        "fallback_aliases_used": sorted({item["normalized_key"] for item in matched if item["source"] == "fallback"}),
        "detected_fields": [{"ocr_key": item["ocr_key"], "ocr_value": clean_workbench_value(item["ocr_value"]),
                             "normalized_key": item["normalized_key"]} for item in entries],
        "applied_corrections": corrections or [],
    }
    precheck = build_export_precheck({
        "module": response["module"],
        "document_type": document_type,
        "form_data": form_data,
        "item_rows": item_rows,
        "mapped_fields": ocr_result.get("mapped_fields") or ocr_result.get("canonical_fields") or {},
        "resolved_masters": resolved_masters,
        "unresolved_masters": unresolved_masters,
        "confidence_map": ocr_result.get("confidence_map") or ocr_result.get("confidence") or {},
        "warnings": list(ocr_result.get("warnings") or []) + warnings,
        "source_type": ocr_result.get("source_type") or ocr_result.get("ocr_engine") or "",
    })
    response.update({
        "export_precheck": precheck,
        "quality_score": precheck["quality_score"],
        "quality_grade": precheck["quality_grade"],
        "export_allowed": precheck["export_allowed"],
        "review_required": precheck["review_required"],
        "blockers": precheck["blockers"],
    })

    # ── Field-level Trace ───────────────────────────────────────────────────
    field_level_trace = []
    selected_ds = (dataset or {}).get("dataset_name") or "None"
    matched_by_target = {m["target_erp_name"]: m for m in response.get("matched_targets", [])}

    for target_erp, dom_id in targets.items():
        target_exists = True
        final_target_id = dom_id
        mapping_found = next((m for m in dataset_mappings if isinstance(m, dict) and m.get("target_erp_name") == target_erp), None)
        source_value = ""
        applied_skipped = "skipped"
        skip_reason = ""
        candidates_for_target = []

        m_src = ""
        m_val_src = ""
        if target_erp in matched_by_target:
            m = matched_by_target[target_erp]
            source_value = m.get("ocr_value") or ""
            applied_skipped = "applied"
            skip_reason = ""
            m_src = m.get("source") or ""
            m_val_src = m.get("value_source") or m_src or ""
        else:
            candidates_for_target = []
            if mapping_found:
                candidates_for_target = lookup.get(mapping_found.get("normalized_source_key"), [])
            if not candidates_for_target:
                candidates_for_target = lookup.get(target_erp, [])
            if not candidates_for_target:
                for nk, erp in aliases.items():
                    if erp == target_erp:
                        candidates_for_target = lookup.get(nk, [])
                        if candidates_for_target:
                            break
            if candidates_for_target:
                entry = candidates_for_target[-1]
                source_value = clean_workbench_value(entry["ocr_value"], target_erp) or ""
                matching_skipped = next((s for s in skipped if s["normalized_key"] == entry["normalized_key"]), None)
                if matching_skipped:
                    skip_reason = matching_skipped["reason"]
                else:
                    if target_erp in form_data and form_data[target_erp]:
                        skip_reason = "not_overwritten_existing_value (overwrite is False)"
                    else:
                        skip_reason = "unknown_validation_failure"
            else:
                if mapping_found:
                    skip_reason = "source_key_not_found_in_document"
                else:
                    applied_skipped = "skipped"
                    skip_reason = "no_mapping_or_alias_rule_found"

        val_status = "VALID"
        unres = next((u for u in unresolved_masters if u["target_field"] == target_erp), None)
        if unres:
            val_status = f"UNRESOLVED: {unres.get('reason')}"
        else:
            if precheck and target_erp in precheck.get("blocked_fields", []):
                val_status = "BLOCKED"
            elif precheck and target_erp in precheck.get("warnings", []):
                val_status = "WARNING"

        # Determine source key
        source_key = ""
        if target_erp in matched_by_target:
            source_key = matched_by_target[target_erp].get("ocr_key") or ""
        elif candidates_for_target:
            source_key = candidates_for_target[-1].get("ocr_key") or ""
        elif mapping_found:
            source_key = mapping_found.get("source_key") or ""

        # Determine confidence
        confidence = None
        if target_erp in matched_by_target:
            ocr_k = matched_by_target[target_erp].get("ocr_key")
            if ocr_k:
                confidence = ocr_result.get("confidence_map", {}).get(ocr_k)
        if confidence is None and mapping_found:
            confidence = mapping_found.get("confidence")
        if confidence is None and candidates_for_target:
            entry = candidates_for_target[-1]
            if isinstance(entry.get("ocr_value"), dict):
                confidence = entry["ocr_value"].get("confidence")
            if confidence is None:
                confidence = ocr_result.get("confidence_map", {}).get(entry.get("ocr_key"))
        try:
            if confidence is not None:
                confidence = float(confidence)
        except:
            confidence = None

        # Map backend source type string to user-friendly requested source mapping terms
        # OCR / regex / training / correction memory / frontend fallback
        mapped_source = "None"
        if applied_skipped == "applied":
            if m_val_src == "correction":
                mapped_source = "correction memory"
            elif m_val_src in ("direct", "fallback", "dataset", "row_header_fallback"):
                mapped_source = "OCR / regex"
            elif m_val_src in ("training_dataset", "dataset_fallback"):
                mapped_source = "training_dataset"
            else:
                mapped_source = m_val_src

        reason_applied_or_rejected = ""
        if applied_skipped == "applied":
            reason_applied_or_rejected = f"Applied: matched via {m_val_src}"
        else:
            reason_applied_or_rejected = f"Rejected: {skip_reason}"

        field_level_trace.append({
            "field": target_erp,
            "target_field": target_erp,
            "final_value": form_data.get(target_erp) or "",
            "source_value": source_value,
            "selected_dataset": selected_ds,
            "mapping_found": mapping_found,
            "final_target_id": final_target_id,
            "target_exists": target_exists,
            "validation_status": val_status,
            "applied_skipped": applied_skipped,
            "skip_reason": skip_reason,
            "source": mapped_source,
            "raw_source": m_src,
            "value_source": m_val_src,
            "source_key": source_key,
            "confidence": confidence,
            "reason_applied_or_rejected": reason_applied_or_rejected
        })

    for trace in field_level_trace:
        logger.info(
            "WorkbenchFieldLevelTrace: field={} final_value={!r} source={!r} source_key={!r} confidence={!r} reason_applied_or_rejected={!r}",
            trace["field"], trace["final_value"], trace["source"],
            trace["source_key"], trace["confidence"], trace["reason_applied_or_rejected"]
        )

    response["field_level_trace"] = field_level_trace
    return response


def _wb_mapped_value(source: dict[str, str], key: str) -> str:
    if key in source and source[key]:
        return source[key]
    normalized = _wb_key(key)
    return next((value for source_key, value in source.items() if _wb_key(source_key) == normalized and value), "")


def _normalize_workbench_payload(req: WorkbenchRequest) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    module = "purchase" if str(req.module or "").lower().startswith("purchase") else "sales"
    source_fields = req.form_data or req.fields or {}
    source_rows = req.item_rows or req.rows or []
    fields = {str(k): _wb_value(v) for k, v in source_fields.items()}
    dataset = req.selected_dataset or {}
    dataset_mappings = dataset.get("mappings_json") or req.mappings_json or req.metadata.get("mappings_json", [])
    raw_fields = req.ocr_fields or req.metadata.get("ocr_fields", {})
    normalized_raw_fields = {str(k): _wb_value(v) for k, v in raw_fields.items()} if isinstance(raw_fields, dict) else {}
    for mapping in dataset_mappings:
        if not isinstance(mapping, dict):
            continue
        source_key = str(mapping.get("ocr_key") or mapping.get("source_field") or "").strip()
        target_key = str(mapping.get("target_field") or mapping.get("erp_field") or "").strip()
        source_value = _wb_mapped_value(normalized_raw_fields, source_key) or _wb_mapped_value(fields, source_key)
        if source_key and target_key and source_value:
            fields[target_key] = source_value
    rows: list[dict[str, Any]] = []
    dataset_row_mappings = dataset.get("row_mappings_json") or req.row_mappings_json or req.metadata.get("row_mappings_json", [])
    raw_rows = req.ocr_rows or req.metadata.get("ocr_rows", [])
    row_count = max(len(source_rows), len(raw_rows))
    for index in range(row_count):
        row = source_rows[index] if index < len(source_rows) else {}
        if not isinstance(row, dict):
            row = {}
        clean = {str(k): _wb_value(v) for k, v in row.items()}
        raw_row = raw_rows[index] if index < len(raw_rows) and isinstance(raw_rows[index], dict) else row
        raw_clean = {str(k): _wb_value(v) for k, v in raw_row.items()}
        for mapping in dataset_row_mappings:
            if not isinstance(mapping, dict):
                continue
            source_key = str(mapping.get("ocr_key") or mapping.get("source_field") or "").strip()
            target_key = str(mapping.get("target_field") or mapping.get("erp_field") or "").strip()
            source_value = _wb_mapped_value(raw_clean, source_key) or _wb_mapped_value(clean, source_key)
            if source_key and target_key and source_value:
                clean[target_key] = source_value
        if any(v for k, v in clean.items() if k != "row_index"):
            rows.append(clean)
    return module, fields, rows


def validate_workbench_payload(req: WorkbenchRequest) -> dict[str, Any]:
    module, fields, rows = _normalize_workbench_payload(req)
    document_type = req.document_type or ("purchase_order" if module == "purchase" else "sales_order")
    warnings: list[str] = []
    blocked: list[str] = []
    blocker_reasons: list[dict[str, str]] = []

    def block(field: str, reason_code: str, message: str) -> None:
        blocked.append(field)
        blocker_reasons.append({"field": field, "reason_code": reason_code, "message": message})

    if module == "purchase":
        required = {
            "po_number": "PO Number",
            "po_date": "PO Date",
            "supplier_id": "Supplier",
            "delivery_date": "Delivery Date",
            "po_grand_total": "PO Grand Total",
        }
        total_key = "po_grand_total"
        tax_key = "po_tax_total"
    else:
        required = {
            "sales_order_date": "Sales Order Date",
            "customer_id": "Customer",
            "bill_to_address": "Bill To Address",
            "ship_to_address": "Ship To Address",
            "delivery_date": "Delivery Date",
            "order_total": "Order Total",
        }
        total_key = "order_total"
        tax_key = "order_tax"

    required_missing = [key for key in required if not fields.get(key)]
    for key in required_missing:
        visible_name = fields.get("supplier_name") if key == "supplier_id" else fields.get("customer_name") if key == "customer_id" else ""
        if visible_name:
            block(key, "ERP_master_id_missing", f"Needs ERP master selection for {required[key].lower()}")
        else:
            block(key, "truly_absent_from_ocr", f"Required field missing: {required[key]}")
    if module == "purchase" and fields.get("supplier_name") and not fields.get("supplier_id"):
        warnings.append("Needs ERP master selection for supplier")
    elif module == "sales" and fields.get("customer_name") and not fields.get("customer_id"):
        warnings.append("Needs ERP master selection for customer")
    if not rows:
        block("item_rows", "truly_absent_from_ocr", "Required item rows are missing")

    line_total_sum = 0.0
    tax_total_sum = 0.0
    valid_rows = 0
    for index, row in enumerate(rows):
        qty = _wb_number(row.get("bulk_qty") or row.get("qty"))
        price = _wb_number(row.get("bulk_unit_price") or row.get("unit_price"))
        line_total = _wb_number(row.get("bulk_line_total") or row.get("line_total") or row.get("amount"))
        tax_amount = _wb_number(row.get("bulk_tax_amount") or row.get("tax_amount")) or 0.0
        if qty is None or qty <= 0:
            block(f"rows[{index}].qty", "canonical_but_validation_blocked", f"Row {index + 1}: quantity must be greater than zero")
        if price is None or price < 0:
            block(f"rows[{index}].unit_price", "canonical_but_validation_blocked", f"Row {index + 1}: unit price is invalid")
        if line_total is None or line_total <= 0:
            block(f"rows[{index}].line_total", "canonical_but_validation_blocked", f"Row {index + 1}: line total must be greater than zero")
        else:
            line_total_sum += line_total
            valid_rows += 1
        tax_total_sum += tax_amount
        product_text = row.get("product") or row.get("description")
        if product_text and not row.get("product_id"):
            block(f"rows[{index}].product_id", "ERP_master_id_missing", f"Row {index + 1}: Needs ERP master selection for product")
            warnings.append(f"Row {index + 1}: Needs ERP master selection for product")
        tax_group_text = row.get("bulk_tax_group") or row.get("tax_group")
        if tax_group_text and not row.get("tax_group_id"):
            block(f"rows[{index}].tax_group_id", "ERP_master_id_missing", f"Row {index + 1}: Needs ERP master selection for tax group")
            warnings.append(f"Row {index + 1}: Needs ERP master selection for tax group")
        if qty is not None and price is not None and line_total is not None:
            discount = _wb_number(row.get("bulk_discount_amount") or row.get("discount_amount")) or 0.0
            expected = max(qty * price - discount + tax_amount, 0.0)
            if abs(expected - line_total) > max(1.0, line_total * 0.02):
                warnings.append(f"Row {index + 1}: qty x price calculation differs from line total")

    submitted_total = _wb_number(fields.get(total_key))
    totals_check = {
        "submitted_total": submitted_total,
        "calculated_total": round(line_total_sum, 2),
        "submitted_tax": _wb_number(fields.get(tax_key)),
        "calculated_tax": round(tax_total_sum, 2),
        "mismatch": False,
    }
    if submitted_total is not None and valid_rows and abs(submitted_total - line_total_sum) > 1.0:
        totals_check["mismatch"] = True
        warnings.append("Submitted total and calculated item total mismatch")

    low_confidence = [
        key for key, value in (req.fields or {}).items()
        if isinstance(value, dict) and value.get("confidence") is not None and float(value.get("confidence") or 0) < 0.5
    ]
    warnings.extend(f"Low confidence field: {field}" for field in low_confidence)

    blocked = list(dict.fromkeys(blocked))
    blocker_reasons = list({(item["field"], item["reason_code"]): item for item in blocker_reasons}.values())
    required_missing = list(dict.fromkeys(required_missing))
    export_allowed = not blocked
    return {
        "success": True,
        "module": module,
        "document_type": document_type,
        "validation_status": "READY" if export_allowed and not warnings else ("BLOCKED" if blocked else "NEEDS_REVIEW"),
        "export_allowed": export_allowed,
        "blocked": not export_allowed,
        "required_missing": required_missing,
        "blocked_fields": blocked,
        "blocker_reasons": blocker_reasons,
        "warnings": list(dict.fromkeys(warnings)),
        "message": "Export allowed." if export_allowed else "Export blocked. Please complete required fields.",
        "normalized_fields": fields,
        "line_items": rows,
        "totals_check": totals_check,
    }


# ── Export helpers ────────────────────────────────────────────────────────────

def _workbench_export_payload(req: WorkbenchExportRequest) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    validation = validate_workbench_payload(req)
    precheck = build_export_precheck({
        "module": req.module,
        "document_type": req.document_type or validation["document_type"],
        "form_data": validation["normalized_fields"],
        "item_rows": validation["line_items"],
        "mapped_fields": req.mapped_fields or req.ocr_fields,
        "resolved_masters": req.resolved_masters,
        "unresolved_masters": req.unresolved_masters,
        "confidence_map": req.confidence_map,
        "warnings": req.warnings,
        "source_type": req.source_type,
    })
    validation["export_precheck"] = precheck
    validation.update({
        "quality_score": precheck["quality_score"],
        "quality_grade": precheck["quality_grade"],
        "review_required": precheck["review_required"],
    })
    if not validation["export_allowed"] or not precheck["export_allowed"]:
        raise HTTPException(
            status_code=422,
            detail={
                "export_allowed": False,
                "blocked": True,
                "message": "Export blocked. Please complete required fields.",
                "blocked_fields": list(dict.fromkeys(validation["blocked_fields"] + [item["field"] for item in precheck["blockers"]])),
                "blocker_reasons": validation["blocker_reasons"],
                "blockers": precheck["blockers"],
                "quality_score": precheck["quality_score"],
                "quality_grade": precheck["quality_grade"],
                "missing_required_fields": precheck["missing_required_fields"],
                "unresolved_masters": precheck["unresolved_masters"],
                "row_issues": precheck["row_issues"],
                "required_missing": validation["required_missing"],
                "warnings": list(dict.fromkeys(validation["warnings"] + precheck["warnings"])),
            },
        )
    return validation, validation["normalized_fields"], validation["line_items"]


def _pick(source: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _wb_value(source.get(key))
        if value:
            return value
    return ""


def _erp_line(row: dict[str, Any], module: str) -> dict[str, str]:
    aliases = {
        "line_no": ("line_no",), "product": ("product",), "product_id": ("product_id",),
        "part_no": ("part_no", "supplier_part_no"), "description": ("description", "product_description"),
        "uom_code_id": ("uom_code_id", "bulk_uom_code", "uom"), "qty": ("qty", "bulk_qty"),
        "unit_price": ("unit_price", "bulk_unit_price"),
        "discount_percentage": ("discount_percentage", "bulk_discount_percentage"),
        "discount_amount": ("discount_amount", "bulk_discount_amount"),
        "hsn_code": ("hsn_code", "bulk_hsn_code"),
        "tax_group_id": ("tax_group_id",), "tax_amount": ("tax_amount", "bulk_tax_amount"),
        "line_sub_total": ("line_sub_total", "bulk_line_sub_total"),
        "line_total": ("line_total", "bulk_line_total"),
        "delivery_date": ("delivery_date", "bulk_delivery_date"),
        "promised_date": ("promised_date",), "comments": ("comments",),
    }
    keys = PURCHASE_LINE_KEYS if module == "purchase" else SALES_LINE_KEYS
    return {key: _pick(row, *aliases.get(key, (key,))) for key in keys}


def build_erp_workbench_payload(
    req: WorkbenchExportRequest,
    validation: dict[str, Any],
    fields: dict[str, str],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    from datetime import datetime, timezone
    module = validation["module"]
    if module == "purchase":
        header_aliases = {
            "suppliername": ("suppliername", "supplier_name"),
            "suppliersite_id": ("suppliersite_id", "supplier_site"),
            "payment_term_id": ("payment_term_id",), "freight_terms_id": ("freight_terms_id", "freight_term"),
            "freight_carrier_id": ("freight_carrier_id", "freight_carrier"),
            "delivery_terms_id": ("delivery_terms_id", "delivery_term"),
        }
        header_keys = PURCHASE_HEADER_KEYS
    else:
        header_aliases = {
            "currency_code_id": ("currency_code_id", "invoice_currency"),
            "payment_term_id": ("payment_term_id", "ar_payment_term_id"),
            "freight_terms_id": ("freight_terms_id", "freight_term"),
            "delivery_terms_id": ("delivery_terms_id", "delivery_name"),
        }
        header_keys = SALES_HEADER_KEYS
    header = {key: _pick(fields, *header_aliases.get(key, (key,))) for key in header_keys}
    dataset = {
        "id": req.dataset_id,
        "name": req.metadata.get("dataset_name"),
        "match_score": req.metadata.get("dataset_match_score"),
        "match_reason": req.metadata.get("dataset_match_reason"),
        "header_mapped_count": req.metadata.get("dataset_header_mapped_count", 0),
        "row_mapped_count": req.metadata.get("dataset_row_mapped_count", 0),
        "fallback_mapped_count": req.metadata.get("fallback_mapped_count", 0),
    }
    metadata = {key: value for key, value in (req.metadata or {}).items() if key not in {"raw_text", "full_text", "text", "extracted_text"}}
    metadata.update({"document_type": validation["document_type"], "source_job_id": req.source_job_id, "exported_at": datetime.now(timezone.utc).isoformat()})
    return {
        "module": f"{module}_order",
        "header": header,
        "lines": [_erp_line(row, module) for row in rows],
        "validation": validation,
        "dataset": dataset,
        "metadata": metadata,
    }
