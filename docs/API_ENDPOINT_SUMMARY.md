# API Endpoint Summary

All paths use the `/api/v1` prefix.

## Health And Authentication

- `GET /health`, `GET /health/ready`
- `POST /auth/register`, `POST /auth/login`, `GET /auth/me`

## Upload And OCR

- `POST /upload/` - PDF or image upload
- `POST /upload/batches` - batch upload
- `GET /upload/jobs/{job_id}` - OCR status/result
- `GET /upload/jobs/{job_id}/review` - review fields
- `POST /upload/jobs/{job_id}/corrections` - apply corrections
- `POST /upload/jobs/{job_id}/revalidate` - revalidate OCR
- `GET /upload/batches/{batch_id}` - batch status
- `POST|GET /upload/batches/{batch_id}/export/zip` - generate/download batch ZIP
- `GET /upload/supported` - supported upload types

## Workbench And Tabular Input

- `POST /forms/workbench/parse-tabular` - parse XLSX or CSV
- `POST /forms/workbench/auto-fill` - Sales/Purchase auto-fill
- `POST /forms/workbench/validate` - validate normalized data
- `POST /forms/workbench/save-draft` - save draft

## Training Datasets

- `POST|GET /companies/training-datasets` - create/list datasets
- `POST /companies/training-datasets/best-match` - select best dataset
- `GET|PUT /companies/training-datasets/{dataset_id}` - read/update dataset
- `POST /companies/training-datasets/{dataset_id}/deactivate`
- `POST /companies/templates/mappings/save`, `GET /companies/templates/mappings`

## ERP Masters

- `POST /companies/erp-master-mappings/import` - CSV/JSON import
- `POST|GET /companies/erp-master-mappings` - create/list mappings
- `POST /companies/erp-master-mappings/resolve` - scored resolution
- `POST /companies/erp-master-mappings/bulk` - bulk operation

## Correction Learning

- `POST /forms/workbench/corrections`
- `POST /forms/workbench/corrections/bulk`
- `POST /forms/workbench/corrections/apply`
- `GET /forms/workbench/corrections`
- `POST /forms/workbench/corrections/{correction_id}/deactivate`

## Quality Gate And Export

- `POST /forms/workbench/export-precheck`
- `POST /forms/workbench/export/json`
- `POST /forms/workbench/export/excel`
- `POST /forms/workbench/export/pdf`

## Compatibility Endpoints

- `POST /fill/`, `GET /fill/jobs/{job_id}`, `GET /fill/jobs/{job_id}/download`
- `POST /forms/export/pdf`, `POST /forms/export/excel`

Compatibility fill and legacy export endpoints remain available with export precheck enforced. New integrations should use Workbench endpoints.
