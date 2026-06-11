$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot ".venv311\Scripts\python.exe"

Set-Location $ProjectRoot
Write-Host "RUN: $Python e2e_runner.py"
& $Python "e2e_runner.py"
Write-Host "PASS: E2E completed"
