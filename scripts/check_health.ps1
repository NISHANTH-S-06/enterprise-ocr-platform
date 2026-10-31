$ErrorActionPreference = "Continue"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendPort = $env:BACKEND_PORT
if (-not $BackendPort) {
    $envPath = Join-Path $ProjectRoot ".env"
    if (Test-Path $envPath) {
        $match = Get-Content $envPath | Where-Object { $_ -match "^\s*BACKEND_PORT\s*=\s*(\d+)" }
        if ($match) { $BackendPort = ($match -replace "^\s*BACKEND_PORT\s*=\s*", "").Trim() }
    }
}
if (-not $BackendPort) { $BackendPort = 8000 }

$BaseUrl = "http://127.0.0.1:$BackendPort/api/v1"

Write-Host "Running health and readiness checks..." -ForegroundColor Yellow

# 1. GET /health
try {
    $Res = Invoke-RestMethod -Uri "$BaseUrl/health" -Method Get -UseBasicParsing
    Write-Host "[PASS] GET  /health: Status=$($Res.status), App=$($Res.app), Env=$($Res.environment)" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] GET  /health: Connection or request failed ($_.Exception.Message)" -ForegroundColor Red
}

# 2. HEAD /health
try {
    $Res = Invoke-WebRequest -Uri "$BaseUrl/health" -Method Head -UseBasicParsing
    Write-Host "[PASS] HEAD /health: Status=$($Res.StatusCode)" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] HEAD /health: Request failed ($_.Exception.Message)" -ForegroundColor Red
}

# 3. GET /health/ready
try {
    $Res = Invoke-RestMethod -Uri "$BaseUrl/health/ready" -Method Get -UseBasicParsing
    $Details = ""
    if ($Res.checks) {
        $Details = " [DB: $($Res.checks.database), OCR: $($Res.checks.ocr_engine) (Mock=$($Res.checks.ocr_mock))]"
    }
    Write-Host "[PASS] GET  /health/ready: Status=$($Res.status)$Details" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] GET  /health/ready: Connection or request failed ($_.Exception.Message)" -ForegroundColor Red
}

# 4. HEAD /health/ready
try {
    $Res = Invoke-WebRequest -Uri "$BaseUrl/health/ready" -Method Head -UseBasicParsing
    Write-Host "[PASS] HEAD /health/ready: Status=$($Res.StatusCode)" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] HEAD /health/ready: Request failed ($_.Exception.Message)" -ForegroundColor Red
}
