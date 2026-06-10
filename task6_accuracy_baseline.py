"""
Task 6 OCR Accuracy Baseline — runs against real test documents.
"""
import os, sys, json, time, requests

sys.path.insert(0, r"D:\OCR_MASTER\ocr-platform-complete")
os.chdir(r"D:\OCR_MASTER\ocr-platform-complete")
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_enable_pir_api"] = "0"

BASE = "http://127.0.0.1:8000"
DOCS = [
    ("salesinvoice.pdf",  "datasets/salesinvoice.pdf"),
    ("Glsprint.pdf",      "datasets/Glsprint.pdf"),
    ("POPRINT.pdf",       "datasets/POPRINT.pdf"),
    ("GRN.pdf",           "datasets/GRN.pdf"),
]

def auth():
    r = requests.post(f"{BASE}/api/v1/auth/login",
                      json={"email":"admin@example.com","password":"admin123"}, timeout=10)
    if r.status_code != 200:
        r2 = requests.post(f"{BASE}/api/v1/auth/register",
                           json={"email":"admin@example.com","password":"admin123","full_name":"Admin"}, timeout=10)
        r = requests.post(f"{BASE}/api/v1/auth/login",
                          json={"email":"admin@example.com","password":"admin123"}, timeout=10)
    return r.json().get("access_token","")

def upload_and_wait(token, path):
    hdrs = {"Authorization": f"Bearer {token}"}
    with open(path, "rb") as f:
        r = requests.post(f"{BASE}/api/v1/upload/", headers=hdrs, files={"file": f}, timeout=30)
    r.raise_for_status()
    job_id = r.json()["job_id"]
    for _ in range(40):
        time.sleep(1.5)
        d = requests.get(f"{BASE}/api/v1/upload/jobs/{job_id}", headers=hdrs, timeout=10).json()
        if d.get("status") in ("completed","done","success","failed","error"):
            return d
    return d

def analyze(data, name):
    mf = data.get("mapped_fields") or {}
    raw = data.get("raw_text","") or data.get("extracted_text","") or ""
    doc_type = data.get("document_type","?")
    missing = data.get("missing_fields",[])
    low_conf = [k for k,v in mf.items()
                if isinstance(v,dict) and v.get("confidence",1) < 0.7
                and v.get("value")]
    item_rows = mf.get("item_rows",{})
    row_count = len(item_rows.get("value",[])) if isinstance(item_rows,dict) else 0

    # Check which missing fields are actually in raw text
    in_raw, not_in_raw = [], []
    for f in missing:
        # Simple: check if any alias value appears in raw
        hints = [f.replace("_"," "), f.replace("_","")]
        found = any(h.lower() in raw.lower() for h in hints if len(h)>3)
        (in_raw if found else not_in_raw).append(f)

    export_allowed = data.get("export_allowed", data.get("validation",{}).get("export_allowed", "?"))

    return {
        "name": name,
        "doc_type": doc_type,
        "raw_text_len": len(raw),
        "mapped_count": len([k for k,v in mf.items() if isinstance(v,dict) and v.get("value")]),
        "field_count": len(mf),
        "item_rows": row_count,
        "missing_count": len(missing),
        "missing": missing[:30],
        "in_raw_but_missing": in_raw[:20],
        "not_in_raw": not_in_raw[:20],
        "low_conf_count": len(low_conf),
        "low_conf_fields": low_conf[:20],
        "export_allowed": export_allowed,
    }

def main():
    token = auth()
    if not token:
        print("AUTH FAILED"); return

    results = []
    for name, path in DOCS:
        if not os.path.exists(path):
            print(f"SKIP {name} — not found at {path}"); continue
        print(f"\n>> Processing {name}...")
        try:
            data = upload_and_wait(token, path)
            result = analyze(data, name)
            results.append(result)
            print(f"   doc_type={result['doc_type']}  raw={result['raw_text_len']}  mapped={result['mapped_count']}  missing={result['missing_count']}  rows={result['item_rows']}")
            if result["in_raw_but_missing"]:
                print(f"   MAPPER_GAPS (in raw, not mapped): {result['in_raw_but_missing']}")
            if result["not_in_raw"]:
                print(f"   OCR_GAPS (not in raw text): {result['not_in_raw']}")
        except Exception as e:
            print(f"   ERROR: {e}")
            results.append({"name": name, "error": str(e)})

    os.makedirs("tests/artifacts", exist_ok=True)
    with open("tests/artifacts/task6_ocr_accuracy_report.json","w") as f:
        json.dump(results, f, indent=2)
    print("\n\n=== ACCURACY SUMMARY ===")
    for r in results:
        if "error" in r:
            print(f"  {r['name']}: ERROR {r['error']}")
        else:
            print(f"  {r['name']}: doc={r['doc_type']} raw={r['raw_text_len']} mapped={r['mapped_count']} missing={r['missing_count']} rows={r['item_rows']}")
    print(f"\nReport saved: tests/artifacts/task6_ocr_accuracy_report.json")

if __name__ == "__main__":
    main()
