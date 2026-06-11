$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot ".venv311\Scripts\python.exe"
$Arguments = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000")

Set-Location $ProjectRoot
Write-Host "RUN: $Python $($Arguments -join ' ')"
& $Python @Arguments
