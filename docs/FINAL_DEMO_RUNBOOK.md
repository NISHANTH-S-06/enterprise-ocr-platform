# Final Demo Runbook

Use this runbook only after the critical blockers in the Task 7 audit are resolved. Demonstrate the gated Workbench flow, not the legacy bypass-capable export routes.

## Prerequisites

- Clean tagged checkout with no unexpected modified/untracked source.
- Python 3.11 environment at `.venv311` with locked dependencies installed.
- Production-like `.env` with strong secrets, explicit CORS origins, and a backed-up database.
- Database migrated to the sole `0008` head.
- Approved PDF, image, XLSX, and CSV demo documents containing no sensitive customer data.

## Verification Commands

Run from the project root:

```powershell
node tests/check_js.js
.\.venv311\Scripts\python.exe -m compileall app -q
.\.venv311\Scripts\python.exe -m compileall alembic -q
.\.venv311\Scripts\alembic.exe heads
.\.venv311\Scripts\alembic.exe current
.\.venv311\Scripts\python.exe -m pytest tests/test_platform.py tests/test_gemini.py tests/test_dataset_normalization.py tests/test_task4_erp_master.py tests/test_task5_excel_csv.py tests/test_task6_export_precheck.py tests/test_task7_1_critical_blockers.py -v
.\.venv311\Scripts\python.exe e2e_runner.py
```

Expected baseline: JavaScript PASS, duplicate IDs 0, compile PASS, one `0008 (head)`, 395 tests PASS, and E2E 4/4 PASS. Stop the demo if any result differs.

## Start Commands

Backend development/demo command:

```powershell
.\.venv311\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend static server from `frontend/`:

```powershell
..\.venv311\Scripts\python.exe -m http.server 3000 --bind 127.0.0.1
```

For production, use the approved container/process manager and environment configuration rather than these local demo commands.

## Demo Flow

1. Sign in as an operator and confirm the health endpoint is available.
2. Upload a PDF sales document through Main AI, process it, and show mapped header fields and item rows.
3. Show customer/product ERP master resolution, unresolved items, confidence indicators, and correction-learning behavior.
4. Run export pre-check. Demonstrate quality score, grade, missing fields, unresolved masters, row issues, and blockers.
5. Use an intentionally incomplete payload and prove JSON, Excel, and PDF exports remain blocked.
6. Correct the required values, rerun pre-check, and prove allowed JSON, Excel, and PDF exports succeed.
7. Repeat upload and auto-fill with one image, one XLSX sales/purchase order, and one CSV item table.
8. Demonstrate ERP Master Import with scoped sample data and verify tenant isolation with a second organization if configured.
9. Sign in as an admin and show audit/recent-job views without exposing raw secrets or server paths.
10. Confirm generated artifacts are stored beneath the configured output root and covered by retention policy.

## Input-Specific Flows

- PDF: upload, poll the OCR job, then open Workbench auto-fill.
- Image: repeat with PNG or JPEG and confirm the normalized mapping contract.
- Excel/CSV: parse through `/api/v1/forms/workbench/parse-tabular`, review headers and rows, then auto-fill.
- Training dataset: create/select a dataset and confirm best-match header and row mappings.
- Correction learning: save and reapply a correction in the same identity context.
- ERP masters: import scoped sample masters and resolve customer, supplier, and product IDs.
- Quality gate: run export precheck and verify incomplete data returns HTTP 422.
- Export: after approval, export JSON, Excel, and PDF and verify non-empty files.

Expected result: all automated checks pass and no response exposes secrets or local filesystem paths.

## Demo Acceptance Checklist

- PDF, image, XLSX, and CSV enter the same normalized auto-fill pipeline.
- Required fields and item rows map correctly.
- Correction learning and ERP master resolution operate after mapping.
- Unresolved critical masters block export.
- Low-confidence required fields require review.
- Blocked payloads cannot export through any public route.
- Allowed payloads export consistently to JSON, Excel, and PDF.
- No absolute server paths, secrets, or cross-tenant records are exposed.
- Logs, disk usage, OCR latency, and worker health remain within approved limits.

## Final 20-Step Demo Checklist

1. [ ] Start backend with `scripts/start_backend.ps1`.
2. [ ] Start frontend with `scripts/start_frontend.ps1`.
3. [ ] Confirm `/api/v1/health` returns healthy status.
4. [ ] Login as an approved operator.
5. [ ] Upload and process one PDF.
6. [ ] Upload and process one supported image.
7. [ ] Parse and process one XLSX or CSV document.
8. [ ] Confirm the document job completes and mapped data is visible.
9. [ ] Run Main AI Auto Fill.
10. [ ] Save and apply a training dataset mapping.
11. [ ] Save and reapply a correction-learning entry.
12. [ ] Import approved sample ERP master data.
13. [ ] Confirm ERP customer/supplier/product auto-resolution.
14. [ ] Display quality score and export-precheck details.
15. [ ] Export JSON successfully.
16. [ ] Export Excel successfully.
17. [ ] Export PDF successfully.
18. [ ] Demonstrate an incomplete payload blocked with HTTP 422.
19. [ ] Demonstrate a review-required, non-hard-blocking scenario.
20. [ ] Record final pass screenshot/evidence reference: `________________`.

## Stop Conditions

Stop and record evidence if Alembic has multiple heads, the working tree differs from the release revision, any export bypass succeeds, a download resolves outside the output root, tenant data crosses scope, secrets appear in logs/responses, or any required automated check fails.

## Known Pre-release Limitations

The current E2E runner uses four PDFs and legacy export endpoints; it is not sufficient evidence for the full Workbench release. Add image, XLSX, CSV, blocked-export, allowed-export, browser, and path-containment E2E before production approval.
