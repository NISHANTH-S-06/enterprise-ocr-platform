# Task 7 Final Production Audit

Audit date: 2026-06-11  
Scope: audit and release planning only; no runtime code, schema, UI, or business logic changed.

## Executive Summary

**Rating: NOT READY (78%)**

The core platform is functionally healthy: the required 368 tests pass, all four PDF E2E documents pass, JavaScript parses, no duplicate DOM IDs exist, and Alembic is at the single `0008` head. Release is blocked because legacy export/fill paths can bypass the Task 6 quality gate, fill downloads do not prove path containment, and the tested source contains uncommitted/untracked release files.

## Verified Status

| Check | Result |
|---|---|
| JavaScript parse | PASS |
| Duplicate frontend IDs | 0 |
| Compile `app` | PASS |
| Compile `alembic` | PASS |
| Alembic heads | `0008 (head)` |
| Alembic current | `0008 (head)` |
| Pytest | 368 passed, 11 warnings |
| E2E | 4/4 PASS |

## Critical Blockers

1. `/api/v1/forms/export/pdf` and `/api/v1/forms/export/excel` accept client-controlled `skip_validation`, allowing the Task 6 export pre-check to be bypassed.
2. `/api/v1/fill/` maps and exports through a separate flow that does not enforce the Task 6 pre-check.
3. Modified and untracked production source, migration, tests, and reports make the tested release non-reproducible from a committed revision.
4. Fill job responses expose server `output_path` values, and download logic trusts the stored path without proving it remains inside the configured output directory.

## Structure And Cleanup Findings

The expected application, router, service, model, schema, migration, frontend, test, script, and documentation areas are present. Cleanup is needed, but it must be separated from release hardening.

- Backup copies exist inside active Python, frontend, test, and migration trees.
- `alembic/versions/0007_add_correction_memory_columns.before-safe-fk-fix.py.bak` is not loaded as a migration today, but should not remain in the migration directory.
- `.venv`, `.venv311`, and `.venv_new` coexist.
- Cache folders, logs, browser profiles, scratch data, and generated outputs remain in the repository workspace.
- `uploads` contains 601 files (55,719,880 bytes); `output` contains 1,683 files (8,524,988 bytes).
- The browser profile and scratch areas contain approximately 150 MB and 12 MB respectively.

## Endpoint Audit

There are 73 registered API endpoints and no duplicate method/path registrations. Router groups cover auth, admin, companies, document quality, fill, forms, health, and upload.

The Workbench JSON, Excel, and PDF exports run the Task 6 pre-check. The legacy forms exports and `/fill/` create inconsistent contracts and gate behavior. Raw OCR is returned where operator review requires it; admin recent-list responses redact raw OCR and file paths. Fill job responses still reveal absolute output paths.

## Service Audit

- OCR service: upload validation, extraction, classification, and job processing.
- Company form mapper: document classification and form/row mapping.
- Final normalizer: canonical field selection and normalization.
- Correction services: correction memory and learned correction application.
- ERP resolver: scoped customer, supplier, and product resolution.
- Tabular parser: XLSX/CSV parsing into the OCR-like contract.
- Export pre-check: required fields, confidence, masters, rows, quality score, and gate.
- Exporters: JSON, Excel, and PDF artifact generation.

Risks include large `forms.py` and `company_form_mapper.py` responsibilities, broad exception fallbacks, repeated alias/normalization helpers, and `workbench_correction_apply.py` importing router helpers from `forms.py`. No active TODO/FIXME markers were found.

## Database And Alembic

The migration chain is linear from `0001` through `0008`, and the current database matches the sole head. No migration was created or changed during this audit.

Downgrade safety is not fully demonstrated. Migration `0007` does not completely restore earlier `company_id` constraints, and `0008` can be incompatible with rows using newly permitted master types when downgraded. Production rollback must use a tested backup/restore procedure, not an assumed downgrade.

## Frontend Health

JavaScript parses, duplicate IDs are zero, and no referenced `onclick` function is missing. Main AI, Training, ERP Master Import, Quality Gate, and export UI are present. Main AI and training support PDF, image, XLSX, and CSV; one legacy upload control still omits XLSX/CSV.

The main risk is concentration: `frontend/index.html` is 393,388 bytes and 7,227 lines with inline HTML, CSS, and JavaScript. LocalStorage also holds recent uploads, drafts, OCR/result data, dataset caches, and ERP master data without a documented expiry policy.

## Test Coverage

The required suites pass. Unit/API coverage is broad for mapping, normalization, correction learning, master resolution, tabular parsing, quality gates, and organization scoping. Some tests use direct function calls and mocks rather than real ASGI requests.

The four-document E2E run covers upload, OCR/poll, classification, validation threshold, and legacy PDF/Excel export for sales invoice, commercial invoice, purchase order, and GRN PDFs. It does not cover real images, XLSX/CSV, Workbench auto-fill/pre-check, JSON export, blocked exports, browser UI, or multi-tenant concurrency. The runner also depends on absolute external dataset paths and currently appears affected by ignore rules.

## Security And Configuration

Production settings reject placeholder/short JWT secrets and wildcard CORS. Upload validation includes extension, MIME/signature, file size, PDF-page, and image-dimension checks. `.env` is ignored and no secret values are included in this report.

Before release, remove validation bypasses, constrain download paths, add login/rate-abuse controls, replace development Compose credentials, review exception/path logging, and define raw OCR/file retention. Authorized raw OCR access is operationally justified but must be governed.

## Performance And Operations

- OCR/native processing is blocking work behind an async API and the Docker configuration uses one worker.
- Timeout cancellation may not terminate native OCR work.
- Uploads are loaded into memory; XLSX/CSV readers can materialize all rows before normalized limits apply.
- ERP resolution compares rows against loaded candidate mappings in memory.
- `OCR_RESULT_CACHE` is unbounded.
- No verified general retention job exists for uploads and exported files.
- The single large frontend increases parse, maintenance, and regression cost.

## Release Gaps

1. Enforce one non-bypassable export pre-check across every public export/fill route.
2. Harden artifact download path containment and response redaction.
3. Commit and review the exact tested release, including migration `0008`, Task 4-6 services/tests, and `e2e_runner.py`.
4. Add Workbench allowed/blocked export integration and E2E coverage for JSON, Excel, PDF, image, XLSX, and CSV.
5. Define retention, production credentials, worker sizing, monitoring, backup, and rollback procedures.

## Files Not Safe To Delete

Do not delete active application source, migrations `0001`-`0008`, Task 4-6 tests, `e2e_runner.py`, `templates_store`, the deployed `.env`, current database data, or uploads/outputs before retention and legal review. Backup-looking files must be checksummed and classified before archival or removal.

The exact phased plan is in `docs/PRODUCTION_CLEANUP_PLAN.md`; the gated demonstration procedure is in `docs/FINAL_DEMO_RUNBOOK.md`.
