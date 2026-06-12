"""
company_form_mapper.py
Lightweight regex/heuristic extractor for business documents.
No external API — pure Python, runs on OCR raw_text.
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Tuple

FIELD_PATTERNS: Dict[str, List[Tuple[str, int]]] = {
    "invoice_number": [
        # exact multi-word labels first (most specific → least)
        (r"tax\s*invoice\s*(?:no\.?|number|#)[:\s#-]*([A-Z0-9\-/]+)", re.I),
        (r"invoice\s*(?:no\.?|number|#|num)[:\s#-]*([A-Z0-9\-/]+)", re.I),
        (r"inv\b\s*(?:no\.?|number|#)?[:\s#-]*([A-Z0-9\-/]{3,30})", re.I),
        (r"tax\s*invoice[:\s#]+([A-Z0-9\-/]{3,30})", re.I),
        # ── synonyms ──
        (r"bill\s*(?:no\.?|number|#)[:\s#-]*([A-Z0-9\-/]+)", re.I),
        (r"(?:document|doc)\b\s*(?:no\.?|number|#)[:\s#-]*([A-Z0-9\-/]+)", re.I),
        (r"voucher\s*(?:no\.?|number|#)[:\s#-]*([A-Z0-9\-/]+)", re.I),
        (r"reference\s*(?:no\.?|number|#|ref)[:\s#-]*([A-Z0-9\-/]+)", re.I),
        (r"challan\s*(?:no\.?|number|#)[:\s#-]*([A-Z0-9\-/]+)", re.I),
        (r"inv\b[:\s#]*([A-Z0-9\-/]{4,20})", re.I),
    ],
    "bill_number": [
        (r"bill\s*(?:no\.?|number|#)[:\s#-]*([A-Z0-9\-/]+)", re.I),
    ],
    "purchase_order_number": [
        (r"p[._]?o[._]?\s*(?:no\.?|number|#)[:\s#]*([A-Z0-9_\-/]+)", re.I),
        (r"purchase\s*order\s*(?:no\.?|number|#)[:\s#-]*([A-Z0-9_\-/]+)", re.I),
        (r"order\s*(?:no\.?|number|#)[:\s#-]*([A-Z0-9_\-/]+)", re.I),
        (r"purchase\s*order[:\s#]*([A-Z0-9_\-/]+)", re.I),
    ],
    "po_date": [
        (r"p\.?o\.?\s*(?:date|dt)[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})", re.I),
        (r"purchase\s*order\s*(?:date|dt)[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})", re.I),
        (r"order\s*(?:date|dt)[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})", re.I),
        (r"p\.?o\.?\s*(?:date|dt)[:\s]*(\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2})", re.I),
    ],
    "invoice_date": [
        (r"invoice\s*date[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})", re.I),
        (r"invoice\s*date[:\s]*(\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2})", re.I),
        (r"invoice\s*date[:\s]*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})", re.I),
        (r"invoice\s*dt[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})", re.I),
        # ── synonyms ──
        (r"bill\s*date[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})", re.I),
        (r"document\s*date[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})", re.I),
        (r"doc\s*date[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})", re.I),
        (r"\bdated?[:\s]+(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})", re.I),
        (r"\bdated?[:\s]+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})", re.I),
        (r"date[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})", re.I),
        (r"date[:\s]*(\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2})", re.I),
        (r"date[:\s]*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})", re.I),
        (r"(?<![A-Za-z0-9])(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})(?![A-Za-z0-9])", 0),
    ],
    "due_date": [
        (r"due\s*(?:date|by)[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})", re.I),
        (r"payment\s*due[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})", re.I),
    ],
    "gst_number": [
        (r"GST(?:IN|\s*No\.?)?[:\s#-]*([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})", re.I),
        (r"GSTIN/UIN[:\s#-]*([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})", re.I),
        (r"([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])", 0),
    ],
    "pan_number": [
        (r"PAN[:\s]*([A-Z]{5}[0-9]{4}[A-Z]{1})", re.I),
        (r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", 0),
    ],
    "phone": [
        (r"(?:phone|mobile|tel|ph)[:\s]*([+\d][\d\s\-().]{7,17})", re.I),
        (r"(?:contact|call)[:\s]*([+\d][\d\s\-().]{7,17})", re.I),
        (r"\b(\+?[0-9]{10,13})\b", 0),
    ],
    "email": [
        (r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", 0),
    ],
    "vendor_name": [
        (r"(?:vendor|seller|from|billed?\s*by|supplier)[:\s]+([^\r\n]{3,80})", re.I),
        (r"(?:company|business)\s*name[:\s]+([^\r\n]{3,80})", re.I),
        (r"^([A-Za-z][A-Za-z0-9 &.,'-]{2,50}(?:pvt|ltd|llp|inc|corp|traders?|enterprises?|solutions?)\.?)", re.I | re.M),
    ],
    "customer_name": [
        (r"(?:customer|client|buyer)[:\s]+([^\r\n]{3,80})", re.I),
        (r"(?:bill\s*to|billed\s*to)[:\s]+([^\r\n]{3,80})", re.I),
        (r"(?:sold\s*to)[:\s]+([^\r\n]{3,80})", re.I),
        (r"(?:party\s*name)[:\s]+([^\r\n]{3,80})", re.I),
    ],
    "billing_address": [
        (r"billing\s*address[:\s]+(.{10,120}?)(?:\n\n|\r\n\r\n|shipping|$)", re.I | re.S),
        (r"bill\s*to[:\s]+(.{10,120}?)(?:\n\n|\r\n\r\n|ship|$)", re.I | re.S),
    ],
    "shipping_address": [
        (r"shipping\s*address[:\s]+(.{10,120}?)(?:\n\n|\r\n\r\n|billing|$)", re.I | re.S),
        (r"ship\s*to[:\s]+(.{10,120}?)(?:\n\n|\r\n\r\n|bill|$)", re.I | re.S),
    ],
    "total_amount": [
        (r"(?:grand\s*)?total\s*(?:amount)?[:\s₹$€£]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
        (r"(?:amount\s*due|net\s*payable)[:\s₹$€£]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
        # ── synonyms ──
        (r"(?:net\s*amount|invoice\s*value|final\s*amount)[:\s₹$€£]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
        (r"(?:amount\s*payable|total\s*payable|balance\s*due)[:\s₹$€£]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
        (r"(?:total\s*invoice\s*value|total\s*value)[:\s₹$€£]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
    ],
    "grand_total": [
        (r"grand\s*total[:\s₹$€£]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
        (r"(?:net\s*amount|final\s*total)[:\s₹$€£]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
        (r"(?:amount\s*payable|total\s*payable)[:\s₹$€£]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
        # Table summary: "Total\n6316\n91842.14" - last number is value.
        (r"^Total\s*\n\s*[0-9,]+\s*\n\s*([0-9,]+(?:\.[0-9]{1,2})?)$", re.I | re.M),
    ],
    "subtotal": [
        (r"sub\s*total[:\s₹$€£]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
    ],
    "taxable_value": [
        (r"(?:taxable\s*(?:value|amount)|assessable\s*value)[:\s₹$€£]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
    ],
    "tax_amount": [
        (r"(?:tax|gst|vat|cgst|sgst|igst)\s*(?:amount|total)?[:\s₹$€£]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
    ],
    "cgst_amount": [
        (r"cgst\s*(?:amount)?[:\sA-Z$%.-]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
    ],
    "sgst_amount": [
        (r"sgst\s*(?:amount)?[:\sA-Z$%.-]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
    ],
    "discount": [
        (r"discount[:\s₹$€£%]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
    ],
    "payment_status": [
        (r"\b(paid|unpaid|pending|overdue|partial(?:ly\s*paid)?|settled|due)\b", re.I),
    ],
    "item_name": [
        (r"(?:description|item|product|service)[:\s]+([A-Za-z0-9 ,.\-&]{3,80})", re.I),
    ],
    "quantity": [
        (r"(?:qty|quantity)[:\s]*([0-9]+(?:\.[0-9]{1,3})?)", re.I),
        (r"total\s*qty[:\s]*([0-9]+(?:\.[0-9]{1,3})?)", re.I),
    ],
    "unit_price": [
        (r"(?:unit\s*price|rate|price\s*per\s*unit)[:\s₹$€£]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
    ],
    "notes": [
        (r"(?:notes?|remarks?|terms?|conditions?)[:\s]+(.{5,200}?)(?:\n\n|$)", re.I | re.S),
    ],
    "company_name": [
        (r"(?:exporter|company\s*name)[:\s]+([^\r\n]{3,80})", re.I),
    ],
    "company_address": [
        (r"(?:exporter\s*address|company\s*address)[:\s]+(.{10,120}?)(?:\n\n|\r\n\r\n|consignee|buyer|$)", re.I | re.S),
    ],
    "iec_code": [
        (r"IEC(?:[:\s#Code]*)([A-Z0-9]{10})", re.I),
    ],
    "consignee_name": [
        (r"(?:consignee\s*(?:name)?)[:\s]+([^\r\n]{3,80})", re.I),
        (r"(?:ship\s*to)[:\s]+([^\r\n]{3,80})", re.I),
        # ── synonyms ──
        (r"(?:delivery\s*to|deliver\s*to)[:\s]+([^\r\n]{3,80})", re.I),
        (r"(?:receiver|recipient)[:\s]+([^\r\n]{3,80})", re.I),
    ],
    "consignee_address": [
        (r"(?:consignee\s*address|ship\s*to\s*address)[:\s]+(.{10,120}?)(?:\n\n|\r\n\r\n|buyer|$)", re.I | re.S),
    ],
    "buyer_name": [
        (r"(?:buyer\s*(?:name)?)[:\s]+([^\r\n]{3,80})", re.I),
        (r"(?:sold\s*to)[:\s]+([^\r\n]{3,80})", re.I),
        # ── synonyms ──
        (r"(?:bill\s*to|billed\s*to)[:\s]+([^\r\n]{3,80})", re.I),
        (r"(?:party\s*name)[:\s]+([^\r\n]{3,80})", re.I),
        (r"(?:purchaser)[:\s]+([^\r\n]{3,80})", re.I),
    ],
    "buyer_address": [
        (r"(?:buyer\s*address|sold\s*to\s*address)[:\s]+(.{10,120}?)(?:\n\n|\r\n\r\n|delivery|$)", re.I | re.S),
    ],
    "delivery_name": [
        (r"(?:delivery\s*to|deliver\s*to)[:\s]+([^\r\n]{3,80})", re.I),
        (r"goods\s*to\s*be\s*despatched\s*to\s*:[:\s]*([^\r\n]{3,80})", re.I),
        (r"ship\s*to[:\s]+([^\r\n]{3,80})", re.I),
    ],
    "delivery_address": [
        (r"(?:delivery\s*address)[:\s]+(.{10,120}?)(?:\n\n|\r\n\r\n|invoice|$)", re.I | re.S),
        (r"goods\s*to\s*be\s*despatched\s*to\s*:?[^\r\n]*\r?\n[^\r\n]{3,80}\r?\n(.{10,240}?)(?=\r?\n\r?\n|\r?\n(?:invoice|buyer|supplier|total|terms?)\b|$)", re.I | re.S),
    ],
    "order_number": [
        (r"(?:order|po)\s*(?:no\.?|number|#)[:\s#-]*([A-Z0-9\-/]+)", re.I),
    ],
    "buyer_order_no": [
        # Multi-value: capture all buyer order numbers as comma-joined string
        (r"buyer['\s]*s?\s*order\s*(?:no\.?|number|#)[:\s#-]*([A-Z0-9\-/,\s]+?)(?:\n|$)", re.I),
        (r"buyer\s*order[:\s#-]*([A-Z0-9\-/,\s]+?)(?:\n|$)", re.I),
        (r"b/?o\s*(?:no\.?|number|#)[:\s#-]*([A-Z0-9\-/,\s]+?)(?:\n|$)", re.I),
        (r"your\s*order\s*(?:no\.?|ref)[:\s#-]*([A-Z0-9\-/,\s]+?)(?:\n|$)", re.I),
        # Glsprint format: "Buyer's Order No.:62073736,62075113"
        (r"buyer['\u2019]?s?\s*order\s*no\.?[:\s]*([A-Z0-9\-/,\s]+?)(?:\n|$)", re.I),
    ],
    "export_category": [
        (r"export\s*category[:\s]*([A-Za-z\s]+)", re.I),
    ],
    "taxation": [
        (r"taxation[:\s]*([A-Za-z\s]+)", re.I),
    ],
    "payment_terms": [
        (r"(?:payment\s*terms|terms\s*of\s*payment)[:\s]+([^\r\n]{3,60})", re.I),
    ],
    "port_of_loading": [
        (r"(?:port\s*of\s*loading|pol)[:\s]+([^\r\n]{3,60})", re.I),
    ],
    "port_of_discharge": [
        (r"(?:port\s*of\s*discharge|pod)[:\s]+([^\r\n]{3,60})", re.I),
    ],
    "mode_of_transport": [
        (r"(?:mode\s*of\s*transport|transport\s*mode)[:\s]+([^\r\n]{3,60})", re.I),
    ],
    "final_destination": [
        (r"final\s*destination[:\s]+([^\r\n]{3,60})", re.I),
    ],
    "country_of_origin": [
        (r"country\s*of\s*origin[:\s]+([^\r\n]{2,60})", re.I),
    ],
    "gross_weight": [
        (r"gross\s*weight[:\s]*([0-9.,]+(?:\s*[a-zA-Z]+)?)", re.I),
    ],
    "net_weight": [
        (r"net\s*weight[:\s]*([0-9.,]+(?:\s*[a-zA-Z]+)?)", re.I),
    ],
    "total_cartons": [
        (r"(?:total\s*cartons|cartons)[:\s]*([0-9.,]+)", re.I),
    ],
    "currency": [
        (r"currency[:\s]*([A-Za-z]{3})", re.I),
    ],
    "amount_in_words": [
        (r"(?:amount\s*in\s*words|rupees)[:\s]+([^\r\n]{10,120})", re.I),
        (r"(?:amount\s*chargeable\s*\(?in\s*words\)?)[:\s]+([^\r\n]{10,160})", re.I),
        (r"(?:in\s*words)[:\s]+([^\r\n]{10,120})", re.I),
    ],
    "igst_percent": [
        (r"igst\s*(?:@|at)?\s*([0-9.]+)\s*%", re.I),
    ],
    "igst_amount": [
        (r"igst\s*(?:amount)?[:\s₹$€£]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
    ],
    "total_amount_inr": [
        (r"total\s*amount\s*(?:in\s*inr|rs|₹)[:\s]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
    ],
    "exchange_rate": [
        (r"exchange\s*rate[:\s]*([0-9.,]+)", re.I),
    ],
    "account_number": [
        (r"(?:a/c|account)\s*(?:no\.?|number|#)[:\s#-]*([0-9A-Z]{6,20})", re.I),
        (r"(?:bank\s*)?account\s*(?:number|no\.?|#)[:\s#-]*([0-9A-Z]{6,24})", re.I),
    ],
    "ifsc": [
        (r"IFSC\s*(?:code)?[:\s#-]*([A-Z]{4}0[A-Z0-9]{6})", re.I),
    ],
    "swift_code": [
        (r"swift\s*(?:code)?[:\s#-]*([A-Z0-9]{8,11})", re.I),
    ],
    "bank_name": [
        (r"bank\s*(?:name)?[:\s]+([^\r\n]{3,60})", re.I),
    ],
    "branch": [
        (r"branch[:\s]+([^\r\n]{3,60})", re.I),
    ],
    "declaration_text": [
        (r"(?:We\s+declare|Declaration|The\s+exporter\s+of\s+the\s+products\s+covered\s+by\s+this\s+document|REX\s+No|statement\s+on\s+origin|country\s+of\s+origin\s+statement)[:\s]*(.{10,500}?)(?:\n\n|\r\n\r\n|signature|authorised\s+signatory|authorized\s+signatory|for\s+and\s+on\s+behalf|page|bank\s+details|total|$)", re.I | re.S),
    ],
    "statement_of_origin": [
        (r"(?:The\s+exporter\s+of\s+the\s+products\s+covered\s+by\s+this\s+document|REX\s+No|statement\s+on\s+origin|country\s+of\s+origin\s+statement|We\s+declare)[:\s]*(.{10,500}?)(?:\n\n|\r\n\r\n|signature|authorised\s+signatory|authorized\s+signatory|for\s+and\s+on\s+behalf|page|bank\s+details|total|$)", re.I | re.S),
    ],
    "transport_name": [
        (r"(?:transport(?:er)?|carrier|logistics)\s*(?:name)?[:\s]+([^\r\n]{3,80})", re.I),
        (r"(?:transporter\s*name|transport\s*details)[:\s]+([^\r\n]{3,80})", re.I),
    ],
    "vehicle_number": [
        (r"(?:vehicle\s*(?:no\.?|number)|truck\s*no\.?)[:\s#-]*([A-Z0-9\s\-]{5,20})", re.I),
        (r"\b([A-Z]{2}[0-9]{2}[A-Z]{1,2}[0-9]{4})\b", 0),   # Indian vehicle pattern
    ],
    "lr_number": [
        (r"(?:lr\s*(?:no\.?|number)|lorry\s*receipt\s*(?:no\.?|number))[:\s#-]*([A-Z0-9\-/]+)", re.I),
        (r"(?:consignment\s*(?:no\.?|note))[:\s#-]*([A-Z0-9\-/]+)", re.I),
    ],
    "eway_bill_number": [
        (r"e[\-\s]?way\s*bill\s*(?:no\.?|number)?[:\s#-]*([0-9]{12})", re.I),
        (r"ewb\s*(?:no\.?|number)?[:\s#-]*([0-9]{12})", re.I),
    ],
    "hsn_code": [
        (r"(?:hsn|sac|hsn[/\s]sac)\s*(?:code|no\.?)?[:\s#-]*([0-9]{4,8})", re.I),
        (r"\bHSN\s*/?\s*SAC\b\s*[:#-]?\s*([0-9]{4,8})", re.I),
    ],
    "total_quantity": [
        (r"(?:total\s*qty|total\s*quantity|grand\s*qty)[:\s]*([0-9,]+(?:\.[0-9]{1,3})?)", re.I),
        (r"total\s*(?:pairs|pcs|units|nos|cartons)[:\s]*([0-9,]+)", re.I),
    ],
}

ALL_FIELDS = list(FIELD_PATTERNS.keys()) + ["item_description"]

MASTER_SCHEMA_FIELDS: List[str] = [
    "company_name", "company_address", "gst_number", "iec_code",
    "consignee_name", "buyer_name", "consignee_address", "buyer_address",
    "delivery_name", "delivery_address", "invoice_number", "invoice_date",
    "order_number", "export_category", "taxation", "payment_terms",
    "port_of_loading", "port_of_discharge", "mode_of_transport",
    "final_destination", "country_of_origin", "gross_weight", "net_weight",
    "total_cartons", "currency", "amount_in_words", "igst_percent",
    "igst_amount", "total_amount_inr", "exchange_rate", "grand_total",
    "account_number", "swift_code", "bank_name", "branch",
    "declaration_text", "statement_of_origin", "items",
]

MASTER_ITEM_FIELDS: List[str] = [
    "sno", "order_no", "group", "article", "colour", "hsn_sac",
    "type", "size", "uom", "qty", "price", "amount", "total", "act",
]

# ── Doc-type relevant missing-field filters ──────────────────────────────────
_SALES_FORM_FIELDS = [
    "company_name","company_address","gst_number","iec_code",
    "consignee_name","consignee_address","buyer_name","buyer_address",
    "delivery_name","delivery_address","invoice_number","invoice_date",
    "order_number","export_category","taxation","payment_terms",
    "port_of_loading","port_of_discharge","mode_of_transport","final_destination","country_of_origin",
    "gross_weight","net_weight","total_cartons","currency",
    "amount_in_words","igst_percent","igst_amount","total_amount_inr","exchange_rate","grand_total",
    "account_number","swift_code","bank_name","branch","declaration_text","statement_of_origin",
]
_GENERAL_FORM_FIELDS = [
    "vendor_name","customer_name","invoice_number","bill_number","purchase_order_number",
    "invoice_date","due_date","gst_number","pan_number","phone","email",
    "billing_address","shipping_address","item_name","item_description",
    "quantity","unit_price","subtotal","tax_amount","discount","total_amount",
    "payment_status","notes",
]
_PO_FORM_FIELDS = list(set(_GENERAL_FORM_FIELDS) | {
    "purchase_order_number","vendor_name","delivery_name","delivery_address",
    "transport_name","vehicle_number","lr_number","eway_bill_number","total_quantity",
})
_GRN_FORM_FIELDS = list(set(_GENERAL_FORM_FIELDS) | {
    "vendor_name","transport_name","vehicle_number","lr_number","eway_bill_number","total_quantity",
})

_DOC_TYPE_FIELDS: Dict[str, List[str]] = {
    "sales":              _SALES_FORM_FIELDS,
    "invoice":            _SALES_FORM_FIELDS,
    "tax_invoice":        _SALES_FORM_FIELDS,
    "export_invoice":     _SALES_FORM_FIELDS,
    "commercial_invoice": _SALES_FORM_FIELDS,
    "po":                 _PO_FORM_FIELDS,
    "purchase_order":     _PO_FORM_FIELDS,
    "goods_receipt":      _GRN_FORM_FIELDS,
    "grn":                _GRN_FORM_FIELDS,
    "general":            _GENERAL_FORM_FIELDS,
}

CANONICAL_FIELD_ALIASES: Dict[str, List[str]] = {
    "invoice_number": ["invoice_no", "inv_no", "bill_no", "bill_number", "tax_invoice_no", "document_no"],
    "invoice_date": ["date", "invoice_dt", "bill_date", "document_date", "dated"],
    "buyer_name": ["buyer", "bill_to", "customer_name", "importer", "buyer_company", "consignee_buyer"],
    "consignee_name": ["consignee", "ship_to", "delivery_to", "consignee_buyer"],
    "vendor_name": ["vendor", "supplier", "seller", "exporter", "supplier_name", "company_name"],
    "gst_number": ["gstin", "gst_number", "gst_no", "gst", "supplier_gstin", "buyer_gstin", "gst_supplier", "gstin_uin"],
    "po_number": ["po_no", "purchase_order_number", "order_no", "buyer_order_no"],
    "purchase_order_number": ["po_number", "po_no", "order_no", "buyer_order_no"],
    "eway_bill_number": ["eway_bill", "eway_no", "e_way_bill"],
    "lr_number": ["lr_no", "lorry_receipt_no"],
    "vehicle_number": ["vehicle_no", "truck_no"],
    "total_amount": ["grand_total", "invoice_total", "net_amount", "amount_payable", "final_total", "invoice_value"],
    "grand_total": ["total_amount", "invoice_total", "net_amount", "amount_payable", "final_total", "invoice_value"],
    "taxable_value": ["taxable_amount", "assessable_value", "subtotal"],
    "quantity": ["qty", "total_qty", "total_quantity"],
    "rate": ["unit_rate", "price", "unit_price"],
    "unit_price": ["rate", "unit_rate", "price"],
    "cgst_amount": ["cgst"],
    "sgst_amount": ["sgst"],
    "igst_amount": ["igst"],
    "currency": ["curr"],
    "port_of_loading": ["loading_port", "port_loading"],
    "port_of_discharge": ["discharge_port", "port_discharge"],
    "country_of_origin": ["origin_country"],
    "shipping_bill_number": ["sb_no", "shipping_bill_no"],
    "container_number": ["container_no"],
    "gross_weight": ["gross_wt", "gross_weight_kg", "total_gross_weight"],
    "net_weight": ["net_wt", "net_weight_kg", "total_net_weight"],
    "total_cartons": ["cartons", "carton_count", "no_of_cartons", "packages", "total_packages"],
    "igst_percent": ["igst_rate", "igst_percentage"],
    "total_amount_inr": ["amount_inr", "inr_total", "total_inr", "invoice_value_inr"],
    "exchange_rate": ["ex_rate", "conversion_rate", "forex_rate"],
    "account_number": ["bank_account", "account_no", "ac_no", "beneficiary_account"],
    "declaration_text": ["declaration", "declaration_clause"],
    "statement_of_origin": ["origin_statement", "country_origin_statement"],
}

REQUIRED_CANONICAL_FIELDS: List[str] = [
    "invoice_no", "invoice_date", "po_no", "po_date", "vendor_name",
    "buyer_name", "consignee_name", "gstin", "buyer_gstin", "vendor_gstin",
    "address", "item_description", "hsn_code", "quantity", "rate",
    "taxable_value", "cgst", "sgst", "igst", "total_tax", "total_amount",
    "amount_in_words", "bank_name", "account_no", "ifsc", "document_type",
]

REQUIRED_CANONICAL_ALIASES: Dict[str, List[str]] = {
    "invoice_no": ["invoice_number", "bill_number", "bill_no", "tax_invoice_no", "document_no", "voucher_no", "challan_no"],
    "invoice_date": ["date", "invoice_dt", "bill_date", "document_date", "dated"],
    "po_no": ["purchase_order_number", "po_number", "order_number", "order_no", "buyer_order_no"],
    "po_date": ["purchase_order_date", "order_date"],
    "vendor_name": ["company_name", "supplier_name", "supplier", "seller", "exporter"],
    "buyer_name": ["customer_name", "buyer", "bill_to", "sold_to", "party_name", "importer"],
    "consignee_name": ["consignee", "ship_to", "delivery_to", "receiver"],
    "gstin": ["gst_number", "gst_no", "gst", "vendor_gstin", "buyer_gstin", "supplier_gstin"],
    "buyer_gstin": ["gstin", "gst_number"],
    "vendor_gstin": ["gstin", "gst_number", "supplier_gstin"],
    "address": ["billing_address", "company_address", "buyer_address", "consignee_address", "shipping_address"],
    "item_description": ["item_name", "description", "description_of_goods"],
    "hsn_code": ["hsn", "hsn_sac", "sac", "hsn_sac_code"],
    "quantity": ["qty", "total_qty", "total_quantity"],
    "rate": ["unit_price", "unit_rate", "price"],
    "taxable_value": ["subtotal", "taxable_amount", "assessable_value"],
    "cgst": ["cgst_amount"],
    "sgst": ["sgst_amount"],
    "igst": ["igst_amount"],
    "total_tax": ["tax_amount", "gst_amount", "total_gst"],
    "total_amount": ["grand_total", "invoice_total", "invoice_value", "amount_payable", "net_amount", "total_amount_inr"],
    "account_no": ["account_number", "bank_account", "ac_no", "beneficiary_account"],
    "ifsc": ["ifsc_code"],
    "document_type": ["doc_type"],
}

ALIAS_TO_CANONICAL: Dict[str, str] = {}
for _canonical, _aliases in CANONICAL_FIELD_ALIASES.items():
    ALIAS_TO_CANONICAL.setdefault(_canonical, _canonical)
    for _alias in _aliases:
        ALIAS_TO_CANONICAL.setdefault(_alias, _canonical)
for _canonical, _aliases in REQUIRED_CANONICAL_ALIASES.items():
    ALIAS_TO_CANONICAL.setdefault(_canonical, _canonical)
    for _alias in _aliases:
        ALIAS_TO_CANONICAL.setdefault(_alias, _canonical)


def _clean(val: str) -> str:
    val = re.sub(r"\s+", " ", val.strip()).rstrip(".,;:")
    # Strip trailing slash from invoice/order numbers (e.g. "INV/2024/" → "INV/2024")
    val = re.sub(r"/+$", "", val.strip())
    return val


def _field_value(data: Any) -> str:
    if isinstance(data, dict):
        value = data.get("value", "")
    else:
        value = data
    return "" if value is None else str(value).strip()


def _field_confidence(data: Any) -> float:
    if not isinstance(data, dict):
        return 0.5 if _field_value(data) else 0.0
    try:
        return float(data.get("confidence", 0.5) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _as_field_entry(data: Any, *, default_method: str = "regex", source_key: str = "") -> Dict[str, Any]:
    if isinstance(data, dict):
        value = _field_value(data)
        confidence = _field_confidence(data)
        method = data.get("method") or default_method
        evidence = data.get("source_evidence") or (f"alias:{source_key}" if default_method == "alias" and source_key else "")
        source = data.get("source") or method
    else:
        value = _field_value(data)
        confidence = 0.5 if value else 0.0
        method = default_method
        evidence = f"alias:{source_key}" if default_method == "alias" and source_key else ""
        source = method
    entry = {
        "value": value,
        "confidence": confidence,
        "method": method,
        "source": source,
        "source_evidence": evidence,
    }
    # Preserve optional provenance keys (e.g. "reason": "AI enriched")
    if isinstance(data, dict) and data.get("reason"):
        entry["reason"] = data["reason"]
    return entry


def _choose_better_field(current: Any, candidate: Any) -> Dict[str, Any]:
    cur = _as_field_entry(current) if current is not None else None
    cand = _as_field_entry(candidate)
    if not cand["value"]:
        return cur or cand
    if not cur or not cur["value"]:
        return cand
    return cand if cand["confidence"] > cur["confidence"] else cur


def normalize_mapped_fields(mapped: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, data in (mapped or {}).items():
        if key in {"item_rows", "items"}:
            normalized[key] = data
            continue
        entry = _as_field_entry(data)
        if key in {"invoice_number", "invoice_no", "inv_no", "bill_no", "bill_number", "tax_invoice_no", "document_no"} \
                and entry["value"] and not _looks_like_value("invoice_number", entry["value"]):
            entry["value"] = ""
        if not entry["value"]:
            normalized.setdefault(key, entry)
            continue
        normalized[key] = _choose_better_field(normalized.get(key), entry)

    for canonical, aliases in CANONICAL_FIELD_ALIASES.items():
        keys = [canonical, *aliases]
        best_key = ""
        best: Dict[str, Any] | None = None
        for key in keys:
            if key not in normalized:
                continue
            entry = _as_field_entry(normalized[key])
            if not entry["value"]:
                continue
            if best is None or entry["confidence"] > best["confidence"]:
                best_key, best = key, entry
        if not best:
            continue

        canonical_candidate = best
        if best_key and best_key != canonical and not _field_value(normalized.get(canonical)):
            canonical_candidate = {
                "value": best["value"],
                "confidence": min(float(best.get("confidence", 0.0) or 0.0), 0.82),
                "method": "alias",
                "source": best.get("source") or best.get("method") or "regex",
                "source_evidence": f"alias:{best_key}",
            }
        normalized[canonical] = _choose_better_field(normalized.get(canonical), canonical_candidate)
        for key in keys:
            if key == canonical:
                continue
            existing = normalized.get(key)
            if existing and _field_value(existing):
                continue
            normalized[key] = {
                "value": best["value"],
                "confidence": min(float(best.get("confidence", 0.0) or 0.0), 0.82),
                "method": "alias",
                "source": best.get("source") or best.get("method") or "regex",
                "source_evidence": f"alias:{best_key or canonical}",
            }

    for canonical, aliases in REQUIRED_CANONICAL_ALIASES.items():
        keys = [canonical, *aliases]
        best_key = ""
        best: Dict[str, Any] | None = None
        for key in keys:
            if key not in normalized:
                continue
            entry = _as_field_entry(normalized[key])
            if not entry["value"]:
                continue
            if best is None or entry["confidence"] > best["confidence"]:
                best_key, best = key, entry
        if not best:
            continue
        for key in keys:
            existing = normalized.get(key)
            if existing and _field_value(existing):
                continue
            normalized[key] = {
                "value": best["value"],
                "confidence": min(float(best.get("confidence", 0.0) or 0.0), 0.82),
                "method": "alias",
                "source": best.get("source") or best.get("method") or "regex",
                "source_evidence": f"alias:{best_key or canonical}",
            }

    item_rows = normalized.get("item_rows")
    if item_rows and "items" not in normalized:
        normalized["items"] = item_rows
    elif normalized.get("items") and "item_rows" not in normalized:
        normalized["item_rows"] = normalized["items"]

    return normalized


def _field_filled(mapped: Dict[str, Any], field: str) -> bool:
    keys = [field, *CANONICAL_FIELD_ALIASES.get(field, [])]
    canonical = ALIAS_TO_CANONICAL.get(field)
    if canonical and canonical != field:
        keys.extend([canonical, *CANONICAL_FIELD_ALIASES.get(canonical, [])])
    return any(_field_value(mapped.get(key)) for key in dict.fromkeys(keys))


def missing_fields_for_doc(mapped: Dict[str, Any], doc_type: str) -> List[str]:
    relevant = _DOC_TYPE_FIELDS.get((doc_type or "general").lower(), _GENERAL_FORM_FIELDS)
    return [field for field in relevant if not _field_filled(mapped, field)]


NEXT_LINE_LABELS: Dict[str, List[str]] = {
    "invoice_number": [
        r"tax\s*invoice\s*(?:no\.?|number|#)",
        r"invoice\s*(?:no\.?|number|#|num)",
        r"inv\b\s*(?:no\.?|number|#)?",
        r"bill\s*(?:no\.?|number|#)",
        r"(?:document|doc)\b\s*(?:no\.?|number|#)",
        r"voucher\s*(?:no\.?|number|#)",
        r"challan\s*(?:no\.?|number|#)",
        r"tax\s*invoice\s*(?:no\.?|number|#)",
    ],
    "bill_number": [r"bill\s*(?:no\.?|number|#)"],
    "gst_number": [r"gst(?:in|\s*no\.?)?", r"gstin/uin"],
    "total_amount": [
        r"grand\s*total", r"total\s*amount", r"amount\s*due",
        r"balance\s*due", r"net\s*payable", r"total",
        r"net\s*amount", r"amount\s*payable", r"invoice\s*value",
    ],
    "grand_total": [
        r"grand\s*total", r"net\s*amount", r"final\s*total",
        r"amount\s*payable", r"total\s*payable", r"invoice\s*value",
    ],
    "invoice_date": [
        r"invoice\s*date", r"bill\s*date", r"document\s*date",
        r"doc\s*date", r"dated?",
    ],
    "vendor_name": [r"vendor", r"seller", r"supplier", r"from", r"billed?\s*by", r"exporter"],
    "customer_name": [r"customer", r"client", r"buyer", r"bill\s*to", r"sold\s*to", r"party\s*name"],
    "consignee_name": [r"consignee", r"ship\s*to", r"delivery\s*to", r"deliver\s*to", r"receiver"],
    "gross_weight": [r"gross\s*weight", r"gr\.?\s*wt\.?", r"gross\s*wt"],
    "net_weight": [r"net\s*weight", r"nt\.?\s*wt\.?", r"net\s*wt"],
    "amount_in_words": [r"amount\s*chargeable\s*\(?in\s*words\)?", r"amount\s*in\s*words?", r"rupees?\s*in\s*words?"],
    "port_of_loading": [r"port\s*of\s*loading", r"pol\b"],
    "port_of_discharge": [r"port\s*of\s*discharge", r"pod\b"],
    "mode_of_transport": [r"mode\s*of\s*transport(?:ation)?", r"vessel\s*/\s*flight\s*no"],
    "final_destination": [r"final\s*destination", r"country\s*of\s*destination"],
    "payment_terms": [r"payment\s*terms?", r"terms\s*of\s*(?:delivery\s*and\s*)?payment"],
    "phone": [r"phone", r"mobile", r"tel", r"contact"],
    "email": [r"email", r"e-mail"],
}


def _looks_like_value(field: str, value: str) -> bool:
    value = _clean(value)
    if not value:
        return False
    label_only = re.sub(r"[^a-z]+", " ", value.lower()).strip()
    bad_by_field = {
        "invoice_number": {
            "value", "invoice", "invoice date", "date", "no", "number",
            "export invoice", "tax invoice", "commercial invoice", "invoice no",
        },
        "purchase_order_number": {"value", "purchase order", "po", "po no", "order", "order no", "no", "number"},
        "buyer_order_no": {"value", "buyer order", "buyer s order", "buyer order no", "order", "order no", "no", "number"},
        "order_number": {"group", "order", "order no"},
        "item_name": {"of goods", "description of goods"},
        "container_number": {"seal number"},
        "seal_number": {"box serial number"},
    }
    number_fields = {"invoice_number", "purchase_order_number", "buyer_order_no", "order_number"}
    if label_only in bad_by_field.get(field, set()) and not (field in number_fields and re.search(r"\d", value)):
        return False
    if field == "invoice_number":
        currency_token = re.sub(r"[()\[\]{}\s]", "", value).upper()
        if currency_token in {"EUR", "USD", "INR", "GBP", "AED"}:
            return False
        if re.fullmatch(r"(?i)(?:export|tax|commercial)?\s*invoice|export|tax|commercial", value):
            return False
        # Reject pure date strings as invoice numbers (Phase 8)
        if re.fullmatch(
            r"\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}"
            r"|\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2}",
            value,
        ):
            return False
    if field == "invoice_date":
        return bool(re.fullmatch(
            r"(?:\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4}|"
            r"\d{4}[\/\-.]\d{1,2}[\/\-.]\d{1,2}|"
            r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
            value,
        ))
    if field in {"purchase_order_number", "buyer_order_no"}:
        return bool(re.search(r"\d", value))
    if field in {"gross_weight", "net_weight"}:
        # Must contain digits (actual weight value)
        return bool(re.search(r"\d", value))
    if field in {"port_of_loading", "port_of_discharge", "mode_of_transport", "final_destination"}:
        # Must be more than just a label word
        return bool(value and len(value) >= 3 and not value.lower().startswith("port of"))
    if field == "amount_in_words":
        # Must be a reasonable words description
        return bool(value and len(value) >= 8)
    if field in {"total_amount", "grand_total"}:
        return bool(re.search(r"\d", value))
    if field == "gst_number":
        return bool(re.search(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", value, re.I))
    if field == "email":
        return "@" in value
    return len(value) >= 2


def _extract_clause_blocks(raw_text: str) -> Dict[str, Any]:
    text = re.sub(r"\r\n?", "\n", raw_text or "")
    out: Dict[str, Any] = {}

    def extract(field: str, starters: List[str], stops: List[str], confidence: float) -> None:
        start_re = r"(?:" + "|".join(starters) + r")"
        stop_re = r"(?=\n\s*(?:" + "|".join(stops) + r")\b|$)"
        m = re.search(rf"({start_re}[\s\S]{{10,700}}?){stop_re}", text, re.I)
        if not m:
            return
        value = _clean(m.group(1))
        if value:
            out[field] = {
                "value": value[:500],
                "confidence": confidence,
                "method": "regex",
                "source_evidence": value[:80],
            }

    common_stops = [
        r"signature", r"authorized\s+signatory", r"authorised\s+signatory",
        r"for\s+and\s+on\s+behalf", r"bank\s+details", r"total", r"page",
    ]
    extract("declaration_text", [
        r"declaration", r"we\s+declare", r"we\s+hereby\s+declare",
        r"i/we\s+declare", r"certified\s+that", r"the\s+above\s+declaration",
    ], common_stops, 0.82)
    extract("statement_of_origin", [
        r"the\s+exporter\s+of\s+the\s+products\s+covered\s+by\s+this\s+document",
        r"rex\s+no", r"rex\s+number", r"statement\s+on\s+origin",
        r"statement\s+of\s+origin", r"country\s+of\s+origin\s+statement",
    ], [*common_stops, r"declaration"], 0.84)
    return out


def _extract_next_line_fields(raw_text: str) -> Dict[str, Any]:
    lines = [_clean(line) for line in raw_text.splitlines()]
    out: Dict[str, Any] = {}

    # Labels that should be SKIPPED when scanning ahead for a value.
    # These are standalone label-only lines that appear between a field label
    # and its value in two-column / stacked PDF layouts (e.g. Glsprint).
    _SKIP_LABEL_RE = re.compile(
        r"^(?:"
        r"invoice\s*(?:no\.?|number|#|date|dt)|"
        r"bill\s*(?:no\.?|number|#|date)|"
        r"inv\b\s*(?:no\.?|number|date|#)?|"
        r"date|dated?|"
        r"gstin?|gst\s*(?:no\.?|number)|"
        r"pan\s*(?:no\.?|number)?|"
        r"buyer|consignee|vendor|supplier|seller|"
        r"total|grand\s*total|amount|"
        r"description|address|"
        r"tax\s*invoice|commercial\s*invoice|export\s*invoice"
        r")\s*[:#\-]?\s*$",
        re.I,
    )
    # Peer-value pattern: lines that look like an invoice/PO number value
    # (e.g. SPR023/26-27, CI/24-25/009, INV-2026-001) but NOT date strings.
    # When scanning for invoice_date, skip over such lines (they belong to
    # the sibling invoice_number field in a two-column layout).
    # Explicitly exclude pure date patterns (dd-mm-yyyy, dd/mm/yyyy, etc.)
    _DATE_LINE_RE = re.compile(
        r"^\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}$"
        r"|^\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2}$",
        re.I,
    )
    _PEER_VALUE_RE = re.compile(
        r"^[A-Z]{2,}[0-9]*[\/\-][A-Z0-9\-\/._]{2,}$"   # SPR023/26-27, CI/24-25/009
        r"|^[A-Z]{2,}\-[0-9]{4}\-[0-9]+$"               # INV-2026-001
        r"|^[A-Z0-9]{3,}\/[0-9]{2}\-[0-9]{2}$",         # EXP/26-27
        re.I,
    )

    for idx, line in enumerate(lines):
        if not line:
            continue
        for field, labels in NEXT_LINE_LABELS.items():
            if field in out:
                continue
            for label in labels:
                m = re.match(rf"^\s*(?:{label})\s*[:#\-]?\s*(.*)$", line, re.I)
                if not m:
                    continue
                value = m.group(1).strip()
                if not _looks_like_value(field, value):
                    # ── Phase 8: multi-line lookahead (skip other label lines) ──
                    value = ""
                    for lookahead_line in lines[idx + 1: idx + 5]:
                        if not lookahead_line:
                            continue
                        if _SKIP_LABEL_RE.match(lookahead_line):
                            continue  # skip peer label lines (e.g. "Invoice Date")
                        # For date fields: also skip lines that look like sibling
                        # invoice/PO number values in a two-column layout,
                        # but only if the line is NOT itself a date string.
                        if (
                            field in {"invoice_date", "po_date"}
                            and not _DATE_LINE_RE.match(lookahead_line)
                            and (
                                _PEER_VALUE_RE.match(lookahead_line)
                                or _looks_like_value("invoice_number", lookahead_line)
                            )
                        ):
                            continue
                        if field in {"invoice_number", "purchase_order_number", "buyer_order_no"} \
                                and not re.search(r"\d", lookahead_line):
                            break
                        if _looks_like_value(field, lookahead_line):
                            value = lookahead_line
                            break
                        # stop at non-skippable, non-value lines
                        break
                value = _clean(value)
                if _looks_like_value(field, value):
                    out[field] = {"value": value, "confidence": 0.72,
                                  "method": "next_line", "source_evidence": line[:60]}
                break
    return out


def _line_window(lines: List[str], start: int, limit: int = 5) -> str:
    vals: List[str] = []
    stop = re.compile(r"^(invoice|date|gst|buyer|consignee|description|qty|total|bank|declaration)\b", re.I)
    for line in lines[start + 1:start + 1 + limit]:
        if not line:
            continue
        if stop.search(line):
            break
        vals.append(line)
    return _clean(" ".join(vals))


def _extract_invoice_heuristics(raw_text: str) -> Dict[str, Any]:
    lines = [_clean(line) for line in raw_text.splitlines()]
    out: Dict[str, Any] = {}
    text = "\n".join(lines)

    direct_patterns = {
        "vendor_name": [
            r"\b(K2\s+Footprints[^\r\n]*)",
            r"^([A-Za-z0-9 &.'-]{3,80}(?:PVT|PRIVATE|LTD|LLP|GMBH|INC|CORP)[^\r\n]*)",
        ],
        "customer_name": [
            r"\b(ARA\s+SHOES\s+AUSTRIA\s+GMBH)\b",
            r"(?:consignee|buyer\s+if\s+other\s+than\s+consignee)\s*:?\s*([^\r\n]{3,80})",
        ],
        "payment_status": [r"payment\s*terms?\s*:?\s*([^\r\n]{2,60})", r"\b([0-9]{1,3}\s*days?)\b"],
        "item_description": [r"\b(LEATHER\s+INSOLES[^\r\n]{0,140})"],
        "unit_price": [r"(?:price\s*eur|unit\s*price|price|rate)\s*:?\s*(?:EUR|USD|INR|Rs\.?)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"],
        "total_amount": [r"(?:grand\s*total|total\s*amount|amount\s*due|net\s*payable)\s*:?\s*(?:EUR|USD|INR|Rs\.?)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"],
        "quantity": [r"(?:qty|quantity)\s*:?\s*([0-9]+(?:\.[0-9]{1,3})?)", r"(?:grand\s*total|total)\s+([0-9]{2,7})(?:\s|$)"],
    }
    for field, patterns in direct_patterns.items():
        for pattern in patterns:
            m = re.search(pattern, text, re.I | re.M)
            if m:
                out[field] = {"value": _clean(m.group(1)), "confidence": 0.78}
                break

    for idx, line in enumerate(lines):
        low = line.lower()
        if "consignee" in low and "customer_name" not in out:
            val = _clean(re.sub(r".*consignee\s*:?", "", line, flags=re.I)) or _line_window(lines, idx, 4)
            if val:
                out["customer_name"] = {"value": val, "confidence": 0.82}
        if "buyer if other than consignee" in low and "customer_name" not in out:
            val = _line_window(lines, idx, 4)
            if val:
                out["customer_name"] = {"value": val, "confidence": 0.72}
        if "description of goods" in low and "item_description" not in out:
            val = _line_window(lines, idx, 6)
            if val:
                out["item_description"] = {"value": val[:180], "confidence": 0.78}
        if re.search(r"(leather|insole|goods|pcs|pair|eur)", line, re.I):
            nums = re.findall(r"\b[0-9][0-9,]*(?:\.[0-9]{1,2})?\b", line)
            desc = _clean(re.sub(r"\b[0-9][0-9,]*(?:\.[0-9]{1,2})?\b", " ", line))
            if len(desc) > 8:
                out.setdefault("item_description", {"value": desc[:180], "confidence": 0.66})
            if nums:
                out.setdefault("quantity", {"value": nums[0].replace(",", ""), "confidence": 0.62})
                if len(nums) >= 2:
                    out.setdefault("unit_price", {"value": nums[-2].replace(",", ""), "confidence": 0.58})
                    out.setdefault("total_amount", {"value": nums[-1].replace(",", ""), "confidence": 0.60})
        if re.search(r"grand\s*total|total\s*amount|amount\s*due|net\s*payable", line, re.I):
            nums = re.findall(r"\b[0-9][0-9,]*(?:\.[0-9]{1,2})?\b", line)
            if nums:
                out["total_amount"] = {"value": nums[-1].replace(",", ""), "confidence": 0.82}

    return out


def _extract_table_invoice_fields(raw_text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    header_seen = False
    moneyish = re.compile(r"\b[0-9][0-9,]*(?:\.[0-9]{1,2})?\b")
    labelish = re.compile(r"(description|goods|item|qty|quantity|price|rate|amount|total)", re.I)
    stopish = re.compile(r"(bank|declaration|terms|signature|subtotal|tax|igst|cgst|sgst)", re.I)

    for raw_line in raw_text.splitlines():
        line = _clean(raw_line)
        if not line:
            continue
        if labelish.search(line):
            header_seen = True
        nums = moneyish.findall(line)
        has_words = bool(re.search(r"[A-Za-z]{3,}", line))

        if header_seen and has_words and len(nums) >= 2 and not stopish.search(line):
            desc = _clean(moneyish.sub(" ", line))
            desc = re.sub(r"\b(qty|quantity|price|rate|amount|total|eur|usd|inr|rs)\b", " ", desc, flags=re.I)
            desc = _clean(desc)
            if desc and len(desc) > 5:
                out.setdefault("item_description", {"value": desc[:180], "confidence": 0.70})
                out.setdefault("item_name", {"value": desc[:100], "confidence": 0.62})
            out.setdefault("quantity", {"value": nums[0].replace(",", ""), "confidence": 0.66})
            if len(nums) >= 6:
                out.setdefault("unit_price", {"value": nums[1].replace(",", ""), "confidence": 0.66})
                out.setdefault("rate", {"value": nums[1].replace(",", ""), "confidence": 0.66})
                out.setdefault("taxable_value", {"value": nums[2].replace(",", ""), "confidence": 0.62})
                out.setdefault("cgst", {"value": nums[3].replace(",", ""), "confidence": 0.58})
                out.setdefault("sgst", {"value": nums[4].replace(",", ""), "confidence": 0.58})
            elif len(nums) >= 3:
                out.setdefault("unit_price", {"value": nums[1].replace(",", ""), "confidence": 0.62})
                out.setdefault("rate", {"value": nums[1].replace(",", ""), "confidence": 0.62})
                out.setdefault("taxable_value", {"value": nums[-1].replace(",", ""), "confidence": 0.56})
            out["total_amount"] = {"value": nums[-1].replace(",", ""), "confidence": 0.72}
            break

        if re.search(r"grand\s*total|total", line, re.I) and nums:
            out["total_amount"] = {"value": nums[-1].replace(",", ""), "confidence": 0.82}
            if len(nums) > 1:
                out.setdefault("quantity", {"value": nums[0].replace(",", ""), "confidence": 0.60})

    return out


def _extract_sales_table(raw_text: str) -> Dict[str, Any]:
    # Extract dynamic rows: sno, order_no, group, article, colour, hsn_sac, type, size, uom, qty, price, amount, total
    # This is a heuristic targeting typical sales item lines.
    rows = []
    lines = raw_text.splitlines()
    header_seen = False
    
    for idx, raw_line in enumerate(lines):
        line = _clean(raw_line)
        if not line:
            continue
        # Check if line might be header
        if re.search(r"\b(sno|article|qty|quantity|price|amount|hsn)\b", line, re.I):
            header_seen = True
            continue
            
        # Try to parse line as a row if header seen or it strongly looks like a row
        nums = re.findall(r"\b[0-9][0-9,]*(?:\.[0-9]{1,2})?\b", line)
        if header_seen and len(nums) >= 2 and not re.search(r"(total|tax|gst|freight|discount)", line, re.I):
            # Very naive assignment based on number of columns found
            # Typical row: Sno Article Qty Price Amount
            parts = line.split()
            row = {}
            if len(parts) >= 4:
                row["sno"] = parts[0] if parts[0].isdigit() else ""
                row["qty"] = nums[0] if nums else ""
                row["price"] = nums[1] if len(nums) > 1 else ""
                row["amount"] = nums[-1] if len(nums) > 2 else (nums[1] if len(nums) > 1 else "")
                
                # Reconstruct article from words
                words = [p for p in parts if not re.match(r"^[0-9.,]+$", p)]
                row["article"] = " ".join(words[:4])
                
            if row:
                rows.append(row)
                
    if rows:
        return {"item_rows": {"value": rows, "confidence": 0.70}}
    return {}


def _extract_sale_purchase_fields(raw_text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    text = raw_text or ""
    patterns = {
        "supplier_name": [
            r"(?:supplier|vendor|seller|from|billed?\s*by)\s*:?\s*([^\r\n]{3,80})",
            r"\b(K2\s+Footprints[^\r\n]*)",
        ],
        "buyer_name": [
            r"(?:buyer|consignee|customer|client|bill\s*to|sold\s*to)\s*:?\s*([^\r\n]{3,80})",
            r"\b(ARA\s+SHOES\s+AUSTRIA\s+GMBH)\b",
        ],
        "address": [
            r"(?:address|billing\s*address|shipping\s*address)\s*:?\s*(.{10,180}?)(?:\n\n|\r\n\r\n|gst|phone|email|invoice|$)",
        ],
        "rate": [
            r"(?:rate|unit\s*price|price\s*eur|price)\s*:?\s*(?:rs\.?|inr|eur|usd|gbp|₹|\$|€|£)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        ],
        "taxable_value": [
            r"taxable\s*(?:value|amount)\s*:?\s*(?:rs\.?|inr|eur|usd|gbp|₹|\$|€|£)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        ],
        "cgst": [
            r"cgst\s*(?:amount)?\s*[:@%0-9. ]*\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        ],
        "sgst": [
            r"sgst\s*(?:amount)?\s*[:@%0-9. ]*\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        ],
        "igst": [
            r"igst\s*(?:amount)?\s*[:@%0-9. ]*\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        ],
    }
    for field, field_patterns in patterns.items():
        for pattern in field_patterns:
            m = re.search(pattern, text, re.I | re.S)
            if m:
                out[field] = {"value": _clean(m.group(1)), "confidence": 0.70}
                break
    return out


def _next_value(lines: List[str], labels: List[str], max_lookahead: int = 4) -> str:
    label_re = re.compile(r"(?:" + "|".join(labels) + r")", re.I)
    stop_re = re.compile(r"^(s\.?\s*no|item|description|qty|quantity|price|value|total|page)\b", re.I)
    for idx, line in enumerate(lines):
        if not label_re.search(line):
            continue
        value = label_re.sub("", line, count=1).strip(" :-")
        if _looks_like_value("generic", value):
            return _clean(value)
        for nxt in lines[idx + 1:idx + 1 + max_lookahead]:
            if not nxt or stop_re.search(nxt):
                continue
            if _looks_like_value("generic", nxt):
                return _clean(nxt)
    return ""


def _extract_document_extras(raw_text: str) -> Dict[str, Any]:
    lines = [_clean(line) for line in raw_text.splitlines() if _clean(line)]
    text = "\n".join(lines)
    out: Dict[str, Any] = {}

    def add(field: str, value: str, confidence: float = 0.72) -> None:
        value = _clean(value)
        if value:
            out.setdefault(field, {"value": value, "confidence": confidence})

    if re.search(r"\bPURCHASE\s+ORDER\b", text, re.I):
        add("document_type", "purchase_order", 0.92)
        add("document_title", "PURCHASE ORDER", 0.90)
    elif re.search(r"\bGOODS\s+RECEIPT\s+NOTE\b", text, re.I):
        add("document_type", "goods_receipt_note", 0.92)
        add("document_title", "Goods Receipt Note", 0.90)
    elif re.search(r"\bCOMMERCIAL\s+INVOICE\b", text, re.I):
        add("document_type", "commercial_invoice", 0.90)
        add("document_title", "COMMERCIAL INVOICE", 0.88)
    elif re.search(r"\bEXPORT\s+INVOICE\b", text, re.I):
        add("document_type", "export_invoice", 0.90)
        add("document_title", "EXPORT INVOICE", 0.88)

    for field, labels in {
        "gin_number": [r"GIN\s*No"],
        "gin_date": [r"GIN\s*Date"],
        "grn_number": [r"GRN\s*No"],
        "grn_date": [r"GRN\s*Date"],
        "po_number": [r"PO\s*No", r"Buyer's\s+Order\s+No\.?"],
        "po_date": [r"PO\s*Date", r"Dated"],
        "dc_number": [r"DC\s*No"],
        "dc_date": [r"DC\s*Date"],
        "season": [r"Season"],
        "exporter_ref": [r"Exporter's\s+Ref\.?"],
        "lut_number": [r"LUT\s*NO\.?"],
        "awb_number": [r"AWB\s*NO"],
        "container_number": [r"Container\s+No", r"CONTAINER\s+NUMBER"],
        "seal_number": [r"SEAL\s+NUMBER"],
        "box_serial_number": [r"BOX\s+SERIAL\s+NUMBER"],
        "factory_name": [r"FACTORY"],
        "pre_carriage_by": [r"Pre\s*-\s*Carriage\s+by"],
        "vessel_flight_no": [r"Vessel\s*/\s*Flight\s*No\.?"],
        "place_of_receipt": [r"Place\s+of\s+Receipt\s+by\s+pre-carrier"],
        "marks_numbers": [r"Marks\s*&\s*Nos\.?"],
        "carton_text": [r"No\.\s*&\s*Kind\s+of\s+Pkgs"],
        "cbm": [r"CBM"],
        "gross_weight": [r"GR\.?\s*WT\.?", r"GROSS\s+WEIGHT\s+OF\s+SHIPMENT"],
        "net_weight": [r"NT\.?\s*WT\.?", r"NET\s+WEIGHT\s+OF\s+SHIPMENT"],
        "remarks": [r"Remarks"],
        "authorized_by": [r"Authorized\s+by"],
    }.items():
        add(field, _next_value(lines, labels))

    for field, pattern in {
        "iec_code": r"I\.?\s*E\.?\s*Code\s*(?:No\.?|:)?\s*([A-Z0-9]{8,15})",
        "lut_number": r"LUT\s*NO\.?\s*:\s*([A-Z0-9]+)",
        "gst_supplier": r"GSTIN/UIN\s*:?\s*([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z])",
        "telephone": r"TEL\.?NO\s*:?\s*([0-9\s-]{6,20})",
        "contact_number": r"CONTACT\s*:?\s*([0-9\s-]{6,20})",
        "swift_code": r"SWIFT\s*(?:NO|CODE)?\s*:?\s*([A-Z0-9 ]{8,20})",
        "account_number": r"(?:A/c|ACCOUNT)\s*No\.?\s*:?\s*([0-9 -]{6,25})",
        "total_pairs": r"\bTotal\s*\n\s*([0-9,]+)\s*\n\s*[0-9,]+(?:\.[0-9]{1,2})?",
        "total_value": r"\bTotal\s*\n\s*[0-9,]+\s*\n\s*([0-9,]+(?:\.[0-9]{1,2})?)",
        "amount_chargeable": r"Amount\s+Chargeable\s*\(In words\)\s*([A-Z0-9 ,.-]+ONLY)",
    }.items():
        m = re.search(pattern, text, re.I)
        if m:
            add(field, m.group(1), 0.82)

    dims = re.findall(r"DIMENSIONS\s*:?\s*([0-9*Xx]+cms\s+\d+Nos)", text, re.I)
    for idx, dim in enumerate(dims[:12], 1):
        add(f"dimension_{idx}", dim, 0.74)

    return out


def _extract_vertical_item_fields(raw_text: str, limit: int = 12) -> Dict[str, Any]:
    lines = [_clean(line) for line in raw_text.splitlines() if _clean(line)]
    out: Dict[str, Any] = {}

    def add_row(prefix: str, row: Dict[str, str]) -> None:
        for key, value in row.items():
            if value:
                out[f"{prefix}_{key}"] = {"value": value, "confidence": 0.68}

    po_re = re.compile(r"^(\d+)\s+([A-Z]{2,}\d+[A-Z-]*)$", re.I)
    gls_re = re.compile(r"^(\d{8})$")
    sales_re = re.compile(r"^\d+$")
    idx = 0
    po_count = gls_count = sales_count = grn_count = 0
    grn_rows: List[Dict[str, str]] = []

    while idx < len(lines):
        line = lines[idx]
        m_po = po_re.match(line)
        if m_po and po_count < limit and idx + 7 < len(lines):
            desc_parts = []
            j = idx + 1
            while j < min(len(lines), idx + 6) and not re.fullmatch(r"(PAIRS|NOS|BOX|PCS)", lines[j], re.I):
                desc_parts.append(lines[j])
                j += 1
            if j + 4 < len(lines):
                po_count += 1
                add_row(f"po_item_{po_count:02d}", {
                    "sno": m_po.group(1),
                    "code": m_po.group(2),
                    "description": " ".join(desc_parts),
                    "uom": lines[j],
                    "delivery_date": lines[j + 1],
                    "quantity": lines[j + 2],
                    "price": lines[j + 3],
                    "value": lines[j + 4],
                })
                idx = j + 5
                continue

        if gls_re.match(line) and gls_count < limit and idx + 7 < len(lines):
            nums = lines[idx + 5:idx + 8]
            if all(re.search(r"\d", n) for n in nums):
                gls_count += 1
                add_row(f"gls_item_{gls_count:02d}", {
                    "order_no": lines[idx],
                    "article": lines[idx + 1],
                    "style": lines[idx + 2],
                    "site": lines[idx + 3],
                    "hsn_code": lines[idx + 4],
                    "size": lines[idx + 5],
                    "quantity": lines[idx + 6],
                    "rate": lines[idx + 7],
                    "amount": lines[idx + 8] if idx + 8 < len(lines) else "",
                })
                idx += 9
                continue

        if sales_re.match(line) and sales_count < limit and idx + 12 < len(lines):
            chunk = lines[idx:idx + 13]
            if re.fullmatch(r"\d{6,}", chunk[1]) and re.fullmatch(r"\d{6,8}", chunk[5]):
                sales_count += 1
                add_row(f"sales_item_{sales_count:02d}", {
                    "sno": chunk[0], "order_no": chunk[1], "group": chunk[2], "article": chunk[3],
                    "colour": chunk[4], "hsn_sac": chunk[5], "type": chunk[6], "size": chunk[7],
                    "uom": chunk[8], "quantity": chunk[9], "price": chunk[10], "amount": chunk[11],
                    "total": chunk[12],
                })
                idx += 13
                continue

        if line.isdigit() and grn_count < limit and idx + 7 < len(lines):
            if re.match(r"^[A-Z]{2,}\d", lines[idx + 1]) and re.fullmatch(r"(NOS|BOX|PCS|PAIRS)", lines[idx + 3], re.I):
                grn_count += 1
                grn_row = {
                    "sno": lines[idx],
                    "code": lines[idx + 1],
                    "description": lines[idx + 2],
                    "unit": lines[idx + 3],
                    "received_qty": lines[idx + 4],
                    "accepted_qty": lines[idx + 5],
                    "rejected_qty": lines[idx + 6],
                    "reference": lines[idx + 7],
                }
                grn_rows.append(grn_row)
                add_row(f"grn_item_{grn_count:02d}", grn_row)
                idx += 8
                continue

        idx += 1

    if out:
        out["line_item_count"] = {"value": max(po_count, gls_count, sales_count, grn_count), "confidence": 0.78}
    if grn_rows:
        out["item_rows"] = {
            "value": grn_rows,
            "confidence": 0.82,
            "method": "grn_vertical_table",
            "source_evidence": "GRN code/description/unit/quantity rows",
        }
    return out


def _extract_commercial_invoice_rows(raw_text: str, limit: int = 100) -> Dict[str, Any]:
    """Extract vertical commercial-invoice rows after an explicit table header."""
    lines = [_clean(line) for line in raw_text.splitlines() if _clean(line)]
    header_terms = {
        "order.no", "order no", "article no / fit", "article", "style / colour",
        "style / color", "site", "hsn code", "hsn/sac", "size", "quantity",
        "qty", "rate", "price", "amount", "total", "uom", "pairs", "pcs",
    }
    header_indexes = [
        i for i, line in enumerate(lines)
        if line.lower().strip(" .:") in header_terms
    ]
    if not header_indexes or not any(re.search(r"\border\s*\.?\s*no\b", lines[i], re.I) for i in header_indexes):
        return {}

    start = max(i for i in header_indexes if i < min(header_indexes) + 20) + 1
    order_re = re.compile(r"^\d{6,12}$")
    hsn_re = re.compile(r"^\d{6,8}$")
    number_re = re.compile(r"^[0-9][0-9,]*(?:\.[0-9]{1,4})?$")
    footer_re = re.compile(
        r"^(?:total|grand total|amount chargeable|abstract|declaration|statement|"
        r"signature|bank details|dimensions?)\b",
        re.I,
    )
    rows: List[Dict[str, str]] = []
    seen: set[Tuple[str, ...]] = set()
    category = ""
    i = start
    while i < len(lines) and len(rows) < limit:
        line = lines[i]
        if footer_re.match(line):
            if rows:
                break
            i += 1
            continue
        if not order_re.fullmatch(line):
            if re.search(r"[A-Za-z]{4,}", line) and not number_re.fullmatch(line):
                category = line
            i += 1
            continue
        if i + 8 >= len(lines):
            break
        chunk = lines[i:i + 9]
        order_no, article, style, site, hsn, size, qty, rate, amount = chunk
        if (
            order_re.fullmatch(order_no)
            and re.search(r"[A-Za-z0-9]", article)
            and re.search(r"[A-Za-z]", style)
            and hsn_re.fullmatch(hsn)
            and number_re.fullmatch(qty)
            and number_re.fullmatch(rate)
            and number_re.fullmatch(amount)
        ):
            qty_num = float(qty.replace(",", ""))
            rate_num = float(rate.replace(",", ""))
            amount_num = float(amount.replace(",", ""))
            if qty_num > 0 and rate_num >= 0 and amount_num > 0:
                expected = qty_num * rate_num
                if abs(expected - amount_num) <= max(1.0, amount_num * 0.02):
                    signature = tuple(chunk)
                    if signature not in seen:
                        seen.add(signature)
                        rows.append({
                            "sno": str(len(rows) + 1),
                            "order_no": order_no,
                            "group": site,
                            "article": article,
                            "colour": style,
                            "hsn_sac": hsn,
                            "type": category,
                            "size": size,
                            "uom": "PAIRS",
                            "qty": qty.replace(",", ""),
                            "price": rate.replace(",", ""),
                            "amount": amount.replace(",", ""),
                            "total": amount.replace(",", ""),
                            "act": "",
                        })
                    i += 9
                    continue
        i += 1

    if not rows:
        return {}
    return {
        "item_rows": {
            "value": rows,
            "confidence": 0.90,
            "method": "commercial_vertical_table",
            "source": "regex",
            "source_evidence": "commercial invoice vertical item table",
        },
        "line_item_count": {
            "value": len(rows),
            "confidence": 0.90,
            "method": "commercial_vertical_table",
            "source_evidence": "validated qty x rate = amount rows",
        },
    }


def extract_fields(raw_text: str) -> Dict[str, Any]:
    """
    Returns mapped_fields dict:
    { field: {"value": ..., "confidence": float} }
    """
    if not raw_text:
        return {}

    result: Dict[str, Any] = {}

    for field, patterns in FIELD_PATTERNS.items():
        for pat, flags in patterns:
            m = re.search(pat, raw_text, flags)
            if m:
                val = _clean(m.group(1))
                if val and (field not in {"invoice_number", "invoice_date", "purchase_order_number", "buyer_order_no"} or _looks_like_value(field, val)):
                    conf = round(min(0.95, 0.60 + len(val) * 0.01 + (0.15 if patterns.index((pat, flags)) == 0 else 0)), 2)
                    # Capture short evidence snippet (safe: never full OCR text)
                    evidence = _clean(m.group(0))[:80]
                    result[field] = {"value": val, "confidence": conf, "method": "regex", "source_evidence": evidence}
                    break

    summary_match = re.search(
        r"^Total\s*\n\s*[0-9,]+\s*\n\s*([0-9,]+(?:\.[0-9]{1,2})?)$",
        raw_text,
        re.I | re.M,
    )
    if summary_match:
        amount = _clean(summary_match.group(1))
        evidence = _clean(summary_match.group(0))[:80]
        summary_entry = {
            "value": amount,
            "confidence": 0.92,
            "method": "regex",
            "source_evidence": evidence,
        }
        result["total_amount"] = dict(summary_entry)
        result["grand_total"] = dict(summary_entry)

    for field, data in _extract_next_line_fields(raw_text).items():
        result.setdefault(field, data)
    heuristics = _extract_invoice_heuristics(raw_text)
    for field, data in heuristics.items():
        current = result.get(field, {}).get("value", "")
        if field == "total_amount" and data.get("value"):
            result[field] = data
        elif field == "item_description" and (not current or re.search(r"\d", current) or len(current) < 12 or current.lower() in {"of goods", "description of goods"}):
            result[field] = data
        else:
            result.setdefault(field, data)
    table_fields = _extract_table_invoice_fields(raw_text)
    for field, data in table_fields.items():
        current = result.get(field, {}).get("value", "")
        if field == "total_amount" and data.get("value"):
            result[field] = data
        elif field == "item_description" and (not current or re.search(r"\d", current) or len(current) < 12 or current.lower() in {"of goods", "description of goods"}):
            result[field] = data
        else:
            result.setdefault(field, data)
    for field, data in _extract_sale_purchase_fields(raw_text).items():
        result.setdefault(field, data)
    for field, data in _extract_sales_table(raw_text).items():
        result.setdefault(field, data)
    for field, data in _extract_document_extras(raw_text).items():
        result.setdefault(field, data)
    for field, data in _extract_vertical_item_fields(raw_text).items():
        result.setdefault(field, data)
    for field, data in _extract_commercial_invoice_rows(raw_text).items():
        result[field] = data
    for field, data in _extract_clause_blocks(raw_text).items():
        result[field] = data

    buyer_order = _field_value(result.get("buyer_order_no"))
    if buyer_order:
        order_numbers = re.findall(r"[A-Z0-9][A-Z0-9_\-/]*\d[A-Z0-9_\-/]*", buyer_order, re.I)
        if len(order_numbers) > 1:
            result["buyer_order_nos"] = {
                "value": ", ".join(order_numbers),
                "confidence": result["buyer_order_no"].get("confidence", 0.7),
                "method": "regex",
                "source_evidence": buyer_order[:80],
            }
            result["buyer_order_no"]["value"] = order_numbers[0]

    # item_description: reuse item_name value or first long line
    if "item_description" not in result and "item_name" in result:
        result["item_description"] = {"value": result["item_name"]["value"], "confidence": 0.55}
    if "item_description" not in result:
        for line in raw_text.splitlines():
            line = line.strip()
            if 10 < len(line) < 120 and not re.match(r"^[\d\W]+$", line):
                result["item_description"] = {"value": line[:100], "confidence": 0.40}
                break

    return result


def auto_fill(raw_text: str, doc_type: str = "general") -> Dict[str, Any]:
    mapped = normalize_mapped_fields(extract_fields(raw_text))
    doc_type_value = (doc_type or "general").lower()
    if doc_type_value and doc_type_value != "unknown":
        mapped["document_type"] = {
            "value": doc_type_value,
            "confidence": 0.90,
            "method": "classifier",
            "source_evidence": "doc_type",
        }

    # Build missing_fields only after alias merge and only for active schema.
    relevant = _DOC_TYPE_FIELDS.get((doc_type or "general").lower(), _GENERAL_FORM_FIELDS)
    missing = missing_fields_for_doc(mapped, doc_type)

    total_relevant = len(relevant)
    filled = total_relevant - len(missing)
    message = f"Auto-fill complete. {filled}/{total_relevant} fields extracted."
    weak_mapping = bool(raw_text and filled == 0)
    if weak_mapping:
        message = "OCR text detected but mapping failed"
    return {
        "success": True,
        "mapped_fields": mapped,
        "missing_fields": missing,
        "doc_type": doc_type,
        "document_type": doc_type_value,
        "weak_mapping": weak_mapping,
        "raw_text_preview": (raw_text or "")[:500],
        "message": message,
    }


# ── Document Classifier ───────────────────────────────────────────────────────

_CLS_RULES: List[Dict[str, Any]] = [
    {
        "type": "GRN",
        "must": ["goods receipt note", "grn no", "grn date"],
        "boost": {"gin no": 4, "gin date": 3, "qty accepted": 4, "qty rejected": 4,
                  "qty received": 3, "qc-": 2, "goods receipt": 3},
        "negative": ["purchase order", "export invoice", "commercial invoice"],
        "threshold": 6,
    },
    {
        "type": "TAX_INVOICE",
        "must": ["tax invoice"],
        "boost": {"invoice no": 4, "bill no": 3, "original for recipient": 4,
                  "buyer": 3, "consignee": 3, "gstin": 3, "gst no": 3,
                  "hsn": 3, "sac": 2, "taxable value": 3, "grand total": 3},
        "negative": ["goods receipt note", "grn no"],
        "threshold": 4,
    },
    {
        "type": "PURCHASE_ORDER",
        "must": ["purchase order"],
        "boost": {"po no": 4, "goods to be despatched": 5, "delivery date": 3,
                  "supplier": 3, "season": 2, "tracking ref": 3, "qty req": 3},
        "negative": ["goods receipt note", "grn no", "export invoice"],
        "threshold": 4,
    },
    {
        "type": "COMMERCIAL_INVOICE",
        "must": ["commercial invoice"],
        "boost": {"lut no": 5, "i.e code": 4, "ie code": 4, "by sea": 3,
                  "awb no": 3, "port of loading": 2, "port of discharge": 2,
                  "supply meant for export": 4, "without payment of igst": 4},
        "negative": ["purchase order", "goods receipt note"],
        "threshold": 4,
    },
    {
        "type": "EXPORT_INVOICE",
        "must": ["export invoice"],
        "boost": {"consignee": 3, "port of loading": 3, "port of discharge": 3,
                  "country of origin": 3, "drawback": 4, "fob": 2,
                  "description of goods": 4, "grand total": 2, "amount (in words)": 3},
        "negative": ["purchase order", "goods receipt note", "commercial invoice"],
        "threshold": 4,
    },
]

# Legacy sales-score fallback (preserves Phase 1 routing behaviour)
_LEGACY_SALES_SIGNALS: Dict[str, int] = {
    "export invoice": 6, "tax invoice": 4, "invoice no": 3, "consignee": 3,
    "buyer if other than consignee": 4, "delivery at": 3,
    "description of goods": 5, "port of loading": 3, "port of discharge": 3,
    "grand total": 3, "amount (in words)": 3, "hsn": 2, "igst": 2,
    "sales": 3, "original for recipient": 4, "buyer": 2, "gstin": 2,
}


def classify_document(raw_text: str, filename: str = "") -> Dict[str, Any]:
    """
    Deterministic keyword classifier.
    Returns:
        {
            "doc_type": "EXPORT_INVOICE" | "COMMERCIAL_INVOICE" | "PURCHASE_ORDER" | "GRN" | "UNKNOWN",
            "mapper":   "sales" | "auto_fill",   # which mapper to call
            "score":    int,
            "matched_rule": str,
            "debug": {...},
        }
    """
    low = (raw_text or "").lower()
    fname_low = (filename or "").lower()
    debug: Dict[str, Any] = {}

    for rule in _CLS_RULES:
        rtype = rule["type"]
        # Must-have: at least one must keyword present
        must_hits = [kw for kw in rule["must"] if kw in low]
        if not must_hits:
            continue
        # Negative guard
        neg_hits = [kw for kw in rule["negative"] if kw in low]
        if neg_hits:
            debug[rtype] = {"skip": "negative_guard", "neg": neg_hits}
            continue
        # Score boosters
        score = len(must_hits) * 5
        boost_hits: Dict[str, int] = {}
        for kw, weight in rule["boost"].items():
            if kw in low:
                score += weight
                boost_hits[kw] = weight
        debug[rtype] = {"must": must_hits, "boost": boost_hits, "score": score}
        if score >= rule["threshold"]:
            mapper = "auto_fill" if rtype in ("PURCHASE_ORDER", "GRN") else "sales"
            return {
                "doc_type": rtype,
                "mapper": mapper,
                "score": score,
                "matched_rule": rtype,
                "debug": debug,
            }

    # Legacy sales-score fallback (preserves existing behaviour)
    legacy_score = 0
    if any(kw in fname_low for kw in ["sales", "invoice", "export", "commercial", "tax"]):
        legacy_score += 3
    for kw, weight in _LEGACY_SALES_SIGNALS.items():
        if kw in low:
            legacy_score += weight
    debug["legacy_score"] = legacy_score
    if legacy_score >= 8:
        return {
            "doc_type": "EXPORT_INVOICE",
            "mapper": "sales",
            "score": legacy_score,
            "matched_rule": "legacy_fallback",
            "debug": debug,
        }

    return {
        "doc_type": "UNKNOWN",
        "mapper": "auto_fill",
        "score": 0,
        "matched_rule": "none",
        "debug": debug,
    }


def _sales_original_text(raw_text: str) -> str:
    pages = [p.strip() for p in re.split(r"\f|(?:^|\n)\s*-{2,}\s*PAGE\s+\d+\s*-{2,}\s*", raw_text or "", flags=re.I) if p.strip()]
    pages = pages or [raw_text or ""]
    copy_re = re.compile(r"\b(?:duplicate|triplicate|accounts|extra)\b", re.I)
    originals = [p for p in pages if re.search(r"\boriginal\b", p, re.I) and not copy_re.search(p)]
    if originals:
        return "\n\n".join(originals)
    clean_pages = [p for p in pages if not copy_re.search(p)]
    return "\n\n".join(clean_pages or pages[:1])


SALES_FIELDS = [
    "company_name", "company_address", "gst_number", "iec_code",
    "consignee_name", "consignee_address", "buyer_name", "buyer_address",
    "delivery_name", "delivery_address", "invoice_number", "invoice_date",
    "order_number", "export_category", "taxation", "payment_terms",
    "port_of_loading", "port_of_discharge", "mode_of_transport", "final_destination",
    "terms_of_delivery", "country_of_origin", "gross_weight", "net_weight",
    "total_cartons", "currency", "amount_in_words", "igst_percent", "igst_amount",
    "total_amount_inr", "exchange_rate", "grand_total", "account_number",
    "swift_code", "bank_name", "branch", "item_rows",
]


def map_sales_invoice_fields(raw_text: str) -> Dict[str, Any]:
    text = _sales_original_text(raw_text)
    mapped: Dict[str, Any] = {}
    rejected: List[Dict[str, str]] = []
    debug: Dict[str, Any] = {"parser": "sales_line_table_v2"}

    def clean_block(value: str) -> str:
        value = (value or "").replace("\r", "\n")
        value = re.sub(r"[ \t]*\n[ \t]*", "\n", value)
        value = re.sub(r"[ \t]{2,}", " ", value)
        lines = [_clean(line.replace(" :", ":")) for line in value.splitlines() if _clean(line)]
        return "\n".join(lines).strip(" :-")

    lines = [clean_block(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    debug["normalized_line_count"] = len(lines)
    debug["normalized_lines_preview"] = lines[:80]

    def norm(s: str) -> str:
        return re.sub(r"[^A-Z0-9]+", " ", s.upper()).strip()

    def has_label(line: str, label: str) -> bool:
        return norm(label) in norm(line)

    def find_label(label: str, start: int = 0) -> int:
        for i in range(start, len(lines)):
            if has_label(lines[i], label):
                return i
        return -1

    def line_value_after_label(line: str, label: str) -> str:
        pat = r"\b" + r"\s+".join(re.escape(p) for p in label.split()) + r"\b\s*:?\s*"
        val = re.sub(pat, "", line, count=1, flags=re.I).strip(" :-|")
        return "" if val == line else clean_block(val)

    def block_between(start_label: str, end_labels: List[str]) -> List[str]:
        start = find_label(start_label)
        if start < 0:
            return []
        end = len(lines)
        for label in end_labels:
            idx = find_label(label, start + 1)
            if idx >= 0:
                end = min(end, idx)
        first_val = line_value_after_label(lines[start], start_label)
        block = [first_val] if first_val else []
        block.extend(lines[start + 1:end])
        return [line for line in block if line]

    def valid_value(field: str, value: str) -> bool:
        low = _clean(value).lower()
        if not low or len(low) < 2:
            return False
        label_only = re.sub(r"[^a-z]+", " ", low).strip()
        bad_exact = {
            "invoice", "invoice no", "invoice date", "dated", "group", "description",
            "description of goods", "consignee", "buyer", "buyer if other than consignee",
            "delivery at", "port of loading", "grand total", "amount", "total",
            "export invoice", "tax invoice", "commercial invoice", "original",
            "statement of origin", "declaration", "order", "order no", "of goods",
            "seal number", "box serial number",
        }
        if label_only in bad_exact:
            return False
        if not re.search(r"[A-Za-z0-9]", value):
            return False
        if field in {"invoice_number", "order_number"}:
            return bool(re.search(r"\d", value)) and not re.search(r"\b(invoice|dated|group|order no)\b", low)
        if field == "invoice_date":
            return _looks_like_value(field, value)
        if field.endswith("_address"):
            return len(value) > 8 and not low.startswith(("buyer if", "delivery at", "invoice no"))
        return True

    def set_field(field: str, value: str, confidence: float = 0.85) -> None:
        value = clean_block(value)
        if valid_value(field, value):
            mapped[field] = {
                "value": value,
                "confidence": confidence,
                "method": "regex",
                "source_evidence": value[:80],
            }
        elif value:
            rejected.append({"field": field, "value": value, "reason": "label_only_or_suspicious"})

    company_idx = next((i for i, line in enumerate(lines[:20]) if re.search(r"FOOTPRINTS", line, re.I)), -1)
    if company_idx < 0:
        company_idx = next((i for i, line in enumerate(lines[:12]) if not re.search(r"\b(original|duplicate|draft|export invoice|tax invoice|statement|declaration)\b", line, re.I)), -1)
    if company_idx >= 0:
        if re.search(r"FOOTPRINTS", lines[company_idx], re.I):
            set_field("company_name", lines[company_idx], 0.90)
        gst_idx = min([idx for idx in [find_label("GST No", company_idx), find_label("GSTIN", company_idx)] if idx >= 0] or [len(lines)])
        set_field("company_address", "\n".join(lines[company_idx + 1:gst_idx]), 0.86)
    if "company_address" not in mapped:
        gst_idx = min([idx for idx in [find_label("GST No"), find_label("GSTIN")] if idx >= 0] or [-1])
        if gst_idx > 0:
            addr_lines = [line for line in lines[:gst_idx] if not re.search(r"\b(original|duplicate|draft|export invoice|tax invoice)\b", line, re.I)]
            set_field("company_address", "\n".join(addr_lines), 0.82)

    for line in lines:
        m = re.search(r"GST\s*(?:NO|IN)?\s*:?\s*([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z])", line, re.I)
        if m:
            set_field("gst_number", m.group(1), 0.95)
            break
    for line in lines:
        m = re.search(r"I\.?\s*E\.?\s*CODE\s*:?\s*([A-Z0-9]{8,15})", line, re.I)
        if m:
            set_field("iec_code", m.group(1), 0.94)
            break

    for prefix, block in (
        ("consignee", block_between("CONSIGNEE", ["BUYER IF OTHER THAN CONSIGNEE"])),
        ("buyer", block_between("BUYER IF OTHER THAN CONSIGNEE", ["DELIVERY AT"])),
        ("delivery", block_between("DELIVERY AT", ["INVOICE NO"])),
    ):
        clean_lines = [line for line in block if valid_value(f"{prefix}_name", line) and not re.search(r"\b(consignee|buyer if|delivery at)\b", line, re.I)]
        if clean_lines:
            set_field(f"{prefix}_name", clean_lines[0], 0.92)
            set_field(f"{prefix}_address", "\n".join(clean_lines[1:]), 0.90)
        debug[f"{prefix}_block"] = clean_lines

    label_to_field = {
        "INVOICE NO": "invoice_number", "INVOICE DATE": "invoice_date", "DATED": "invoice_date",
        "PORT OF LOADING": "port_of_loading", "PORT OF DISCHARGE": "port_of_discharge",
        "MODE OF TRANSPORT": "mode_of_transport", "FINAL DESTINATION": "final_destination",
        "TERMS OF DELIVERY": "terms_of_delivery", "PAYMENT TERMS": "payment_terms",
        "GROSS WEIGHT OF SHIPMENT": "gross_weight", "NET WEIGHT OF SHIPMENT": "net_weight",
        "TOTAL NO OF CARTONS": "total_cartons", "CURRENCY": "currency",
        "COUNTRY OF ORIGIN OF GOODS": "country_of_origin", "EXPORT CATEGORY": "export_category",
        "TAXATION": "taxation",
    }

    # PDF text layers often emit two visual-column labels first, followed by
    # their two values: Invoice No, Invoice Date, INV-001, 31-05-2026.
    invoice_no_idx = find_label("INVOICE NO")
    if (
        invoice_no_idx >= 0
        and invoice_no_idx + 3 < len(lines)
        and has_label(lines[invoice_no_idx + 1], "INVOICE DATE")
    ):
        paired_number = lines[invoice_no_idx + 2]
        paired_date = lines[invoice_no_idx + 3]
        if valid_value("invoice_number", paired_number) and valid_value("invoice_date", paired_date):
            set_field("invoice_number", paired_number, 0.94)
            set_field("invoice_date", paired_date, 0.94)

    sorted_labels = sorted(label_to_field, key=len, reverse=True)
    kv_hits: List[Dict[str, str]] = []
    for i, line in enumerate(lines):
        hits = [label for label in sorted_labels if has_label(line, label)]
        if not hits:
            continue
        positions = sorted((line.upper().find(label.split()[0]), label) for label in hits)
        for pos_idx, (_, label) in enumerate(positions):
            field = label_to_field[label]
            value = line_value_after_label(line, label)
            if not value or any(has_label(value, next_label) for next_label in sorted_labels):
                next_same_line = positions[pos_idx + 1][1] if pos_idx + 1 < len(positions) else ""
                if next_same_line:
                    pattern = re.escape(label).replace(r"\ ", r"\s+") + r"\s*:?\s*(.*?)" + re.escape(next_same_line).replace(r"\ ", r"\s+")
                    m = re.search(pattern, line, re.I)
                    value = clean_block(m.group(1)) if m else value
            if not value and i + 1 < len(lines) and not any(has_label(lines[i + 1], l) for l in sorted_labels):
                value = lines[i + 1]
            set_field(field, value, 0.88)
            if value:
                kv_hits.append({"label": label, "field": field, "value": value})
    debug["grid_hits"] = kv_hits

    # Fallback for OCR text where label and value are split into adjacent visual columns.
    joined = "\n".join(lines)
    for field, pattern in {
        "port_of_loading": r"PORT OF LOADING\s*:?\s*(CHENNAI|MUMBAI|DELHI|BANGALORE|COCHIN)",
        "port_of_discharge": r"PORT OF DISCHARGE\s*:?\s*([A-Z ]{3,40})",
        "mode_of_transport": r"MODE OF TRANSPORT\s*:?\s*(AIR|SEA|ROAD|RAIL)",
        "final_destination": r"FINAL DESTINATION\s*:?\s*([A-Z ]{3,40})",
        "payment_terms": r"PAYMENT TERMS\s*:?\s*([0-9]+\s*DAYS?)",
        "gross_weight": r"GROSS WEIGHT OF SHIPMENT\s*:?\s*([0-9.,]+)",
        "net_weight": r"NET WEIGHT OF SHIPMENT\s*:?\s*([0-9.,]+)",
        "total_cartons": r"TOTAL NO OF CARTONS\s*:?\s*([0-9.,]+)",
        "currency": r"CURRENCY\s*:?\s*([A-Z]{3})",
        "country_of_origin": r"COUNTRY OF ORIGIN OF GOODS\s*:?\s*([A-Z ]{3,40})",
        "export_category": r"EXPORT CATEGORY\s*:?\s*([A-Z ]{3,50})",
        "taxation": r"TAXATION\s*:?\s*(IGST|CGST|SGST|GST|VAT)",
    }.items():
        if field not in mapped:
            m = re.search(pattern, joined, re.I)
            if m:
                set_field(field, m.group(1), 0.82)

    words_idx = find_label("AMOUNT IN WORDS")
    if words_idx < 0:
        words_idx = find_label("AMOUNT (IN WORDS)")
    if words_idx >= 0:
        amount_words = line_value_after_label(lines[words_idx], "AMOUNT IN WORDS") or line_value_after_label(lines[words_idx], "AMOUNT (IN WORDS)")
        extra = []
        if amount_words:
            for line in lines[words_idx + 1:]:
                if has_label(line, "GRAND TOTAL") or has_label(line, "IGST DETAILS") or has_label(line, "ACCOUNT NO"):
                    break
                extra.append(line)
        else:
            prev = []
            for line in reversed(lines[:words_idx]):
                if re.fullmatch(r"[0-9,]+(?:\.[0-9]{1,2})?", line):
                    continue
                if has_label(line, "GRAND TOTAL"):
                    break
                prev.append(line)
            extra = list(reversed(prev))
        set_field("amount_in_words", "\n".join([amount_words] + extra), 0.88)
    for line in lines:
        if has_label(line, "GRAND TOTAL"):
            nums = re.findall(r"\b[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?\b|\b[0-9]+(?:\.[0-9]{1,2})?\b", line)
            if nums:
                set_field("grand_total", nums[-1], 0.92)
            elif lines.index(line) + 1 < len(lines):
                set_field("grand_total", lines[lines.index(line) + 1], 0.92)

    igst_start = find_label("IGST DETAILS")
    igst_end = find_label("ACCOUNT NO", igst_start + 1) if igst_start >= 0 else -1
    igst_lines = lines[igst_start:(igst_end if igst_end > igst_start else min(len(lines), igst_start + 12))] if igst_start >= 0 else []
    igst_text = "\n".join(igst_lines)
    debug["igst_details_lines"] = igst_lines
    igst_nums = [line for line in igst_lines if re.fullmatch(r"[0-9,]+(?:\.[0-9]{1,2})?|[0-9.]+\s*%", line)]
    if len(igst_nums) >= 5:
        set_field("exchange_rate", igst_nums[1], 0.92)
        set_field("total_amount_inr", igst_nums[2], 0.92)
        set_field("igst_percent", igst_nums[3].replace(" ", ""), 0.92)
        set_field("igst_amount", igst_nums[4], 0.92)
    else:
        for field, pattern in {
            "exchange_rate": r"EXCHANGE\s*RATE\s*:?\s*([0-9.,]+)",
            "total_amount_inr": r"TOTAL\s*AMOUNT\s*IN\s*INR\s*:?\s*([0-9,]+\.[0-9]{1,2}|[0-9,]+)",
            "igst_percent": r"IGST\s*@?\s*([0-9.]+\s*%)",
            "igst_amount": r"IGST\s*(?:AMOUNT)?\s*:?\s*([0-9,]+\.[0-9]{1,2}|[0-9,]+)",
        }.items():
            m = re.search(pattern, igst_text, re.I)
            if m:
                set_field(field, m.group(1).replace(" ", ""), 0.90)

    bank_line = next((line for line in lines if has_label(line, "ACCOUNT NO")), "")
    if bank_line:
        parts = [p.strip() for p in bank_line.split("|")]
        set_field("account_number", re.sub(r"^.*ACCOUNT\s*NO\s*:?\s*", "", parts[0], flags=re.I), 0.92)
        if len(parts) > 1:
            set_field("swift_code", re.sub(r"^SWIFT\s*:?\s*", "", parts[1], flags=re.I), 0.90)
        if len(parts) > 2:
            set_field("bank_name", parts[2], 0.88)
        if len(parts) > 3:
            set_field("branch", parts[3], 0.88)

    items: List[Dict[str, str]] = []
    desc_indexes = [i for i, line in enumerate(lines) if has_label(line, "DESCRIPTION OF GOODS")]
    grand_idx = find_label("GRAND TOTAL")
    table_start = max([i for i in desc_indexes if grand_idx < 0 or i < grand_idx] or desc_indexes or [-1])
    table_end = grand_idx if grand_idx > table_start else len(lines)
    table_lines = lines[table_start + 1:table_end] if table_start >= 0 else []
    header_re = re.compile(r"\b(S\.?\s*NO|ORDER\s*NO|GROUP|ARTICLE|COLOUR|HSN/?SAC|TYPE|SIZE|UOM|QTY|PRICE|AMOUNT|TOTAL|DESCRIPTION\s+OF\s+GOODS)\b", re.I)
    money = r"[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?"
    row_re = re.compile(
        rf"^\s*(\d+)\s+(\d{{6,}})\s+([A-Z0-9-]+)\s+(.+?)\s+([A-Z]+)\s+(64069020|\d{{6,8}})\s+(\d+)\s+([0-9]{{2}}-[0-9]{{2}})\s+([A-Z]+)\s+([0-9,]+)\s+({money})\s+({money})\s+({money})\s*$",
        re.I,
    )
    debug["table_lines"] = table_lines
    vertical_cells = [line for line in table_lines if line and not header_re.search(line)]
    idx = 0
    while idx + 12 < len(vertical_cells):
        chunk = vertical_cells[idx:idx + 13]
        if re.fullmatch(r"\d+", chunk[0]) and re.fullmatch(r"\d{6,}", chunk[1]) and re.fullmatch(r"\d{6,8}", chunk[5]):
            items.append({
                "sno": chunk[0], "order_no": chunk[1], "group": chunk[2], "article": chunk[3],
                "colour": chunk[4], "hsn_sac": chunk[5], "type": chunk[6], "size": chunk[7],
                "uom": chunk[8], "qty": chunk[9], "price": chunk[10], "amount": chunk[11], "total": chunk[12],
                "act": "",
            })
            idx += 13
        else:
            idx += 1
    for raw_line in table_lines:
        line = clean_block(raw_line)
        if not line or header_re.search(line):
            continue
        pipe_cols = [c.strip() for c in re.split(r"\s*\|\s*|\t+", line) if c.strip()]
        m = row_re.match(line)
        if m:
            vals = m.groups()
            items.append({
                "sno": vals[0], "order_no": vals[1], "group": vals[2], "article": vals[3],
                "colour": vals[4], "hsn_sac": vals[5], "type": vals[6], "size": vals[7],
                "uom": vals[8], "qty": vals[9], "price": vals[10], "amount": vals[11], "total": vals[12],
                "act": "",
            })
            continue
        if len(pipe_cols) >= 13 and re.match(r"^\d+$", pipe_cols[0]):
            items.append({
                "sno": pipe_cols[0], "order_no": pipe_cols[1], "group": pipe_cols[2], "article": pipe_cols[3],
                "colour": pipe_cols[4], "hsn_sac": pipe_cols[5], "type": pipe_cols[6], "size": pipe_cols[7],
                "uom": pipe_cols[8], "qty": pipe_cols[9], "price": pipe_cols[10],
                "amount": pipe_cols[11], "total": pipe_cols[12], "act": pipe_cols[13] if len(pipe_cols) > 13 else "",
            })
            continue
        cols = [c.strip() for c in re.split(r"\s{2,}", line) if c.strip()]
        nums = re.findall(rf"\b{money}\b", line)
        if len(cols) >= 10 and re.match(r"^\d+$", cols[0]):
            items.append({
                "sno": cols[0], "order_no": cols[1], "group": cols[2], "article": cols[3],
                "colour": cols[4], "hsn_sac": cols[5], "type": cols[6], "size": cols[7],
                "uom": cols[8], "qty": cols[9], "price": cols[10] if len(cols) > 10 else "",
                "amount": cols[11] if len(cols) > 11 else "", "total": cols[12] if len(cols) > 12 else "",
                "act": cols[13] if len(cols) > 13 else "",
            })
        elif len(nums) >= 3 and re.search(r"[A-Za-z]{3,}", line):
            desc = _clean(re.sub(r"\b[0-9][0-9,]*(?:\.[0-9]{1,2})?\b", " ", line))
            items.append({"sno": str(len(items) + 1), "article": desc, "qty": nums[-3], "price": nums[-2], "amount": nums[-1], "total": nums[-1], "act": ""})
    if items:
        items = [{key: str(row.get(key, "") if isinstance(row, dict) else "") for key in MASTER_ITEM_FIELDS} for row in items]
        mapped["item_rows"] = {"value": items, "confidence": 0.90, "method": "regex", "source": "regex"}
        mapped["items"] = mapped["item_rows"]

    # Final strong validation pass.
    for field in list(mapped.keys()):
        value = mapped[field].get("value")
        if field == "item_rows":
            continue
        if not valid_value(field, str(value)):
            rejected.append({"field": field, "value": str(value), "reason": "final_validation"})
            mapped.pop(field, None)

    mapped = normalize_mapped_fields(mapped)
    mapped["document_type"] = {
        "value": "sales",
        "confidence": 0.95,
        "method": "classifier",
        "source_evidence": "sales_mapper",
    }
    missing = missing_fields_for_doc(mapped, "sales")
    field_confidence = {field: float(data.get("confidence", 0.0) or 0.0) for field, data in mapped.items() if isinstance(data, dict)}
    for field in missing:
        field_confidence[field] = 0.0
    for item in rejected:
        field_confidence[item.get("field", "")] = 0.0
    debug["mapped_keys"] = sorted(mapped.keys())
    debug["item_row_count"] = len(items)

    return {
        "success": True,
        "mapped_fields": mapped,
        "missing_fields": missing,
        "missing_sales_fields": missing,
        "rejected_sales_values": rejected,
        "field_confidence": field_confidence,
        "sales_mapping_debug": debug,
        "sales_original_text_preview": "\n".join(lines[:60])[:3000],
        "message": f"Sales auto-fill complete. {len(mapped)} fields extracted.",
        "document_type": "sales",
    }


def flatten_item_rows_for_legacy_fields(mapped_result: Dict[str, Any], document_type: str, raw_text: str = "") -> Dict[str, Any]:
    """
    Reads item_rows from mapped_result["mapped_fields"], and flattens them into
    sales/gls-style flat names. Never overwrites existing non-empty keys.
    """
    if not mapped_result or not isinstance(mapped_result, dict):
        return mapped_result

    mapped_fields = mapped_result.setdefault("mapped_fields", {})

    # 1. Flatten existing item_rows if present
    item_rows_data = mapped_fields.get("item_rows")
    if item_rows_data and isinstance(item_rows_data, dict):
        rows = item_rows_data.get("value")
        if isinstance(rows, list):
            is_gls = (document_type in ("COMMERCIAL_INVOICE", "commercial_invoice"))
            prefix = "gls_item_" if is_gls else "sales_item_"

            for i, row in enumerate(rows):
                idx_str = f"{i + 1:02d}"

                # Build flat fields for this row
                flat_row = {}
                if is_gls:
                    flat_row["order_no"] = row.get("order_no") or row.get("sno") or ""
                    flat_row["article"] = row.get("article") or ""
                    flat_row["style"] = row.get("style") or row.get("group") or row.get("type") or ""
                    flat_row["site"] = row.get("site") or row.get("colour") or ""
                    flat_row["hsn_code"] = row.get("hsn_code") or row.get("hsn_sac") or ""
                    flat_row["size"] = row.get("size") or ""
                    flat_row["quantity"] = row.get("qty") or row.get("quantity") or ""
                    flat_row["rate"] = row.get("price") or row.get("rate") or ""
                    flat_row["amount"] = row.get("amount") or ""
                else:
                    flat_row["sno"] = row.get("sno") or ""
                    flat_row["order_no"] = row.get("order_no") or ""
                    flat_row["group"] = row.get("group") or ""
                    flat_row["article"] = row.get("article") or ""
                    flat_row["colour"] = row.get("colour") or ""
                    flat_row["hsn_sac"] = row.get("hsn_sac") or ""
                    flat_row["type"] = row.get("type") or ""
                    flat_row["size"] = row.get("size") or ""
                    flat_row["uom"] = row.get("uom") or ""
                    flat_row["quantity"] = row.get("qty") or row.get("quantity") or ""
                    flat_row["qty"] = row.get("qty") or row.get("quantity") or ""
                    flat_row["price"] = row.get("price") or row.get("rate") or ""
                    flat_row["rate"] = row.get("price") or row.get("rate") or ""
                    flat_row["amount"] = row.get("amount") or ""
                    flat_row["total"] = row.get("total") or ""

                # Add to mapped_fields
                for k, val in flat_row.items():
                    full_key = f"{prefix}{idx_str}_{k}"
                    existing = mapped_fields.get(full_key)
                    if not existing or (isinstance(existing, dict) and not existing.get("value")):
                        mapped_fields[full_key] = {
                            "value": str(val),
                            "confidence": 0.90
                        }

            # Add line_item_count if not present
            if "line_item_count" not in mapped_fields:
                mapped_fields["line_item_count"] = {
                    "value": len(rows),
                    "confidence": 0.90
                }

    # 2. Extract general/vertical fields from raw_text and merge without overwriting
    if raw_text:
        gen_fields = extract_fields(raw_text)
        if not mapped_fields.get("item_rows") and isinstance(gen_fields.get("item_rows"), dict):
            mapped_fields["item_rows"] = gen_fields["item_rows"]
            mapped_fields["items"] = gen_fields["item_rows"]
            rows = gen_fields["item_rows"].get("value", [])
            if isinstance(rows, list):
                mapped_fields["line_item_count"] = {
                    "value": len(rows),
                    "confidence": gen_fields["item_rows"].get("confidence", 0.90),
                    "method": gen_fields["item_rows"].get("method", "regex"),
                    "source_evidence": gen_fields["item_rows"].get("source_evidence", ""),
                }
        for key, data in gen_fields.items():
            if key in {"item_rows", "items"}:
                continue
            existing = mapped_fields.get(key)
            if not existing or (isinstance(existing, dict) and not existing.get("value")):
                if isinstance(data, dict):
                    val = data.get("value", "")
                    conf = data.get("confidence", 0.90)
                else:
                    val = data
                    conf = 0.90

                mapped_fields[key] = {
                    "value": str(val),
                    "confidence": conf
                }

        # Flatten rows adopted from the generic commercial parser.
        item_rows_data = mapped_fields.get("item_rows")
        rows = item_rows_data.get("value", []) if isinstance(item_rows_data, dict) else []
        if isinstance(rows, list):
            prefix = "gls_item_" if document_type in ("COMMERCIAL_INVOICE", "commercial_invoice") else "sales_item_"
            for index, row in enumerate(rows, start=1):
                if not isinstance(row, dict):
                    continue
                for key, value in row.items():
                    flat_key = f"{prefix}{index:02d}_{key}"
                    mapped_fields.setdefault(flat_key, {
                        "value": str(value or ""),
                        "confidence": item_rows_data.get("confidence", 0.90),
                        "method": item_rows_data.get("method", "regex"),
                    })

    mapped_fields = normalize_mapped_fields(mapped_fields)

    # Recalculate missing fields and field confidence after canonical alias merge.
    missing = missing_fields_for_doc(mapped_fields, document_type.lower() if document_type else "sales")
    field_confidence = {field: float(data.get("confidence", 0.0) or 0.0) for field, data in mapped_fields.items() if isinstance(data, dict)}
    for field in missing:
        field_confidence[field] = 0.0

    mapped_result["mapped_fields"] = mapped_fields
    mapped_result["missing_fields"] = missing
    mapped_result["missing_sales_fields"] = missing
    mapped_result["field_confidence"] = field_confidence
    if "message" in mapped_result:
        mapped_result["message"] = f"Sales auto-fill complete. {len(mapped_fields)} fields extracted."

    return mapped_result
