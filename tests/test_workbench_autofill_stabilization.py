import pytest
from app.api.v1.routers.forms_workbench_helpers import WorkbenchAutoFillRequest, build_workbench_auto_fill

def test_workbench_row_aliases_description_and_promised_dates():
    # Test sales order row aliases
    sales_req = WorkbenchAutoFillRequest(
        module="sales_order",
        ocr_result={
            "document_type": "sales_invoice",
            "item_rows": [{
                "description": "Finished Shoe Black",
                "product_id": "P-100",
                "qty": "10",
                "rate": "25.50",
                "amount": "252.50",
            }],
        },
    )
    sales_result = build_workbench_auto_fill(sales_req, sales_req.ocr_result)
    assert sales_result["item_rows"][0]["product_description"] == "Finished Shoe Black"
    assert sales_result["item_rows"][0]["product"] == "Finished Shoe Black"

    # Test purchase order row aliases (promised alternate dates)
    purchase_req = WorkbenchAutoFillRequest(
        module="purchase_order",
        ocr_result={
            "document_type": "purchase_order",
            "item_rows": [{
                "description": "Leather Upper Brown",
                "product_id": "RM-200",
                "qty": "4",
                "unit_price": "100",
                "line_total": "398",
                "promised_alt_date": "22/06/2026",
            }, {
                "product_description": "Leather Upper Black",
                "product_id": "RM-201",
                "qty": "5",
                "unit_price": "110",
                "line_total": "545",
                "promised_alternate_date": "23/06/2026",
            }],
        },
    )
    purchase_result = build_workbench_auto_fill(purchase_req, purchase_req.ocr_result)
    assert purchase_result["item_rows"][0]["product_description"] == "Leather Upper Brown"
    assert purchase_result["item_rows"][0]["promised_alternate_date"] == "22/06/2026"
    assert purchase_result["item_rows"][1]["product_description"] == "Leather Upper Black"
    assert purchase_result["item_rows"][1]["promised_alternate_date"] == "23/06/2026"

def test_workbench_matched_targets_metadata_enrichment():
    req = WorkbenchAutoFillRequest(
        module="purchase_order",
        ocr_result={
            "document_type": "purchase_order",
            "source_type": "paddle_ocr",
            "ocr_engine": "paddle_ocr",
            "confidence_map": {
                "po_number": 0.88,
                "po_date": 0.94,
            },
            "fields": {
                "po_number": "PO-999",
                "po_date": "22-06-2026",
            },
        },
    )
    
    result = build_workbench_auto_fill(req, req.ocr_result)
    
    matched = result["matched_targets"]
    assert len(matched) >= 2
    
    po_num_match = next(m for m in matched if m["target_erp_name"] == "po_number")
    assert po_num_match["source_type"] == "paddle_ocr"
    assert po_num_match["source_engine"] == "paddle_ocr"
    assert po_num_match["confidence"] == 0.88
    # Validation status should be VALID because po_number is successfully filled with a valid value
    assert po_num_match["validation_status"] == "VALID"
