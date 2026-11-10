# Release Review: OCR Training and Mapping Milestone

Date: 2026-06-12

## Overall Verdict

**CONDITIONAL PASS**

Runtime verification is green: compile passes, all 475 tests pass, E2E passes for 4/4 documents, migration current/head is 0009, training signature generation and matching were previously verified, mapper behavior is correct, and PDF/XLSX exports pass.

The worktree is not ready for a single release commit. It mixes milestone changes with broad frontend/backend hardening, tracked backup deletions, generated evidence, scripts, and 1,943 quarantined files. Commit preparation must use explicit path selection and must not stage the worktree wholesale.

## Dirty Worktree Summary

- Tracked diff: 79 files, 8,488 insertions, 108,146 deletions.
- Untracked files: 1,966.
- Quarantine content: 1,943 untracked files under `_quarantine_unused/`.
- Non-quarantine untracked files: 23 before this report.
- The large deletion count is primarily tracked backup/evidence files moved into the untracked quarantine tree.
- `frontend/index.html` raw diff is inflated by line-ending churn. Ignoring EOL whitespace, its semantic diff is still 289 insertions and 57 deletions.

## Files Changed by Phase

### 1. Training Dataset Schema and Migration

Commit candidates:

- `app/models/company.py`
- `alembic/versions/0009_add_training_template_signature.py`

Adds template signature, approval, confidence, sample/usage counters, last-used timestamp, and expanded module constraints.

### 2. Training Template Engine

Commit candidates:

- `app/services/training_template_engine.py`
- `tests/test_training_template_engine.py`

Provides deterministic signatures, similarity scoring, dataset matching, usage tracking, and priority-aware field application.

### 3. Upload Pipeline Integration

Commit candidate:

- `app/api/v1/routers/upload.py`

Integrates training matching after regex mapping and before Gemini, applies matched fields, recalculates missing fields, updates usage, and returns training metadata.

Risk: this file also contains timezone edits and mojibake/encoding churn in comments and user-visible evidence strings. Stage or reconstruct the intended training hunks carefully before committing.

### 4. Frontend TRAINING Badge and UI Confidence Changes

Commit candidates:

- `frontend/index.html`
- `tests/check_js.js`

Adds the TRAINING source badge and KPI. The same frontend diff also contains API connectivity handling, date input changes, field-map corrections, export validation state, and other workbench behavior. These changes exceed the stated badge/percentage scope and require explicit semantic review before staging.

### 5. Training API Persistence and Auto-Signature

Commit candidate:

- `app/api/v1/routers/companies.py`

Adds persistence fields, expanded module validation, raw OCR metadata extraction, and automatic `tpl:` signature generation.

### 6. Field Mapping Enhancement

Commit candidate:

- `app/services/company_form_mapper.py`

Adds stacked-label lookahead, peer-value skipping, date-only invoice rejection, embedded-date boundary protection, and blank-cell safeguards.

### 7. Tests

Milestone test candidates:

- `tests/test_training_template_engine.py`
- Relevant training, autosignature, and invoice-mapping hunks in `tests/test_platform.py`
- Relevant TRAINING badge checks in `tests/check_js.js`

Other modified test files belong to separate ERP/export hardening work:

- `tests/test_task4_erp_master.py`
- `tests/test_task5_excel_csv.py`
- `tests/test_task6_export_precheck.py`
- `tests/artifacts/task11_normal_export_gate_fix_report.json`

### 8. Temporary or Accidental Files

Do not include in milestone commits:

- `_quarantine_unused/` (1,943 archived/generated files)
- `fix_map.py`
- `scripts/*.txt`
- `scripts/find_tests.bat`
- `scripts/fix_utcnow*.ps1`
- `scripts/quarantine.ps1`
- `scripts/run_targeted_tests.bat`
- `scripts/verify_7c.py`
- `scripts/verify_7c_match.py`

Tracked deletions of root audit files, backup source copies, frontend backups, test backups, and verification outputs are cleanup changes. They should be reviewed as a separate cleanup commit only after confirming the external archive is authoritative.

### 9. Unknown or Separate-Scope Changes Needing Review

These are valid-looking hardening changes but are not cleanly part of the training/mapping milestone:

- `app/api/v1/routers/admin.py`
- `app/api/v1/routers/forms.py`
- `app/models/batch.py`
- `app/models/extracted_data.py`
- `app/models/fill_job.py`
- `app/models/job.py`
- `app/models/log.py`
- `app/models/user.py`
- `app/services/audit_service.py`
- `app/services/export_precheck_engine.py`
- `app/services/form_fill_engine.py`
- `pytest.ini`
- `docs/FRONTEND_LIVE_DEMO_CHECKLIST.md`
- `docs/TASK11_NORMAL_EXPORT_GATE_FIX.md`

These should be assigned to separate hardening commits or deferred after review.

## Verification Results

### Compile

PASS: `python -m compileall app tests`

### Tests

PASS: `475 passed, 2 warnings in 3.08s`

Warnings are existing dependency/deprecation warnings from ReportLab and python-jose.

### E2E

PASS: 4/4 documents.

- `salesinvoice.pdf`: 278 fields, EXPORT_INVOICE, PDF/XLSX passed.
- `Glsprint.pdf`: 330 fields, COMMERCIAL_INVOICE, PDF/XLSX passed.
- `POPRINT.pdf`: 218 fields, PURCHASE_ORDER, PDF/XLSX passed.
- `GRN.pdf`: 99 fields, GRN, PDF/XLSX passed.

All documents currently return `needs_review`, consistent with the active validation policy.

## Migration Status

- `alembic current`: `0009 (head)`
- `alembic heads`: `0009 (head)`
- Database dialect reported by Alembic: PostgreSQL.

The requested `python -m alembic` command fails because the installed Alembic package has no `alembic.__main__`. The environment entrypoint `.venv311\Scripts\alembic.exe` succeeds and is the valid command for this environment.

## Training Pipeline Proof

Previously verified against the live PostgreSQL-backed API:

- POST without `template_signature`, with `metadata_json.raw_ocr_text`, returned HTTP 201.
- Generated signature started with `tpl:`.
- Required schema columns were present.
- `match_training_dataset` returned the same dataset with score `0.90`.
- Temporary verification dataset was removed after proof.
- Upload responses expose training match metadata.

## Mapper Proof

- Glsprint maps `invoice_number = SPR023/26-27`.
- Stacked invoice number/date regression passes.
- Currency tokens remain rejected as invoice numbers.
- Date-only strings remain rejected as invoice numbers.
- `SPR023/26-27`, `CI/24-25/009`, and `12345/2026` remain accepted.
- salesinvoice keeps `invoice_number` blank and preserves `currency = EUR`.

## Export Proof

E2E generated valid PDF and XLSX output for all four sample documents. Both export endpoints returned successful, structurally valid files.

## Untracked File Review

### Commit

- `alembic/versions/0009_add_training_template_signature.py`
- `app/services/training_template_engine.py`
- `tests/test_training_template_engine.py`
- This release report, in the final docs/release-report commit.

### Commit Only with Separate Scope Review

- `docs/FRONTEND_LIVE_DEMO_CHECKLIST.md`
- `docs/TASK11_NORMAL_EXPORT_GATE_FIX.md`
- `tests/artifacts/task11_normal_export_gate_fix_report.json`

### Archive or Ignore

- `_quarantine_unused/`
- `scripts/*.txt`
- Temporary verification scripts and batch/PowerShell helpers under `scripts/`
- `fix_map.py`

### Delete Later Only After Approval

- Quarantined generated exports and debug images after retention policy approval.
- Temporary verification helpers after confirming their evidence is archived.
- Tracked backup/evidence originals currently shown as deleted, only as a separate approved cleanup action.

## Risks

1. Do not use `git add -A`; it would stage quarantine files and broad cleanup deletions.
2. `upload.py` has encoding/mojibake churn mixed with functional edits.
3. `frontend/index.html` has whole-file EOL churn and multiple unrelated semantic changes.
4. The milestone is mixed with export-gate, ERP, timezone, admin, and workbench hardening.
5. The worktree lacks a clean baseline commit, increasing partial-staging error risk.
6. Generated E2E output and uploads continue to change during verification and should remain ignored.

## Recommended Commit Plan

### Commit 1: Training Dataset Schema and API

- `app/models/company.py`
- `alembic/versions/0009_add_training_template_signature.py`
- Training persistence/autosignature hunks from `app/api/v1/routers/companies.py`
- Corresponding focused tests from `tests/test_platform.py`

### Commit 2: Training Template Engine and Upload Integration

- `app/services/training_template_engine.py`
- Training integration hunks from `app/api/v1/routers/upload.py`
- `tests/test_training_template_engine.py`
- Corresponding upload integration tests

Before staging, remove or isolate unrelated encoding and timezone churn from the upload diff.

### Commit 3: Frontend TRAINING Badge and Confidence Cleanup

- Reviewed semantic hunks from `frontend/index.html`
- Matching checks from `tests/check_js.js`

Normalize the frontend file's line endings in a controlled, separate operation or reconstruct only intended hunks. Do not commit the current raw whole-file diff without review.

### Commit 4: Invoice Mapping Enhancement and Regression Tests

- `app/services/company_form_mapper.py`
- Focused invoice mapping regressions from `tests/test_platform.py`

### Commit 5: Documentation and Release Report

- `RELEASE_REVIEW_PHASE_TRAINING_MAPPING.md`
- Only milestone-relevant documentation explicitly approved for release

Cleanup/quarantine changes and unrelated hardening should not be folded into these five commits.

## Commit Status

No commit was created during this review.
