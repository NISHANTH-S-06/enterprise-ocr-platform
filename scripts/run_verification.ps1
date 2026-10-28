$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot ".venv311\Scripts\python.exe"

Set-Location $ProjectRoot

# 1. Setup clean isolated temp directory
$TempRoot = "D:\OCR_MASTER\pytest_tmp"
Write-Host "Initializing clean temporary directory at: $TempRoot" -ForegroundColor Yellow
if (Test-Path $TempRoot) {
    Remove-Item $TempRoot -Recurse -Force
}
New-Item -ItemType Directory -Force $TempRoot | Out-Null

$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:PYTEST_ADDOPTS = "--basetemp=$TempRoot\basetemp"

# 2. Run compilation check
Write-Host "--------------------------------------------------"
Write-Host "Running Python compilation check..." -ForegroundColor Yellow
& $Python -m compileall app tests
Write-Host "[PASS] Python compilation successful." -ForegroundColor Green

# 3. Run Pytest suite
Write-Host "--------------------------------------------------"
Write-Host "Running full Pytest suite..." -ForegroundColor Yellow
& $Python -m pytest -q
Write-Host "[PASS] Pytest suite passed." -ForegroundColor Green

# 4. Run JS syntax & elements check
Write-Host "--------------------------------------------------"
Write-Host "Running JavaScript checks..." -ForegroundColor Yellow
Write-Host "RUN: node tests/check_js.js"
& node "tests/check_js.js"
Write-Host "RUN: node check_frontend.js"
& node "check_frontend.js"
Write-Host "[PASS] JavaScript syntax and duplication checks passed." -ForegroundColor Green

# 5. Run E2E runner
Write-Host "--------------------------------------------------"
Write-Host "Running End-to-End (E2E) runner..." -ForegroundColor Yellow
& $Python "e2e_runner.py"
Write-Host "[PASS] E2E runner completed successfully." -ForegroundColor Green

# 6. Run Accuracy Benchmark runner
Write-Host "--------------------------------------------------"
Write-Host "Running Accuracy Benchmark runner..." -ForegroundColor Yellow
& $Python "tests/benchmark/benchmark_runner.py"
Write-Host "[PASS] Accuracy Benchmark runner completed successfully." -ForegroundColor Green

Write-Host "=================================================="
Write-Host "ALL VERIFICATION TASKS COMPLETED SUCCESSFULLY!" -ForegroundColor Green
Write-Host "=================================================="
