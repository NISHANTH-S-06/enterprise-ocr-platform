@echo off
cd /d D:\OCR_MASTER\ocr-platform-complete
echo === JS CHECK ===
node tests/check_js.js
echo === COMPILE APP ===
.venv311\Scripts\python.exe -m compileall app -q
echo === COMPILE ALEMBIC ===
.venv311\Scripts\python.exe -m compileall alembic -q
echo === ALEMBIC CURRENT ===
.venv311\Scripts\alembic.exe current
echo === PYTEST FULL + TASK4 ===
.venv311\Scripts\python.exe -m pytest tests/test_platform.py tests/test_gemini.py tests/test_dataset_normalization.py tests/test_task4_erp_master.py -v 2>&1
echo === E2E ===
.venv311\Scripts\python.exe e2e_runner.py
echo === DONE ===
