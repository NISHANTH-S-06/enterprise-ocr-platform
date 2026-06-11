$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

Write-Host "Project root: $ProjectRoot"
Write-Host "Start backend in one visible terminal:"
Write-Host "  .\scripts\start_backend.ps1"
Write-Host "Start frontend in another visible terminal:"
Write-Host "  .\scripts\start_frontend.ps1"
Write-Host "Health: http://127.0.0.1:8000/api/v1/health"
Write-Host "Frontend: http://127.0.0.1:3000"
Write-Host "No migration, cleanup, or data deletion was executed."
