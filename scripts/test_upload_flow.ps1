param(
  [Parameter(Mandatory=$true)][string]$Token,
  [string]$ApiBase = "http://127.0.0.1:8000",
  [string]$ImagePath = "",
  [string]$PdfPath = "",
  [string]$LargeImagePath = ""
)

$ErrorActionPreference = "Stop"

function Upload-And-Poll {
  param([string]$Path, [string]$Label)
  if (-not (Test-Path -LiteralPath $Path)) {
    Write-Host "SKIP $Label - file not found: $Path"
    return
  }

  Write-Host "UPLOAD $Label -> $Path"
  $headers = @{ Authorization = "Bearer $Token" }
  $upload = Invoke-RestMethod -Method Post -Uri "$ApiBase/api/v1/upload/" -Headers $headers -Form @{ file = Get-Item -LiteralPath $Path }
  Write-Host "JOB $($upload.job_id)"

  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    $job = Invoke-RestMethod -Method Get -Uri "$ApiBase/api/v1/upload/jobs/$($upload.job_id)" -Headers $headers
    if ($job.status -in @("completed", "failed")) {
      $job | ConvertTo-Json -Depth 8
      return
    }
  }
  throw "Timed out polling $Label"
}

Upload-And-Poll -Path $ImagePath -Label "sample image"
Upload-And-Poll -Path $PdfPath -Label "sample PDF"
Upload-And-Poll -Path $LargeImagePath -Label "large image"

$bad = Join-Path $env:TEMP "ocr-invalid-upload.txt"
Set-Content -LiteralPath $bad -Value "not an image or pdf"
try {
  Write-Host "UPLOAD invalid file -> $bad"
  Invoke-RestMethod -Method Post -Uri "$ApiBase/api/v1/upload/" -Headers @{ Authorization = "Bearer $Token" } -Form @{ file = Get-Item -LiteralPath $bad } | ConvertTo-Json -Depth 8
} catch {
  if ($_.ErrorDetails.Message) {
    Write-Host $_.ErrorDetails.Message
  } else {
    Write-Host $_.Exception.Message
  }
}
