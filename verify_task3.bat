@echo off
cd /d D:\OCR_MASTER\ocr-platform-complete
echo === COMPILE ALEMBIC ===
.venv311\Scripts\python.exe -m compileall alembic -q
echo === ALEMBIC BEFORE ===
.venv311\Scripts\alembic.exe current
echo === ALEMBIC UPGRADE HEAD ===
.venv311\Scripts\alembic.exe upgrade head
echo === ALEMBIC AFTER ===
.venv311\Scripts\alembic.exe current
echo === COMPILE APP ===
.venv311\Scripts\python.exe -m compileall app -q
echo === PYTEST ===
.venv311\Scripts\python.exe -m pytest tests/test_platform.py tests/test_gemini.py tests/test_dataset_normalization.py -v 2>&1
echo === E2E ===
.venv311\Scripts\python.exe e2e_runner.py
echo === JS CHECK ===
node tests/check_js.js
echo === DONE ===
