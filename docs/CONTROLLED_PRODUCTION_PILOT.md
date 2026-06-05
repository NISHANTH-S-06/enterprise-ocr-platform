# Controlled Production Pilot Plan - v1.0.0-RC1

Release candidate: `v1.0.0-RC1`

Purpose: prepare and execute a controlled pilot deployment without feature development or runtime behavior changes.

## 1. Pilot Environment Audit

Reviewed:
- `docs/PRODUCTION_DEPLOYMENT_RUNBOOK.md`
- `docs/PRODUCTION_CHECKLIST.md`
- `docs/OBSERVABILITY_MONITORING_RUNBOOK.md`
- `docs/GO_LIVE_ACCEPTANCE_RC1.md`
- `.env.production.example`

### Required Infrastructure

Minimum pilot target:
- Ubuntu 22.04 VPS or AWS EC2.
- 4 vCPU minimum.
- 8 GB RAM minimum.
- 16 GB RAM preferred for PaddleOCR stability.
- 80 GB SSD minimum.
- Persistent storage for:
  - `uploads`
  - `output`
  - `templates_store`
  - database backups
- PostgreSQL through Docker Compose or managed Postgres.
- Docker and Docker Compose plugin.
- TLS/reverse proxy, such as Nginx or Caddy.
- Off-host backup destination.
- Basic monitoring through host cron, reverse proxy logs, Docker healthchecks, and admin endpoints.

### Remaining Pilot Blockers

Pilot cannot start until these are resolved:
- Target VPS/EC2 host provisioned.
- DNS and TLS configured.
- Production `.env` created from `.env.production.example`.
- Strong `SECRET_KEY` generated.
- `CORS_ALLOWED_ORIGINS` set to the pilot domain.
- Public registration policy confirmed.
- Backup destination configured and restore drill completed.
- Healthcheck alert configured.
- Disk/memory/DB/OCR failure alerts configured.
- Admin/superadmin pilot accounts created through controlled process.
- Real OCR engine verified on the host.
- Pilot document set selected for smoke tests and baseline capture.

### Minimum VPS Specs

Pilot minimum:
- 4 vCPU
- 8 GB RAM
- 80 GB SSD

Recommended pilot:
- 4-8 vCPU
- 16 GB RAM
- 160 GB SSD

Reason: PaddleOCR model loading and batch uploads are memory-sensitive. Docker runs one API worker by default to reduce OCR memory pressure.

## 2. Staging Deployment Plan

### A. Pre-Deployment

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin nginx
sudo systemctl enable --now docker
```

Prepare app:

```bash
sudo mkdir -p /opt/ocr-platform
sudo chown "$USER":"$USER" /opt/ocr-platform
git clone <repo-url> /opt/ocr-platform
cd /opt/ocr-platform
git checkout v1.0.0-RC1
cp .env.production.example .env
nano .env
```

Required `.env` checks:

```bash
grep -E "APP_ENV|DATABASE_URL|CORS_ALLOWED_ORIGINS|ALLOW_PUBLIC_REGISTRATION|AI_FALLBACK_ENABLED" .env
test "$(grep -c '^SECRET_KEY=' .env)" -eq 1
```

### B. Deployment Sequence

```bash
cd /opt/ocr-platform
docker compose build
docker compose up -d
docker compose ps
docker compose logs api --tail=150
```

The API container runs:

```bash
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

### C. Migration Sequence

Confirm database readiness:

```bash
docker compose exec db pg_isready -U ocr -d ocrdb
docker compose exec api alembic current
docker compose exec api alembic history --verbose | tail -40
```

For future migration deployments:

```bash
cd /opt/ocr-platform
git fetch --tags
git checkout <release-tag>
docker compose build api
docker compose up -d api
docker compose logs api --tail=150
docker compose exec api alembic current
```

### D. Verification Sequence

```bash
curl -fsS http://127.0.0.1:8000/api/v1/health
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
docker compose ps
docker stats --no-stream
df -h
```

Confirm logs:

```bash
docker compose logs api --since=10m | grep -i "starting\|OCR engine\|migration\|error"
```

### E. Rollback Sequence

Rollback app only:

```bash
cd /opt/ocr-platform
docker compose logs api --tail=300 > rollback-api-before.log
git checkout <known-good-tag-or-commit>
docker compose build api
docker compose up -d api
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
```

Rollback with database restore:

```bash
docker compose down
docker compose up -d db
gunzip -c backups/db/ocrdb-YYYYMMDD-HHMMSS.sql.gz | docker compose exec -T db psql -U ocr -d ocrdb
docker compose up -d
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
```

Do not restore database before preserving logs and confirming the rollback reason.

## 3. Pilot Smoke Test Plan

Use a small pilot tenant and a controlled document set. Record request time, response status, job IDs, batch IDs, and output file names.

### A. Login

Goal: confirm auth and token issuance.

```bash
curl -sS -X POST "https://<pilot-domain>/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"<pilot-user>","password":"<password>"}'
```

Pass:
- HTTP 200.
- Access token returned.

### B. Upload

Goal: confirm file validation and job creation.

```bash
TOKEN="<jwt-token>"
curl -sS -X POST "https://<pilot-domain>/api/v1/upload/" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "file=@samples/salesinvoice.pdf"
```

Pass:
- HTTP 202.
- `job_id` returned.
- status is `processing`.

### C. OCR

Goal: confirm OCR completion and field output.

```bash
curl -sS -H "Authorization: Bearer ${TOKEN}" \
  "https://<pilot-domain>/api/v1/upload/jobs/<job-id>"
```

Pass:
- status becomes `completed`.
- OCR text length is non-zero for valid sample.
- warnings reviewed.

### D. Validation

Goal: confirm validated fields and review state.

Use job polling response and admin recent jobs/audit visibility:

```bash
curl -sS -H "Authorization: Bearer ${TOKEN}" \
  "https://<pilot-domain>/api/v1/upload/jobs/<job-id>"
```

Pass:
- validation errors/warnings are present when expected.
- extraction status is `validated` or `needs_review` according to document quality.

### E. Export PDF

Goal: confirm PDF export generation and download.

```bash
curl -sS -X POST "https://<pilot-domain>/api/v1/fill/" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"extracted_data_id":"<extracted-data-id>","export_format":"pdf"}'
```

Then download using the returned fill job ID.

Pass:
- export job succeeds.
- PDF downloads and opens.

### F. Export Excel

Goal: confirm Excel export generation and download.

```bash
curl -sS -X POST "https://<pilot-domain>/api/v1/fill/" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"extracted_data_id":"<extracted-data-id>","export_format":"excel"}'
```

Pass:
- export job succeeds.
- XLSX downloads and opens.

### G. Batch Upload

Goal: confirm batch creation, child jobs, and batch status.

```bash
curl -sS -X POST "https://<pilot-domain>/api/v1/upload/batches" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "files=@samples/salesinvoice.pdf" \
  -F "files=@samples/POPRINT.pdf"
```

Poll:

```bash
curl -sS -H "Authorization: Bearer ${TOKEN}" \
  "https://<pilot-domain>/api/v1/upload/batches/<batch-id>?refresh=true"
```

Pass:
- batch ID returned.
- child jobs visible.
- completed/failed counters reconcile.

### H. Organisation Management

Goal: confirm superadmin-only organisation management.

```bash
SUPERADMIN_TOKEN="<superadmin-jwt>"
curl -sS -H "Authorization: Bearer ${SUPERADMIN_TOKEN}" \
  "https://<pilot-domain>/api/v1/admin/organisations"
```

Pass:
- superadmin can list organisations.
- admin/operator/reviewer/viewer cannot access global org management.
- secret-like settings are not returned.

### I. Tenant User Assignment

Goal: confirm controlled tenant assignment.

```bash
curl -sS -X POST "https://<pilot-domain>/api/v1/admin/organisations/<org-id>/users/assign" \
  -H "Authorization: Bearer ${SUPERADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"<user-id>","role":"operator"}'
```

Pass:
- user assigned to correct organisation.
- audit event created.
- admin can access only own organisation.
- cross-tenant access denied.

## 4. Baseline Capture Plan

Capture baseline during pilot with 5-10 representative documents.

### Metrics to Capture

OCR latency:
- Time from upload accepted to job completed.
- Capture per document type: sales invoice, purchase order, GRN, invoice variant.

Upload latency:
- HTTP request duration for single upload and batch upload.

Export latency:
- Time from fill job request to downloadable PDF/Excel.

Memory usage:

```bash
docker stats --no-stream
free -m
```

CPU usage:

```bash
docker stats --no-stream
uptime
```

DB response time:

```bash
time docker compose exec db psql -U ocr -d ocrdb -c "select 1;"
```

Gemini latency, if enabled:
- Time AI fallback starts to mapped result completion.
- Count Gemini failures/quota warnings.
- Keep `AI_FALLBACK_ENABLED=false` unless the pilot explicitly tests it.

Disk usage:

```bash
df -h
docker system df
```

Recommended baseline table:

| Metric | Target baseline | Captured value | Notes |
| --- | --- | --- | --- |
| Single upload p95 | under 10s | | |
| OCR average latency | measured during pilot | | |
| OCR p95 latency | measured during pilot | | |
| PDF export p95 | under 10s | | |
| Excel export p95 | under 10s | | |
| Batch upload p95 | measured during pilot | | |
| API memory steady state | under 75% host RAM | | |
| API memory during OCR | under 85% host RAM | | |
| DB response | under 500ms local | | |
| Disk usage after pilot | under 70% | | |

## 5. Pilot Signoff Checklist

Engineering:
- [ ] `v1.0.0-RC1` tag/commit confirmed.
- [ ] Compile validation passed.
- [ ] Test suite passed.
- [ ] E2E result reviewed.
- [ ] No feature changes included in pilot deploy.

Operations:
- [ ] Host provisioned.
- [ ] Docker Compose deployment successful.
- [ ] Health/readiness endpoints pass.
- [ ] Rollback command path tested or dry-run reviewed.
- [ ] Logs accessible.

Security:
- [ ] Production secrets installed.
- [ ] CORS locked to pilot domain.
- [ ] Public registration disabled unless approved.
- [ ] Superadmin access restricted.
- [ ] Tenant isolation smoke test passed.

OCR:
- [ ] OCR engine loads on host.
- [ ] Single-document OCR smoke test passed.
- [ ] Batch OCR smoke test passed.
- [ ] OCR latency baseline captured.
- [ ] Memory baseline captured.

Backups:
- [ ] Database backup created.
- [ ] File volume backups created.
- [ ] Backup integrity verified.
- [ ] Restore drill completed or scheduled before go-live expansion.

Monitoring:
- [ ] Healthcheck alert configured.
- [ ] DB alert configured.
- [ ] Disk/memory alerts configured.
- [ ] OCR/batch failure alert configured.
- [ ] Backup freshness alert configured.

Deployment:
- [ ] TLS/reverse proxy configured.
- [ ] Persistent volumes verified.
- [ ] Database migration head confirmed.
- [ ] Pilot smoke test plan completed.

## 6. Pilot Go/No-Go Recommendation

Current recommendation: conditional GO for controlled pilot.

Conditions:
- Deploy only to a controlled VPS/EC2 environment with at least 4 vCPU, 8 GB RAM, and persistent storage.
- Use 16 GB RAM if available.
- Keep public registration disabled.
- Keep Gemini disabled unless explicitly testing fallback behavior.
- Limit pilot users and document volume.
- Complete backup restore verification before expanding beyond pilot.
- Capture OCR latency and memory baselines before customer-facing rollout.

No-Go triggers:
- Real OCR engine fails to load on host.
- Database readiness fails.
- Tenant isolation smoke test fails.
- Valid upload/OCR/export smoke test fails.
- Disk/memory alerts cannot be configured.
- Backup cannot be created and verified.

