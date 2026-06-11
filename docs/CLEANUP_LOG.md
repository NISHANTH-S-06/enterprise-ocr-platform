# Cleanup Log

Timestamp: 2026-06-11 +05:30

## Completed Actions

- Moved 52 `app/**/*.before-task*.py` backups to `backups/backend/`, preserving the complete original path below that folder.
- Moved all 11 `frontend/index.before-task*.html` files to `backups/frontend/`.
- Moved `alembic/env.before-task4-migration.py` to `backups/alembic/alembic__env.before-task4-migration.py`.
- Moved `alembic/versions/0007_add_correction_memory_columns.before-safe-fk-fix.py.bak` to `backups/alembic/alembic__versions__0007_add_correction_memory_columns.before-safe-fk-fix.py.bak`.
- Deleted project-level Python caches under root, `app/`, `alembic/`, `tests/`, and `scratch/`.
- Deleted root `.pytest_cache`.

## Exact Backend Inventory

Archived filenames by source area:

- Routers: `companies` task10/task2-manager/task2-standardization/task3/task4/task8/task9; `fill` task3/task7-1; `forms` task1/task2/task3-correction/task3-ERP/task4-1/task4/task5/task6/task7-1/task7-row/task8/task9; `upload` task3/task6/task7/task8.
- Models: `company` task10/task2/task3/task4-resolver/task4-migration/task9; `workbench` task3/task4; model `__init__` task4/task9.
- Services: `company_form_mapper` task3/task6/task7/task8; `final_field_normalizer` task3/task7/task8/task9; `form_fill_engine` task6/task7-1; `validation_engine` task6/task7/task9; `validation_service` task3/task6/task7/task9.

Every archived backend file retains its original `app/...` relative path beneath `backups/backend/`; this is the authoritative one-to-one movement record.

## Exact Frontend Inventory

- `index.before-task1-main-autofill-mapping.html`
- `index.before-task1-ui-cleanup-review.html`
- `index.before-task12-live-hotfix.html`
- `index.before-task2-dataset-manager.html`
- `index.before-task3-correction-learning.html`
- `index.before-task3-erp-export.html`
- `index.before-task5-browser-click-fix.html`
- `index.before-task5-excel-csv.html`
- `index.before-task6-export-precheck.html`
- `index.before-task8-dataset-e2e.html`
- `index.before-task9-master-mapping.html`

## Not Touched

Active source, migrations `0001`-`0008`, frontend entrypoint, tests, test artifacts, databases, uploads, output, templates, datasets, environment files, generated exports, and customer files were retained.

## Needs Approval

Root logs, audit/verification outputs, old batch scripts, browser profiles, generated exports, and temporary reports were retained. One existing Python smoke runner has a credential-like default; it was not changed and is excluded from release packaging.
