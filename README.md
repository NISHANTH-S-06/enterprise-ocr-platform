# OCR Platform — Enterprise Form Fill Engine

Full async OCR pipeline: upload → extract → validate → autofill → export.

## Stack
FastAPI · SQLAlchemy (async) · PostgreSQL · Alembic · PaddleOCR · ReportLab · OpenPyXL · JWT

## Quick Start (Windows VS Code)

```powershell
# 1. Copy env
copy .env.example .env
# Edit .env — set SECRET_KEY and DATABASE_URL

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run migrations
alembic upgrade head

# 4. Start server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open: http://127.0.0.1:8000/docs

## Docker

```powershell
docker-compose up -d
```

Production deployment:

- See `docs/PRODUCTION_DEPLOYMENT_RUNBOOK.md`
- See `docs/PRODUCTION_CHECKLIST.md`
- Start from `.env.production.example`

Release candidate package:

- See `docs/RELEASE_READINESS_RC1.md`
- See `docs/CHANGELOG_RC1.md`
- See `docs/RELEASE_NOTES_RC1.md`
- See `docs/GO_LIVE_ACCEPTANCE_RC1.md`
- See `docs/CONTROLLED_PRODUCTION_PILOT.md`

## API Flow

1. `POST /api/v1/auth/login` → JWT token
2. `POST /api/v1/fill/` with `extracted_data_id` + `export_format` (pdf/excel/json)
3. `GET  /api/v1/fill/jobs/{id}/download` → file

## Run Tests

```powershell
pytest tests/ -v
```

## Final Local Run - Upload OCR Auto-Fill

Backend:

```powershell
cd enterprise-ocr-platform
$env:FLAGS_use_mkldnn="0"
$env:FLAGS_enable_pir_api="0"
$env:OMP_NUM_THREADS="1"
$env:PADDLE_CPP_LOGLEVEL="3"
.\.venv311\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```powershell
cd frontend
python -m http.server 3000 --bind 127.0.0.1
```

Open:

```text
http://127.0.0.1:3000
```

Upload API smoke test:

```powershell
$token = "<JWT_TOKEN>"
curl.exe -X POST "http://127.0.0.1:8000/api/v1/upload/" -H "Authorization: Bearer $token" -F "file=@<INVOICE_IMAGE_PATH>"
curl.exe -H "Authorization: Bearer $token" "http://127.0.0.1:8000/api/v1/upload/jobs/<JOB_ID>"
```

Scripted upload checks:

```powershell
cd enterprise-ocr-platform
.\scripts\test_upload_flow.ps1 -Token $token -ImagePath "<INVOICE_IMAGE_PATH>" -PdfPath "<INVOICE_PDF_PATH>" -LargeImagePath "<LARGE_IMAGE_PATH>"
```

Expected OCR JSON shape from both upload and job polling responses:

```json
{
  "success": true,
  "text": "",
  "raw_text_length": 0,
  "detected_line_count": 0,
  "raw_text_preview": "",
  "fields": {},
  "confidence": {},
  "warnings": []
}
```

## DB Tables
organisations · users · user_sessions · api_keys · uploaded_files · file_pages ·
ocr_jobs · job_events · extracted_data · extraction_templates · audit_logs · system_logs · form_fill_jobs
