$ErrorActionPreference = "Continue"

Write-Host "Stopping OCR local servers..." -ForegroundColor Yellow

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Get-EnvPort([string]$VarName, [int]$DefaultPort) {
    $portVal = [System.Environment]::GetEnvironmentVariable($VarName)
    if (-not $portVal) {
        $envPath = Join-Path $ProjectRoot ".env"
        if (Test-Path $envPath) {
            $match = Get-Content $envPath | Where-Object { $_ -match "^\s*$VarName\s*=\s*(\d+)" }
            if ($match) { $portVal = ($match -replace "^\s*$VarName\s*=\s*", "").Trim() }
        }
    }
    if ($portVal -and [int]::TryParse($portVal, [ref]$null)) { return [int]$portVal }
    return $DefaultPort
}

$BackendPort = Get-EnvPort "BACKEND_PORT" 8000
$FrontendPort = Get-EnvPort "FRONTEND_PORT" 3000

# 1. Stop backend on port
$ConnBackend = Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue
if ($ConnBackend) {
    $PIDOwner = $ConnBackend[0].OwningProcess
    Write-Host "Found backend process on port $BackendPort (PID: $PIDOwner). Terminating..." -ForegroundColor Cyan
    Stop-Process -Id $PIDOwner -Force -ErrorAction SilentlyContinue
    Write-Host "Backend server stopped successfully." -ForegroundColor Green
} else {
    Write-Host "No backend server process found on port $BackendPort." -ForegroundColor Gray
}

# 2. Stop frontend on port
$ConnFrontend = Get-NetTCPConnection -LocalPort $FrontendPort -State Listen -ErrorAction SilentlyContinue
if ($ConnFrontend) {
    $PIDOwner = $ConnFrontend[0].OwningProcess
    Write-Host "Found frontend process on port $FrontendPort (PID: $PIDOwner). Terminating..." -ForegroundColor Cyan
    Stop-Process -Id $PIDOwner -Force -ErrorAction SilentlyContinue
    Write-Host "Frontend server stopped successfully." -ForegroundColor Green
} else {
    Write-Host "No frontend server process found on port $FrontendPort." -ForegroundColor Gray
}

Write-Host "Server cleanup complete." -ForegroundColor Green
