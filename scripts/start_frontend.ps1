$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot ".venv311\Scripts\python.exe"
$Frontend = Join-Path $ProjectRoot "frontend"

# 1. Verify virtual environment exists
if (-not (Test-Path $Python)) {
    Write-Host "ERROR: Virtual environment not found at: $Python" -ForegroundColor Red
    Write-Host "Please ensure the .venv311 environment is properly initialized." -ForegroundColor Red
    Exit 1
}

$Port = $env:FRONTEND_PORT
if (-not $Port) {
    $envPath = Join-Path $ProjectRoot ".env"
    if (Test-Path $envPath) {
        $match = Get-Content $envPath | Where-Object { $_ -match "^\s*FRONTEND_PORT\s*=\s*(\d+)" }
        if ($match) { $Port = ($match -replace "^\s*FRONTEND_PORT\s*=\s*", "").Trim() }
    }
}
if (-not $Port) { $Port = 3000 }
$Port = [int]$Port

$Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($Connection) {
    $PIDOwner = $Connection[0].OwningProcess
    Write-Host "ERROR: Port $Port is already in use by process ID: $PIDOwner." -ForegroundColor Red
    Write-Host "Please terminate the process or run stop_local_servers.ps1 before launching." -ForegroundColor Red
    Exit 1
}

$Arguments = @("-m", "http.server", "$Port", "--bind", "127.0.0.1")

Set-Location $Frontend
Write-Host "Starting OCR Platform Frontend on http://127.0.0.1:$Port/index.html ..." -ForegroundColor Green
Write-Host "RUN: $Python $($Arguments -join ' ')"
& $Python @Arguments
