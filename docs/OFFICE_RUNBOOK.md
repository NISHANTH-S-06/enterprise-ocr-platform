# OCR Enterprise Platform Office Runbook

Welcome to the OCR Enterprise Platform operator and administrator runbook. This guide provides instructions for deploying, operating, backing up, and troubleshooting the platform in an office or demo environment.

---

## 1. System Requirements & Environment Checklist

### System Prerequisites
* **Operating System**: Windows 10/11 (CUDA-capable GPU recommended for local OCR acceleration).
* **Python Runtime**: Python 3.11.x installed.
* **Web Browser**: Microsoft Edge, Google Chrome, or Mozilla Firefox (modern layout support required).
* **Project Directory**: `D:\OCR_MASTER\ocr-platform-complete`

### Environment Configuration
The application parameters are configured via the `.env` file in the project root. Ensure the settings are populated correctly:
* `APP_NAME` is set to `"OCR Platform"`.
* `APP_ENV` is set to `"development"`.
* `UPLOAD_DIR` defaults to `"uploads"`.
* `OUTPUT_DIR` defaults to `"output"`.
* `TEMPLATES_DIR` defaults to `"templates_store"`.

---

## 2. Server Startup & Shutdown Operations

PowerShell scripts are provided in the `scripts/` directory to automate local services.

### Starting the Servers
1. Open a Windows PowerShell window.
2. Navigate to the project root:
   ```powershell
   cd D:\OCR_MASTER\ocr-platform-complete
   ```
3. Start the Backend API (runs on port `8000`):
   ```powershell
   powershell.exe -File scripts\start_backend.ps1
   ```
4. Start the Frontend HTTP Server (runs on port `3000`):
   ```powershell
   powershell.exe -File scripts\start_frontend.ps1
   ```

### Verifying Service Health
To check if both servers are active and reachable, run:
```powershell
powershell.exe -File scripts\check_health.ps1
```
This script queries the following endpoints:
* **Liveness Probe**: `GET http://127.0.0.1:8000/api/v1/health`
* **Readiness Probe**: `GET http://127.0.0.1:8000/api/v1/health/ready`

A successful return displays `Status=ok` with database and active PaddleOCR information.

### Stopping the Servers
To cleanly stop all local server processes and release ports 8000 and 3000, run:
```powershell
powershell.exe -File scripts\stop_local_servers.ps1
```

---

## 3. Operator Manual & Office Workflow

### Accessing the Web App
Open your web browser and navigate to:
```
http://127.0.0.1:3000/index.html
```

### Authentication Flow
1. **First-time Login**: If running in a fresh workspace, click "Register" and create a user profile.
2. **Subsequent Access**: Log in using your registered credentials.
3. *Note*: If registration fails with an "Already Exists" message, proceed directly to Login.

### Single Document Extraction Workflow
1. Navigate to the **Review Workbench** tab.
2. Drag and drop an invoice PDF (e.g., `salesinvoice.pdf`) into the dropzone or browse your local file system.
3. Click **Upload**. The background processor executes OCR text extraction and normalization.
4. **Auto Fill**: The system identifies and matches invoice headers, totals, dates, and line item tables automatically.
5. **Clear AI-Filled Values**: To wipe out automated mappings and input metadata manually, click **Clear AI Filled Values** at the bottom of the review workspace.
6. **Required Fields Audit**: Fill in any missing mandatory inputs (e.g., invoice number, date, vendor name, grand total).
7. **Submit for Verification**: When ready, click **Approve / Verify** to finalize the document.

### Batch Upload Workflow
1. Navigate to the **Batch Upload** tab.
2. Drop multiple PDF documents simultaneously.
3. The upload panel displays progress bars, batch size, accepted/rejected counts, and errors for invalid document formats.
4. Monitor live status counters (Queued, Processing, Completed, Needs Review, Failed).

### Document Archive and Search
1. Navigate to the **Document Archive** tab.
2. Use the search bar to locate processed invoices by typing names, amounts, or invoice numbers.
3. Apply filters:
   * **Date Range** (Start/End date).
   * **Amount Range** (Minimum/Maximum grand total).
   * **Document Type** (Invoice, Purchase Order, Goods Receipt Note).
   * **Duplicate Status** (Not Duplicate, Exact Duplicate, Warning).
4. Click **Search** to execute filtering.

### Reopening a Processed Job
1. From the search results grid in the **Document Archive**, click **Reopen** next to a completed or flagged document.
2. This redirects the operator interface back to the **Review Workbench** with the original document, text overlays, and AI-extracted values pre-populated for further modification.

### Exporting Extracted Data
1. Select one or more documents using the checkboxes in the **Document Archive** grid.
2. Click **Export Selected**.
3. Choose your output format (**PDF**, **Excel**, **CSV**, or **JSON**) to download the structured spreadsheet or documents instantly.

---

## 4. Warnings and Business Rule Interpretations

During human review, safety indicators will trigger warnings:
* **Duplicate Detection Warning**: Tells you if the uploaded invoice has the same filename, checksum, invoice number, or total as a previously processed document. This protects against double payment.
* **ERP Intelligence Validation**: Flags if the supplier name does not match any official vendor in the ERP master registry, or if tax calculations on items fail to match the total.

---

## 5. Local Backup & Restore Guide

To protect your transactional documents and database from local hardware failures, schedule regular backups:

### Directory Backup Checklist
Ensure the following directories are backed up to a secure local drive:
1. **SQLite Database File**: `D:\OCR_MASTER\ocr-platform-complete\ocr_enterprise.db`
2. **Uploaded Document Store**: `D:\OCR_MASTER\ocr-platform-complete\uploads\`
3. **Structured Output Files**: `D:\OCR_MASTER\ocr-platform-complete\output\`
4. **Custom Templates Store**: `D:\OCR_MASTER\ocr-platform-complete\templates_store\`

### Simple Local Backup Script
Copy this command into PowerShell to make a quick dated backup copy:
```powershell
$BackupDir = "D:\OCR_MASTER_BACKUPS\$(Get-Date -Format 'yyyyMMdd_HHmmss')"
New-Item -ItemType Directory -Force $BackupDir | Out-Null
Copy-Item "ocr_enterprise.db" (Join-Path $BackupDir "ocr_enterprise.db")
Copy-Item "uploads" (Join-Path $BackupDir "uploads") -Recurse -Container
Copy-Item "output" (Join-Path $BackupDir "output") -Recurse -Container
Write-Host "Backup completed successfully to: $BackupDir" -ForegroundColor Green
```

### Restoration Steps
1. Execute `scripts\stop_local_servers.ps1` to release locks.
2. Replace `ocr_enterprise.db` with the backed-up copy.
3. Restore `uploads/`, `output/`, and `templates_store/` folders.
4. Restart the servers using `scripts\start_backend.ps1`.

---

## 6. Troubleshooting Guide

| Issue | Cause | Solution |
| ----- | ----- | -------- |
| **Port 8000 Already in Use** | Another process is holding the API port. | Run `scripts\stop_local_servers.ps1` to clear locks, or run `Get-NetTCPConnection -LocalPort 8000` to find and stop the PID. |
| **Port 3000 Already in Use** | Another process is holding the frontend port. | Run `scripts\stop_local_servers.ps1` to stop the HTTP server. |
| **Register returns 409** | The operator credentials already exist in the database. | Click **Login** instead of Register and log in directly. |
| **Idempotency Hit Logs** | The system blocked an upload of the exact same document checksum. | Normal behavior. The server returns the cached result without duplicate processing. |
| **PaddleOCR Cache Warnings** | PyTorch/oneDNN flags or Paddle cache loading. | Normal during cold startups. The message `Model files already exist. Using cached files` can be safely ignored. |
| **Password Protected PDF** | PyMuPDF blocks parsing of encrypted files. | The system rejects the file automatically. Request an unencrypted file from the issuer. |
| **Large File Rejected** | File size exceeds `MAX_FILE_SIZE_MB` limit. | Split the PDF or compress it below the configured limit in `.env`. |
| **Export Button Blocked** | Extraction warnings or validation blocker flags are active. | Resolve the validation warnings in the workbench first, then click **Approve** to unlock exports. |

---

## 7. Administrator Verification Guide

To perform full developer or administrator tests before deploying updates, run:
```powershell
powershell.exe -File scripts\run_verification.ps1
```
This executes the following sequence:
1. Resets the environment to a clean temp directory at `D:\OCR_MASTER\pytest_tmp`.
2. Checks compile status of all scripts.
3. Runs the unit and integration pytest suite.
4. Executes JS syntax validation checks.
5. Runs the E2E processing runner.
6. Asserts accuracy benchmarks on candidate datasets.

---

## 8. Final Demo / Manual QA Checklist

Use this checklist to verify that the OCR system is 100% operational for team presentation:

- [ ] **Backend Starts**: `scripts\start_backend.ps1` launches API server cleanly on port 8000.
- [ ] **Frontend Starts**: `scripts\start_frontend.ps1` starts web interface on port 3000.
- [ ] **Liveness OK**: `check_health.ps1` reports liveness GET/HEAD as operational (200).
- [ ] **Readiness OK**: `check_health.ps1` reports DB and PaddleOCR readiness as active.
- [ ] **Login works**: User registration and login succeed in the web UI.
- [ ] **Single Upload**: Drag-dropping an invoice uploads and processes successfully.
- [ ] **Auto-Fill Check**: Supplier, totals, dates, and line items load on the review grid.
- [ ] **Clear AI Fill**: Clicking "Clear AI Filled Values" resets the UI fields to blank.
- [ ] **Warning checks**: Duplicate warning indicator lights up when uploading a document twice.
- [ ] **Archive Search**: Archive filter options correctly find records by date or amount.
- [ ] **Reopen works**: Clicking "Reopen" restores editing fields on the review tab.
- [ ] **PDF Export**: Export to PDF downloads a valid file.
- [ ] **Excel Export**: Export to XLSX downloads a valid spreadsheet.
- [ ] **Benchmark Check**: Running the benchmark yields 100% accuracy and 0 false positives.
