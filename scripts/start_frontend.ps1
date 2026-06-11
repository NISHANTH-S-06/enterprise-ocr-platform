$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot ".venv311\Scripts\python.exe"
$Frontend = Join-Path $ProjectRoot "frontend"
$Arguments = @("-m", "http.server", "3000", "--bind", "127.0.0.1")

Set-Location $Frontend
Write-Host "RUN: $Python $($Arguments -join ' ')"
& $Python @Arguments
