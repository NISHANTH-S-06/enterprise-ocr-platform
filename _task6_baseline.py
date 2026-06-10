"""Task 6 — Direct mapper audit via in-process extraction from PDF files."""
import sys, os, json
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_enable_pir_api"] = "0"
sys.path.insert(0, r"D:\OCR_MASTER\ocr-platform-complete")

# Try to extract text from PDFs using PyMuPDF
import fitz  # PyMuPDF

from app.services.company_form_mapper import (
    extract_fields, normalize_mapped_fields, missing_fields_for_doc,
    classify_document, map_sales_invoice_fields,
    _SALES_FORM_FIELDS, _PO_FORM_FIELDS, _GRN_FORM_FIELDS,
)

docs = {
    "salesinvoice.pdf": r"D:\OCR_MASTER\datasets\salesinvoice.pdf",
    "Glsprint.pdf":     r"D:\OCR_MASTER\datasets\Glsprint.pdf",
    "POPRINT.pdf":      r"D:\OCR_MASTER\datasets\POPRINT.pdf",
    "GRN.pdf":          r"D:\OCR_MASTER\datasets\GRN.pdf",
}

report = {}
for name, path in docs.items():
    print(f"\n{'='*60}\n{name}")
    try:
        doc = fitz.open(path)
        raw_text = "\n".join(page.get_text() for page in doc)
        doc.close()
    except Exception as e:
        print(f"  PDF text extract failed: {e}")
        raw_text = ""

    # Try OCR text from PaddleOCR if PyMuPDF gives empty result
    print(f"  PyMuPDF text len: {len(raw_text)}")

    if len(raw_text) < 50:
        print("  WARNING: Image-only PDF, PyMuPDF got no text. Skipping direct analysis.")
        report[name] = {"note": "image-only PDF, no text layer for direct analysis"}
        continue

    cls = classify_document(raw_text, name)
    doc_type = cls["doc_type"].lower()
    print(f"  classifier: {cls['doc_type']} (score={cls['score']})")

    # Use appropriate mapper
    if cls["mapper"] == "sales":
        mapped = map_sales_invoice_fields(raw_text)
    else:
        mapped = extract_fields(raw_text)

    normalized = normalize_mapped_fields(mapped)
    missing = missing_fields_for_doc(normalized, doc_type)

    def get_val(v):
        return (v.get("value","") if isinstance(v,dict) else str(v or "")).strip()

    filled = {k: get_val(v) for k,v in normalized.items()
              if k not in ("item_rows","items") and get_val(v)}

    item_rows = normalized.get("item_rows") or normalized.get("items") or []

    in_raw_not_mapped = []
    not_in_raw = []
    for f in missing[:40]:
        kws = [w for w in f.replace("_"," ").split() if len(w)>3]
        if any(kw.lower() in raw_text.lower() for kw in kws):
            in_raw_not_mapped.append(f)
        else:
            not_in_raw.append(f)

    print(f"  doc_type:       {doc_type}")
    print(f"  raw_text_len:   {len(raw_text)}")
    print(f"  filled_fields:  {len(filled)}")
    print(f"  item_rows:      {len(item_rows) if isinstance(item_rows,list) else 'dict'}")
    print(f"  missing ({len(missing)}): {missing[:20]}")
    print(f"  in_raw_not_mapped ({len(in_raw_not_mapped)}): {in_raw_not_mapped}")
    print(f"  not_in_raw ({len(not_in_raw)}): {not_in_raw}")
    print(f"  filled_sample:")
    for k,v in list(filled.items())[:20]:
        print(f"    {k}: {v[:60]}")

    report[name] = {
        "doc_type": doc_type,
        "raw_text_len": len(raw_text),
        "filled": len(filled),
        "item_rows": len(item_rows) if isinstance(item_rows,list) else 0,
        "missing": missing,
        "in_raw_not_mapped": in_raw_not_mapped,
        "not_in_raw": not_in_raw,
        "filled_sample": {k: v[:80] for k,v in list(filled.items())[:30]},
    }

os.makedirs(r"D:\OCR_MASTER\ocr-platform-complete\tests\artifacts", exist_ok=True)
out = r"D:\OCR_MASTER\ocr-platform-complete\tests\artifacts\task6_ocr_accuracy_report.json"
with open(out, "w") as f:
    json.dump(report, f, indent=2)
print(f"\nReport: {out}")
