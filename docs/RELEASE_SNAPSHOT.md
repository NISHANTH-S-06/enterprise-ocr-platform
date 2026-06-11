# RELEASE SNAPSHOT — Task 7.1 Critical Blockers Fix

**Project:** OCR Enterprise Platform (Aline X)
**Task 8 Stage:** Production-ready candidate after safe cleanup and release packaging
**Task 8 Date:** 2026-06-11
**Release Stage:** Task 7.1 — Critical Production Blockers Fixed
**Date:** Generated after Task 7.1 completion
**Alembic Expected:** 0008 head

---

## Start Commands

### Backend (FastAPI)
```bash
# From project root
.\.venv311\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Frontend
Open `frontend/index.html` directly in browser, or serve statically:
```bash
.\.venv311\Scripts\python.exe -m http.server 3000 --bind 127.0.0.1 --directory frontend
```

### Database Migration
```bash
.venv311\Scripts\alembic.exe upgrade head
```

---

## Verification Commands

### JS Static Parse Check
```bash
node tests/check_js.js
```

### Backend Compile Check
```bash
.venv311\Scripts\python.exe -m compileall app -q
.venv311\Scripts\python.exe -m compileall alembic -q
```

### Alembic State
```bash
.venv311\Scripts\alembic.exe heads
.venv311\Scripts\alembic.exe current
```

### Full Pytest Suite
```bash
.venv311\Scripts\python.exe -m pytest tests/test_platform.py tests/test_gemini.py tests/test_dataset_normalization.py tests/test_task4_erp_master.py tests/test_task5_excel_csv.py tests/test_task6_export_precheck.py tests/test_task7_1_critical_blockers.py -v
```

### End-to-End Runner
```bash
.venv311\Scripts\python.exe e2e_runner.py
```

---

## Supported Upload Types
- PDF (`.pdf`)
- Image: PNG, JPEG, TIFF, BMP, WebP
- Excel (`.xlsx`)
- CSV (`.csv`)

## Supported Export Types
- JSON
- Excel (`.xlsx`)
- PDF

---

## Critical Blocker Fixes (Task 7.1)

| Blocker | Status |
|---|---|
| Legacy export bypass (`/forms/export/pdf`, `/forms/export/excel`) | FIXED — `_resolve_export_fields` now gates on `build_export_precheck` |
| Fill route bypass (`/fill/`) | FIXED — `form_fill_engine` runs `build_export_precheck` before any export |
| Unsafe output path / path traversal | FIXED — `safe_join_output_path` guards fill downloads |
| Absolute path exposure in API response | FIXED — `get_fill_job` no longer returns `output_path`; returns `output_filename` + `download_url` |
| Release snapshot | FIXED — this document |

---

## Known Limitations

- Ollama integration: planned, not yet implemented (Gemini fallback only)
- Fill engine (`/fill/`) uses `module="general"` for export_precheck — less strict than workbench precheck
- `skip_validation=True` skips legacy validation only; export precheck remains mandatory
- Batch ZIP paths are generated under `output/batches/{batch_id}/` and responses expose only a download URL
- Frontend is a single static file (`frontend/index.html`) — no React/Vite build step required

## Remaining Risks

- Alembic migration 0008 must be applied before first run
- Fill job `output_path` values created before Task 7.1 may be outside `output/` if `OUTPUT_DIR` was changed — those will return HTTP 400 on download

---

## Demo Flow

1. Login with OPERATOR credentials
2. Upload a PDF invoice via Upload panel
3. Wait for OCR to complete (status → completed)
4. Open Main AI Fill workbench → select Sales or Purchase
5. Click **AI Auto Fill** — fields populate from OCR
6. Review extracted fields; correct any wrong values
7. Click **Save Corrections** if corrections were made
8. Click **Export Precheck** to run quality gate
9. If grade A or B → click **Export JSON / Export Excel / Export PDF**
10. Download file — verify no absolute path in response

---

## Verification Checklist

- [x] JS parse PASS
- [x] Duplicate IDs = 0
- [x] Compile app PASS
- [x] Compile alembic PASS
- [x] Alembic heads: 0008 head
- [x] Alembic current: 0008 head
- [x] Pytest PASS (395 tests)
- [x] E2E 4/4 PASS
- [x] No export route bypasses export_precheck
- [x] Fill route includes export_precheck
- [x] No absolute output path exposed in fill job API response
- [x] Path traversal rejected with HTTP 400
- [x] Release snapshot exists: docs/RELEASE_SNAPSHOT.md
- [x] Report exists: tests/artifacts/task7_1_critical_blockers_report.json
