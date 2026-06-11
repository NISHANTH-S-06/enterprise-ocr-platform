# Task 10 UI Cleanup Report

## Audit Findings
- `frontend/index.html:935-936` contains the normal-user nav entry for `ERP Master Import`.
- `frontend/index.html:1920` contains the ERP master import panel itself.
- `frontend/index.html:6961-6972` contains the JavaScript page switcher and the import panel open logic.
- `tests/test_task4_erp_master.py:262-276` contains the frontend assertions that now validate the hidden-by-default behavior and the `?admin=1` gate.

## What Changed
- Hid `ERP Master Import` from the normal sidebar by default.
- Hid the ERP master import panel from the normal dashboard.
- Added a frontend-only `?admin=1` visibility gate.
- Kept the backend import endpoint, resolver logic, and related tests intact.
- Added the required HTML/JS comment: `ERP Master Import is admin/setup only and hidden from normal user flow.`

## Admin Mode
- Normal mode: ERP Master Import stays hidden.
- Admin mode: `?admin=1` can reveal the admin-only nav item and panel.
- No backend auth or RBAC changes were made.

## Normal User Flow
`Upload document -> Process OCR -> AI Auto Fill -> Review fields / quality gate -> Export PDF / Excel / JSON -> Save Corrections / Training upload-test where already present`

## Files Changed
- `frontend/index.html`
- `tests/test_task4_erp_master.py`

## Verification
- `node tests/check_js.js` PASS
- `.venv311\\Scripts\\python.exe -m compileall app -q` PASS
- `.venv311\\Scripts\\python.exe -m compileall alembic -q` PASS
- `.venv311\\Scripts\\python.exe -m pytest tests/test_platform.py tests/test_task4_erp_master.py tests/test_task6_export_precheck.py -v` PASS
- `.venv311\\Scripts\\python.exe -m pytest tests/test_platform.py tests/test_gemini.py tests/test_dataset_normalization.py tests/test_task4_erp_master.py tests/test_task5_excel_csv.py tests/test_task6_export_precheck.py tests/test_task7_1_critical_blockers.py -v` PASS
- Optional E2E run: not executed

## Risks
- The admin flag is frontend-only and is not a security boundary.
- The ERP import code still exists in the bundle and can be triggered directly from the console if someone knows the function name.
- No backend behavior was changed, so existing import APIs remain available.
