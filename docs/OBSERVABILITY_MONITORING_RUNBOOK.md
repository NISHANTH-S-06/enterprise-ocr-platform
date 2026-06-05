# OCR Enterprise Platform Observability and Monitoring Runbook

This runbook defines the production observability baseline for the OCR Enterprise Platform without requiring paid monitoring tools or heavy external services.

## 1. Current Observability Audit

Existing strengths:
- FastAPI liveness endpoint: `/api/v1/health`.
- FastAPI readiness endpoint: `/api/v1/health/ready`.
- Readiness checks database connectivity and OCR engine state.
- Dockerfile and Docker Compose use readiness healthchecks.
- Application logs go to stdout through Loguru, so Docker captures them.
- Audit logs exist for auth, upload, OCR, validation, export, organisation, and user-management events.
- Admin operational endpoints already expose safe summaries:
  - `/api/v1/admin/summary`
  - `/api/v1/admin/storage`
  - `/api/v1/admin/audit-logs`
  - `/api/v1/admin/uploads/recent`
  - `/api/v1/admin/jobs/recent`
  - `/api/v1/admin/batches/recent`
- Upload job status endpoint gives job-level visibility.
- Batch status endpoint gives batch-level visibility.
- Admin payload helpers redact secret-like metadata and avoid raw OCR content.

Missing or externalized:
- No Prometheus metrics endpoint.
- No built-in alert scheduler.
- No log aggregation service configured.
- No automatic backup freshness monitor.
- No host-level disk/memory alerting.

Minimal production approach:
- Use existing health endpoints, Docker logs, admin summary endpoints, and host cron/systemd checks.
- Add external monitoring later only after production baseline is stable.

## 2. Logging Guidance

Safe log fields:
- `request_id`
- `job_id`
- `batch_id`
- `user_id`
- `org_id`
- `stage`
- `status`
- `error_category`
- `duration_ms`
- `file_size`
- `mime_type`
- counts such as `field_count`, `warning_count`, `page_count`

Never log:
- raw OCR text
- full extracted text
- file contents
- Authorization headers
- API keys
- passwords
- tokens
- secrets
- full Gemini prompts
- full Gemini raw responses if they can contain document text
- uploaded file paths if they expose private host layout

Docker log commands:

```bash
docker compose logs api --tail=200
docker compose logs api --since=10m
docker compose logs db --tail=200
docker compose logs api | grep -i "error\|failed\|exception"
```

Recommended log review filters:

```bash
docker compose logs api --since=10m | grep -i "ocr.failure\|OCR failed"
docker compose logs api --since=10m | grep -i "batch.upload\|batch.export"
docker compose logs api --since=10m | grep -i "ai.fallback\|gemini"
docker compose logs api --since=10m | grep -i "system.error\|Unhandled"
```

## 3. Health and Metrics Baseline

Health endpoints:

```bash
curl -fsS http://127.0.0.1:8000/api/v1/health
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
```

Admin metrics endpoints:

```bash
TOKEN="<admin-jwt>"
curl -fsS -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:8000/api/v1/admin/summary
curl -fsS -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:8000/api/v1/admin/storage
curl -fsS -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:8000/api/v1/admin/jobs/recent?limit=20
curl -fsS -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:8000/api/v1/admin/batches/recent?limit=20
curl -fsS -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:8000/api/v1/admin/audit-logs?limit=20
```

Monitor:
- API readiness failures.
- Database connectivity.
- OCR job failures.
- Batch failures.
- Export failures.
- Disk usage.
- Backup freshness.
- Gemini fallback failures or quota errors.
- HTTP 5xx rate from reverse proxy logs.
- Upload latency from reverse proxy or API logs.
- OCR processing latency from job logs and persisted job fields.

Host-level commands:

```bash
docker compose ps
docker stats --no-stream
df -h
free -m
uptime
docker system df
```

Database checks:

```bash
docker compose exec db pg_isready -U ocr -d ocrdb
docker compose exec db psql -U ocr -d ocrdb -c "select now();"
```

Backup freshness check:

```bash
find backups/db -name "*.sql.gz" -mtime -1 -print | head -1
find backups/files -name "*.tar.gz" -mtime -1 -print | head -1
```

## 4. Initial Alert Thresholds

Use these thresholds as the first production baseline:

| Signal | Initial threshold | Severity |
| --- | --- | --- |
| API readiness | fails 3 consecutive checks | critical |
| DB unavailable | more than 2 minutes | critical |
| Disk usage | greater than 80% | warning |
| Disk usage | greater than 90% | critical |
| Memory usage | greater than 85% for 10 minutes | warning |
| OCR failure rate | greater than 10% over 15 minutes | warning |
| Batch failure rate | greater than 10% over 15 minutes | warning |
| HTTP 5xx rate | greater than 5% over 10 minutes | critical |
| Backup freshness | newest DB backup older than 24 hours | critical |
| Gemini quota/API failures | more than 5 in 10 minutes | warning |
| Average OCR time | greater than 2x measured baseline for 15 minutes | warning |
| Upload latency | p95 greater than 10 seconds for 10 minutes | warning |
| Export failures | more than 3 in 10 minutes | warning |
| Tenant access concern | any credible cross-tenant report | critical |

Baseline OCR time should be measured after production deployment using representative invoice, purchase order, and GRN documents.

## 5. Cron-Friendly Checks

Simple readiness check:

```bash
#!/usr/bin/env bash
set -euo pipefail
curl -fsS http://127.0.0.1:8000/api/v1/health/ready >/dev/null
```

Disk check:

```bash
#!/usr/bin/env bash
set -euo pipefail
USAGE=$(df / | awk 'NR==2 {gsub("%","",$5); print $5}')
test "$USAGE" -lt 80
```

Backup freshness check:

```bash
#!/usr/bin/env bash
set -euo pipefail
find /opt/ocr-platform/backups/db -name "*.sql.gz" -mtime -1 | grep -q .
```

These scripts can run from cron and notify by email, Slack webhook, or any local alerting mechanism available to the deployment.

## 6. Incident Runbook

### 1. API Down

Symptoms:
- Healthcheck fails.
- Reverse proxy returns 502/503.
- `docker compose ps` shows API unhealthy or restarting.

Diagnosis:

```bash
docker compose ps
docker compose logs api --tail=300
curl -i http://127.0.0.1:8000/api/v1/health
docker stats --no-stream
```

Likely causes:
- Bad environment variable.
- Migration failure.
- OCR preload failure in production.
- Memory exhaustion.
- Port conflict or container crash.

Safe recovery:
- Check logs before restarting.
- Restart API: `docker compose restart api`.
- If migration failed, inspect `alembic current` and database connectivity.
- If memory is exhausted, stop traffic and resize host before repeated restarts.

Escalation:
- Escalate immediately if API cannot start after one clean restart and logs show migration, database, or OCR initialization failures.

### 2. DB Down

Symptoms:
- Readiness `database` check fails.
- Login/upload/admin endpoints fail.
- DB container unhealthy.

Diagnosis:

```bash
docker compose ps
docker compose logs db --tail=300
docker compose exec db pg_isready -U ocr -d ocrdb
df -h
```

Likely causes:
- Disk full.
- Postgres crash.
- Corrupt volume.
- Bad database credentials.

Safe recovery:
- Restart DB: `docker compose restart db`.
- Free disk only from known safe files such as old Docker layers or old off-host-copied backups.
- Restore from backup if the data volume is corrupt.

Escalation:
- Escalate if Postgres reports corruption, repeated crash loops, or missing data.

### 3. OCR Slow or Stuck

Symptoms:
- Upload accepted but jobs remain processing.
- OCR latency exceeds baseline.
- CPU/RAM high.

Diagnosis:

```bash
docker compose logs api --since=15m | grep -i "OCR background\|Background OCR\|ocr"
docker stats --no-stream
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
```

Likely causes:
- Host underprovisioned.
- Large/complex PDFs.
- PaddleOCR model contention.
- Disk I/O pressure.

Safe recovery:
- Restart API only if jobs are clearly stuck and no active recovery is happening.
- Reduce concurrent traffic.
- Scale host RAM/CPU.
- Keep Docker worker count at 1 unless model memory has been profiled.

Escalation:
- Escalate if stuck jobs accumulate or customer documents are time-sensitive.

### 4. PaddleOCR Memory Issue

Symptoms:
- Container exits with OOM.
- API restarts during OCR.
- Host memory near full.

Diagnosis:

```bash
docker stats --no-stream
dmesg | grep -i "killed process\|oom" | tail -20
docker compose logs api --tail=300 | grep -i "paddle\|memory\|killed"
```

Likely causes:
- Insufficient RAM.
- Too many workers.
- Large batch traffic.

Safe recovery:
- Keep `--workers 1`.
- Restart API after memory returns.
- Resize host to 16 GB RAM if using PaddleOCR heavily.

Escalation:
- Escalate if OOM repeats after worker count and traffic are controlled.

### 5. Disk Full

Symptoms:
- Upload/export fails.
- DB writes fail.
- Healthcheck degraded or API errors.

Diagnosis:

```bash
df -h
du -sh uploads output templates_store backups 2>/dev/null
docker system df
docker compose logs api --tail=200
docker compose logs db --tail=200
```

Likely causes:
- Upload growth.
- Export ZIP growth.
- Backups retained locally too long.
- Docker image/layer buildup.

Safe recovery:
- Copy backups off-host before deleting local copies.
- Run `docker system prune` after confirming no needed images.
- Increase disk size.
- Do not delete customer uploads without retention approval.

Escalation:
- Escalate immediately if database volume is out of space.

### 6. Upload Failing

Symptoms:
- Upload endpoint returns 4xx/5xx.
- Users report files rejected.

Diagnosis:

```bash
docker compose logs api --since=15m | grep -i "upload\|413\|415\|quarantine\|file"
df -h
curl -i http://127.0.0.1:8000/api/v1/health/ready
```

Likely causes:
- Unsupported MIME/extension.
- File too large.
- Tenant batch limits.
- Disk full.
- Storage volume not writable.

Safe recovery:
- Verify file type and size.
- Verify storage volume.
- Confirm tenant limits.
- Restart API only if failures are systemic and logs indicate internal errors.

Escalation:
- Escalate if valid uploads fail across tenants.

### 7. Export Failing

Symptoms:
- PDF/Excel/ZIP export returns failure.
- Output file missing.

Diagnosis:

```bash
docker compose logs api --since=15m | grep -i "export\|fill\|output\|reportlab\|openpyxl"
df -h
```

Likely causes:
- Output volume not writable.
- Disk full.
- Source extraction incomplete.
- Export library exception.

Safe recovery:
- Confirm extraction status first.
- Confirm output volume and disk.
- Restart API if library error appears transient.

Escalation:
- Escalate if exports fail for completed and validated documents.

### 8. Gemini Quota or API Failing

Symptoms:
- AI fallback audit logs show quota/API failures.
- Gemini enriched fields stop appearing.

Diagnosis:

```bash
docker compose logs api --since=15m | grep -i "gemini\|ai.fallback\|quota\|api_exception"
docker compose exec api env | grep -E "AI_FALLBACK|GEMINI"
```

Likely causes:
- Missing/invalid API key.
- Quota exhausted.
- Gemini API outage.
- Tenant disabled AI fallback.

Safe recovery:
- Disable fallback with `AI_FALLBACK_ENABLED=false` if quota failures are noisy.
- Core OCR/regex extraction continues without Gemini.
- Rotate API key only through secret management.

Escalation:
- Escalate if business workflow depends on AI recovery.

### 9. Tenant Data Access Concern

Symptoms:
- User reports seeing another tenant's data.
- Audit logs show suspicious cross-org access.

Diagnosis:

```bash
TOKEN="<superadmin-jwt>"
curl -fsS -H "Authorization: Bearer ${TOKEN}" "http://127.0.0.1:8000/api/v1/admin/audit-logs?limit=100"
docker compose logs api --since=1h | grep -i "403\|404\|auth.unauthorized\|organisation\|tenant"
```

Likely causes:
- Misassigned user org.
- Incorrect role.
- Token/session from wrong account.
- Potential tenant isolation defect.

Safe recovery:
- Disable affected user account or move role/org assignment through admin API.
- Preserve logs and database snapshot.
- Do not bulk-edit tenant data during investigation.

Escalation:
- Treat as critical. Escalate to engineering/security lead immediately.

### 10. Backup Restore Needed

Symptoms:
- Data corruption.
- Accidental deletion.
- Failed migration with data loss.
- Disaster recovery event.

Diagnosis:

```bash
ls -lh backups/db backups/files
gunzip -t backups/db/<backup>.sql.gz
tar tzf backups/files/uploads-<backup>.tar.gz >/dev/null
tar tzf backups/files/output-<backup>.tar.gz >/dev/null
tar tzf backups/files/templates-<backup>.tar.gz >/dev/null
```

Likely causes:
- Operator error.
- Disk failure.
- Bad deploy.
- Database corruption.

Safe recovery:
- Stop app traffic.
- Snapshot current broken state before restore if possible.
- Restore database and file volumes using the deployment runbook.
- Verify `/api/v1/health/ready`, login, upload, OCR, and export.

Escalation:
- Escalate before restore if data loss window or tenant impact is unclear.

