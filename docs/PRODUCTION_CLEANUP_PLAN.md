# Production Cleanup Plan

Task 8 executed the allowlisted backup archival and project-cache cleanup on 2026-06-11. Higher-risk retention, environment removal, log deletion, and code-organization work remain approval-only.

## Entry Conditions

- Tag or archive the current working tree and database backup.
- Commit the exact tested source, migration, tests, and runner.
- Record checksums for files proposed for archival or deletion.
- Run cleanup first on a disposable clone, never directly on production.

## Phase A: Safe File Cleanup

1. Inventory every `*.before-*`, `*.bak`, copied `.py`, frontend backup, and test backup.
2. Move approved backups to a dated `backups/` archive outside import and Alembic paths.
3. Verify Alembic still reports only `0008 (head)` after moving the `0007` backup.
4. Remove generated `__pycache__` and `.pytest_cache` directories.
5. Classify logs, audit text, verification outputs, browser profiles/downloads, and scratch files; archive evidence and delete only confirmed disposable data.
6. Retire `.venv` or `.venv_new` only after proving `.venv311` is the documented environment.
7. Apply retention rules to `uploads/` and `output/`; preserve legal/audit/customer-required artifacts.
8. Move historical task reports to `docs/reports/` while preserving references.

Exit gate: clean checkout builds, Alembic remains at `0008`, and all verification commands pass.

## Phase B: Documentation

1. Update README with the supported Workbench upload, mapping, resolver, pre-check, and export flow.
2. Publish the canonical endpoint list and mark legacy fill/export routes deprecated.
3. Document PDF/image/XLSX/CSV limits and failure responses.
4. Document ERP master import/resolution and tenant scope.
5. Add production environment, database migration, backup/restore, and rollback instructions.
6. Add demo, troubleshooting, log redaction, and file-retention guidance.

Exit gate: a new operator can deploy and run the demo from documentation without undocumented local paths.

## Phase C: Code Organization - Approval Required

1. Route all export/fill operations through one pre-check contract while preserving public endpoint compatibility or issuing a versioned deprecation.
2. Move router-owned correction helpers into a service to remove the service-to-router dependency.
3. Consolidate normalization/alias helpers only with characterization tests.
4. Split `forms.py` by Workbench, validation, and export ownership without changing URLs.
5. Split `frontend/index.html` only after UI regression coverage exists.
6. Keep mapper decomposition behavior-neutral and protected by golden document tests.

Exit gate: public contracts are unchanged or explicitly versioned, and characterization tests show equivalent outputs.

## Phase D: Production Hardening

1. Remove `skip_validation` from client control and enforce pre-check on legacy and fill paths.
2. Resolve artifact paths under a configured root, reject traversal/out-of-root paths, and return opaque download IDs.
3. Replace development credentials; document secret rotation, CORS origins, JWT lifetime, and admin bootstrap.
4. Add rate limiting/login abuse controls and structured sensitive-data-safe logging.
5. Add bounded OCR result caching and upload/export retention jobs.
6. Stream or spool large files and enforce XLSX/CSV limits while reading, before full materialization.
7. Move blocking OCR to a bounded worker queue; define timeout, retry, cancellation, concurrency, and memory limits.
8. Add readiness/dependency health checks, metrics, alerting, and disk-capacity monitoring.
9. Test backup restore and migration rollback in staging.

Exit gate: security review passes and load/retention tests demonstrate bounded memory, disk, and queue behavior.

## Phase E: Final Release Validation

1. Build from a clean, tagged checkout.
2. Run JavaScript, duplicate-ID, compile, Alembic, pytest, and E2E checks.
3. Add and run Workbench E2E for image, XLSX, CSV, allowed export, blocked export, and JSON/Excel/PDF parity.
4. Run tenant-isolation and artifact path-containment tests.
5. Execute the manual demo runbook with a non-admin user and an admin user.
6. Verify logs contain no secrets, raw documents, or absolute server paths beyond approved operational metadata.
7. Verify backup, restore, rollback, retention, and incident contacts.

Release gate: zero critical blockers, clean source revision, sole expected Alembic head, all automated checks pass, and the release owner signs the runbook.

## Protected Items

Do not delete migrations `0001`-`0008`, active Task 4-6 source/tests, `e2e_runner.py`, deployed secrets, `templates_store`, database data, or customer uploads/exports without an approved retention decision. Do not use `alembic stamp` as cleanup or recovery.
