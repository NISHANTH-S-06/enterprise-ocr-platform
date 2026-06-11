$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot ".venv311\Scripts\python.exe"
$Alembic = Join-Path $ProjectRoot ".venv311\Scripts\alembic.exe"
$Tests = @(
    "tests/test_platform.py",
    "tests/test_gemini.py",
    "tests/test_dataset_normalization.py",
    "tests/test_task4_erp_master.py",
    "tests/test_task5_excel_csv.py",
    "tests/test_task6_export_precheck.py",
    "tests/test_task7_1_critical_blockers.py"
)

Set-Location $ProjectRoot
Write-Host "RUN: node tests/check_js.js"
& node "tests/check_js.js"
Write-Host "RUN: python -m compileall app -q"
& $Python -m compileall app -q
Write-Host "RUN: python -m compileall alembic -q"
& $Python -m compileall alembic -q
Write-Host "RUN: alembic heads"
& $Alembic heads
Write-Host "RUN: alembic current"
& $Alembic current
Write-Host "RUN: pytest $($Tests -join ' ') -v"
& $Python -m pytest @Tests -v
Write-Host "PASS: release verification completed"
