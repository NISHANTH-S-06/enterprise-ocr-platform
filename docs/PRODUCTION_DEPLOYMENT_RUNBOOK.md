# OCR Enterprise Platform Production Deployment Runbook

This runbook covers production deployment, operations, backup, and recovery for the FastAPI OCR Enterprise Platform.

For monitoring, alert thresholds, and incident response, see `docs/OBSERVABILITY_MONITORING_RUNBOOK.md`.

## 1. Deployment Target Audit

### VPS - Ubuntu 22.04

Recommended shape:
- RAM: 8 GB minimum, 16 GB preferred for PaddleOCR and batch uploads.
- CPU: 4 vCPU minimum, 6-8 vCPU preferred for concurrent OCR.
- Storage: 80 GB SSD minimum, 160 GB+ preferred; mount persistent storage for `uploads`, `output`, `templates_store`, and database backups.

OCR suitability:
- Strong fit if using Docker Compose or systemd with persistent disk.
- Best control over PaddleOCR dependencies, memory, and storage.

Cost:
- Usually predictable and lower than PaaS for OCR-heavy workloads.

Risks:
- You own patching, backups, firewall, SSL, monitoring, and incident response.

### Docker Compose

Recommended shape:
- RAM: same as VPS, 8 GB minimum and 16 GB preferred.
- CPU: 4 vCPU minimum.
- Storage: persistent Docker volumes or mounted host directories.

OCR suitability:
- Strong fit when the host has enough RAM.
- Current Docker default uses one worker to avoid loading PaddleOCR models in multiple workers.

Cost:
- Depends on host. Low operational overhead once configured.

Risks:
- Must verify migrations, healthchecks, volume backups, and Docker build context hygiene.

### Railway

Recommended shape:
- RAM: highest available plan that can sustain PaddleOCR model load.
- CPU: dedicated or high-performance tier preferred.
- Storage: use managed Postgres plus external object storage; do not rely on ephemeral filesystem.

OCR suitability:
- Risky for PaddleOCR due cold starts, memory footprint, and filesystem persistence.

Cost:
- Can become expensive for OCR workloads.

Risks:
- Ephemeral storage, memory limits, slow startup, and background OCR duration.

### Render

Recommended shape:
- RAM: 8 GB+ instance if available.
- CPU: high CPU instance preferred.
- Storage: persistent disk for `uploads`, `output`, and `templates_store`; managed Postgres.

OCR suitability:
- Possible on paid instances, but validate PaddleOCR startup time and memory.

Cost:
- Higher than a comparable VPS for OCR-heavy usage.

Risks:
- Cold starts, memory pressure, persistent disk configuration, and long-running OCR jobs.

### Azure VM

Recommended shape:
- RAM: 16 GB preferred.
- CPU: 4-8 vCPU.
- Storage: Premium SSD; separate managed disk for uploads/output/backups.

OCR suitability:
- Strong fit, similar to VPS, with better enterprise networking and backup options.

Cost:
- More expensive than basic VPS, but mature for production operations.

Risks:
- Requires Azure operational knowledge and cost controls.

### AWS EC2

Recommended shape:
- RAM: 16 GB preferred.
- CPU: 4-8 vCPU, e.g. compute or general purpose instances.
- Storage: gp3 EBS volume for app files, RDS Postgres preferred for database.

OCR suitability:
- Strong fit. Best long-term option if using RDS, S3, CloudWatch, and snapshots.

Cost:
- Higher than a simple VPS unless rightsized carefully.

Risks:
- More moving parts; cost can grow without budgets and lifecycle policies.

### Primary Recommendation

Primary target: Ubuntu 22.04 VPS or AWS EC2 with Docker Compose.

Reason:
- PaddleOCR needs predictable CPU, RAM, and persistent local or mounted storage.
- Docker Compose matches the existing deployment shape.
- Start with one API worker. Scale vertically first, then split OCR processing into workers in a later phase if needed.

Use Railway/Render only after proving model startup, memory use, persistent storage, and healthcheck behavior on the chosen paid tier.

## 2. Environment Template

Required production variables:

```env
APP_ENV=production
SECRET_KEY=<64-character-random-secret>
DATABASE_URL=postgresql+asyncpg://ocr:<strong-password>@db:5432/ocrdb
CORS_ALLOWED_ORIGINS=https://ocr.example.com
ALLOW_PUBLIC_REGISTRATION=false
```

Gemini variables:

```env
AI_FALLBACK_ENABLED=false
GEMINI_API_KEY=
AI_FALLBACK_MODEL=gemini-1.5-flash
AI_FALLBACK_TIMEOUT_SECONDS=10
AI_FALLBACK_MIN_CONFIDENCE=0.75
AI_FALLBACK_REGEX_PROTECT_CONFIDENCE=0.90
```

OCR variables:

```env
FLAGS_use_mkldnn=0
FLAGS_enable_pir_api=0
OMP_NUM_THREADS=1
PADDLE_CPP_LOGLEVEL=3
OCR_TIMEOUT_SECONDS=60
```

Storage variables:

```env
UPLOAD_DIR=uploads
OUTPUT_DIR=output
TEMPLATES_DIR=templates_store
MAX_FILE_SIZE_MB=50
MAX_PDF_PAGES=2
```

Notes:
- `SECRET_KEY` is required and must not be a placeholder or short value.
- `DATABASE_URL` is required and should point to Postgres.
- `GEMINI_API_KEY` is optional unless `AI_FALLBACK_ENABLED=true`.
- `CORS_ALLOWED_ORIGINS` must be explicit in production. Do not use `*`.
- `ALLOW_PUBLIC_REGISTRATION=false` is recommended for SaaS production.

## 3. Deployment Runbook

### A. First Deployment

Provision host:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

Prepare app:

```bash
git clone <repo-url> /opt/ocr-platform
cd /opt/ocr-platform
cp .env.production.example .env
nano .env
```

Start:

```bash
docker compose build
docker compose up -d
docker compose ps
```

Verify migrations and readiness:

```bash
docker compose logs api --tail=100
curl -fsS http://127.0.0.1:8000/api/v1/health
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
```

### B. Migration Deployment

Pull and deploy:

```bash
cd /opt/ocr-platform
git pull
docker compose build api
docker compose up -d api
docker compose logs api --tail=100
```

The API container runs `alembic upgrade head` before app startup.

Manual migration check:

```bash
docker compose exec api alembic current
docker compose exec api alembic history --verbose
```

### C. Rollback Deployment

Rollback app image/code:

```bash
cd /opt/ocr-platform
git checkout <known-good-tag-or-commit>
docker compose build api
docker compose up -d api
docker compose logs api --tail=100
```

Database rollback:
- Prefer restore from backup for destructive schema/data problems.
- Do not run manual downgrade in production unless the downgrade path has been tested.

Restore command example:

```bash
gunzip -c backups/db/ocrdb-YYYYMMDD-HHMMSS.sql.gz | docker compose exec -T db psql -U ocr -d ocrdb
```

### D. Restart Procedures

Restart API only:

```bash
docker compose restart api
docker compose logs api --tail=100
```

Restart full stack:

```bash
docker compose restart
docker compose ps
```

### E. Health Verification

```bash
curl -i http://127.0.0.1:8000/api/v1/health
curl -i http://127.0.0.1:8000/api/v1/health/ready
docker compose ps
docker compose logs api --tail=100
docker compose logs db --tail=100
```

Expected:
- `/health` returns `status=ok`.
- `/health/ready` returns `status=ok` or `degraded` if OCR is not fully ready.
- Database check should be `ok`.

### F. OCR Verification

Check readiness:

```bash
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
```

Check logs:

```bash
docker compose logs api | grep -i "OCR engine"
docker compose logs api | grep -i "PaddleOCR"
```

Upload smoke test:

```bash
TOKEN="<jwt-token>"
curl -X POST "http://127.0.0.1:8000/api/v1/upload/" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "file=@sample.pdf"
```

Poll job:

```bash
curl -H "Authorization: Bearer ${TOKEN}" \
  "http://127.0.0.1:8000/api/v1/upload/jobs/<job-id>"
```

### G. Backup Verification

Create backup:

```bash
mkdir -p backups/db backups/files
docker compose exec -T db pg_dump -U ocr -d ocrdb | gzip > backups/db/ocrdb-$(date +%Y%m%d-%H%M%S).sql.gz
docker run --rm -v ocr-platform-complete_uploads_data:/data -v "$PWD/backups/files:/backup" alpine tar czf /backup/uploads-$(date +%Y%m%d-%H%M%S).tar.gz -C /data .
docker run --rm -v ocr-platform-complete_output_data:/data -v "$PWD/backups/files:/backup" alpine tar czf /backup/output-$(date +%Y%m%d-%H%M%S).tar.gz -C /data .
docker run --rm -v ocr-platform-complete_templates_data:/data -v "$PWD/backups/files:/backup" alpine tar czf /backup/templates-$(date +%Y%m%d-%H%M%S).tar.gz -C /data .
```

Verify backup files:

```bash
ls -lh backups/db backups/files
gunzip -t backups/db/*.sql.gz
tar tzf backups/files/uploads-*.tar.gz >/dev/null
tar tzf backups/files/output-*.tar.gz >/dev/null
tar tzf backups/files/templates-*.tar.gz >/dev/null
```

## 4. Backup Strategy

Database:
- Daily full `pg_dump`.
- Retain 14 daily backups and 8 weekly backups.
- Store at least one copy off-host.
- Test restore monthly.

Files:
- Back up `uploads`, `output`, and `templates_store` daily.
- Retain according to compliance and customer data policy.
- Prefer object storage or host snapshots for larger deployments.

Disaster recovery:

```bash
docker compose down
docker volume rm ocr-platform-complete_postgres_data
docker compose up -d db
gunzip -c backups/db/ocrdb-YYYYMMDD-HHMMSS.sql.gz | docker compose exec -T db psql -U ocr -d ocrdb
docker run --rm -v ocr-platform-complete_uploads_data:/data -v "$PWD/backups/files:/backup" alpine sh -c "cd /data && tar xzf /backup/uploads-YYYYMMDD-HHMMSS.tar.gz"
docker run --rm -v ocr-platform-complete_output_data:/data -v "$PWD/backups/files:/backup" alpine sh -c "cd /data && tar xzf /backup/output-YYYYMMDD-HHMMSS.tar.gz"
docker run --rm -v ocr-platform-complete_templates_data:/data -v "$PWD/backups/files:/backup" alpine sh -c "cd /data && tar xzf /backup/templates-YYYYMMDD-HHMMSS.tar.gz"
docker compose up -d
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
```

## 5. Operations Runbook

### OCR Stops Working

Diagnosis:

```bash
docker compose logs api --tail=300 | grep -i "ocr\|paddle\|tesseract"
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
docker stats
```

Recovery:
- Restart API: `docker compose restart api`.
- Confirm OCR flags are set.
- Confirm memory is not exhausted.
- If production reports mock OCR or OCR preload failure, redeploy on a larger host or repair PaddleOCR dependencies.

### Gemini Fails

Diagnosis:

```bash
docker compose logs api --tail=300 | grep -i "gemini\|ai.fallback"
docker compose exec api env | grep -E "AI_FALLBACK|GEMINI"
```

Recovery:
- Confirm `AI_FALLBACK_ENABLED=true` only when a valid `GEMINI_API_KEY` is configured.
- If Gemini is unavailable, set `AI_FALLBACK_ENABLED=false` and restart API.
- Regex/OCR pipeline continues without Gemini.

### DB Unavailable

Diagnosis:

```bash
docker compose ps
docker compose logs db --tail=200
docker compose exec db pg_isready -U ocr -d ocrdb
curl -i http://127.0.0.1:8000/api/v1/health/ready
```

Recovery:
- Restart DB: `docker compose restart db`.
- Check disk space.
- Restore from backup if data volume is corrupt.

### Disk Full

Diagnosis:

```bash
df -h
docker system df
du -sh uploads output templates_store backups 2>/dev/null
docker compose logs api --tail=100
```

Recovery:
- Move old backups off-host.
- Clean unused Docker layers: `docker system prune`.
- Archive old `output` files after retention review.
- Increase disk size before deleting customer data.

### Upload Failures

Diagnosis:

```bash
docker compose logs api --tail=200 | grep -i "upload\|413\|415\|quarantine\|file"
df -h
```

Recovery:
- Confirm file type and size limits.
- Confirm `uploads` volume is mounted and writable.
- Check tenant batch limits if batch upload fails.

### Export Failures

Diagnosis:

```bash
docker compose logs api --tail=200 | grep -i "export\|fill\|output"
df -h
```

Recovery:
- Confirm `output` volume is mounted and writable.
- Confirm source extraction is completed and validated.
- Restart API if ReportLab/OpenPyXL errors are transient.

### Healthcheck Fails

Diagnosis:

```bash
docker compose ps
docker compose logs api --tail=200
curl -i http://127.0.0.1:8000/api/v1/health
curl -i http://127.0.0.1:8000/api/v1/health/ready
```

Recovery:
- If DB check fails, recover DB first.
- If OCR is degraded but DB is ok, uploads may still start but OCR should be investigated.
- Restart API after correcting env or dependency problems.
