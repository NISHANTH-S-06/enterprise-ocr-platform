# OCR Enterprise Platform Release Readiness - v1.0.0-RC1

Release candidate: RC1 Candidate

Recommended version: `v1.0.0-RC1`

Release date: 2026-06-03

## 1. Versioning Strategy

Version format: `MAJOR.MINOR.PATCH`

Release labels:
- Release candidate: `v1.0.0-RC1`, `v1.0.0-RC2`
- Stable production release: `v1.0.0`
- Patch release: `v1.0.1`
- Hotfix release: `v1.0.1-hotfix.1`

Policy:
- Increment `MAJOR` for breaking API contracts, destructive migration requirements, or incompatible deployment changes.
- Increment `MINOR` for backward-compatible features.
- Increment `PATCH` for backward-compatible fixes, security hardening, documentation corrections, and operational improvements.
- Use `RC` tags for release candidates that have passed tests and E2E but still require production signoff.
- Use `hotfix` tags only for urgent production fixes after a stable release.

Rollback versions:
- Every production deployment must record the deployed Git commit, image tag, database migration head, and environment template revision.
- Rollback target should be the latest known-good release tag.
- Database rollback should prefer backup restore unless an Alembic downgrade path has been tested.

## 2. Deployment Acceptance Criteria

Required before go-live:
- Tests: `154 passed`.
- E2E: PASS against required document thresholds.
- Health: `/api/v1/health` PASS.
- Readiness: `/api/v1/health/ready` PASS, database check `ok`.
- Backups: database and file backup configured and restore-tested.
- Monitoring: health, disk, memory, DB, OCR failures, 5xx rate, and backup freshness alerts configured.
- Security: production `SECRET_KEY`, explicit CORS origins, public registration disabled unless approved.
- Database: migrations current and Postgres healthy.
- OCR: real OCR engine verified in production target.
- Storage: `uploads`, `output`, and `templates_store` persistent.
- Tenant security: cross-tenant isolation tests passing and admin/superadmin policies confirmed.

Go-live cannot proceed if any critical acceptance item is incomplete.

## 3. Rollback Criteria

Rollback is required when:
- OCR engine cannot start or sustained OCR failure blocks core usage.
- Database migration fails or causes data loss/corruption risk.
- Deployment fails health/readiness after one controlled restart.
- Tenant isolation issue is suspected.
- Critical security issue is discovered.
- API returns sustained critical 5xx rate after deployment.
- Upload/export core flow is broken for valid documents.

Rollback sequence:

```bash
cd /opt/ocr-platform
docker compose ps
docker compose logs api --tail=300
git checkout <known-good-tag-or-commit>
docker compose build api
docker compose up -d api
curl -fsS http://127.0.0.1:8000/api/v1/health
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
```

If database restore is required:

```bash
docker compose down
docker compose up -d db
gunzip -c backups/db/ocrdb-YYYYMMDD-HHMMSS.sql.gz | docker compose exec -T db psql -U ocr -d ocrdb
docker compose up -d
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
```

Preserve logs and backup the broken state before destructive recovery when possible.

## 4. Go-Live Signoff Checklist

Engineering:
- [ ] Release tag `v1.0.0-RC1` created.
- [ ] Compile passes.
- [ ] Full pytest suite passes.
- [ ] E2E thresholds pass.
- [ ] No unapproved runtime changes since final validation.

Operations:
- [ ] Deployment runbook reviewed.
- [ ] Observability runbook reviewed.
- [ ] On-call owner assigned.
- [ ] Rollback owner assigned.

Database:
- [ ] Postgres healthy.
- [ ] Alembic migration head confirmed.
- [ ] Backup created before deployment.
- [ ] Restore procedure tested.

Security:
- [ ] Production secrets configured.
- [ ] CORS locked to approved origins.
- [ ] Public registration policy approved.
- [ ] Superadmin accounts reviewed.

OCR:
- [ ] PaddleOCR or approved OCR engine loads in production.
- [ ] Sample OCR upload verified.
- [ ] OCR latency baseline captured.

Monitoring:
- [ ] Healthcheck alert configured.
- [ ] DB alert configured.
- [ ] Disk/memory alerts configured.
- [ ] 5xx alert configured.
- [ ] OCR/batch failure alerts configured.

Backups:
- [ ] Database backup scheduled.
- [ ] File volume backups scheduled.
- [ ] Off-host copy configured.
- [ ] Backup freshness alert configured.

Deployment:
- [ ] Docker build succeeds.
- [ ] API starts with one worker.
- [ ] `uploads`, `output`, and `templates_store` persisted.
- [ ] Reverse proxy/TLS configured.

Tenant security:
- [ ] Organisation management verified.
- [ ] Tenant user management verified.
- [ ] Tenant limits verified.
- [ ] Cross-tenant access denied.

Gemini:
- [ ] `AI_FALLBACK_ENABLED` decision approved.
- [ ] `GEMINI_API_KEY` configured only if fallback is enabled.
- [ ] Gemini failure mode reviewed.

## 5. Release Readiness Scorecard

Product readiness: 92%
- Core OCR, validation, exports, batch, Gemini fallback, detection report, and SaaS tenant management are complete.
- Remaining gap: production usage feedback and real customer workload profiling.

Deployment readiness: 90%
- Docker hardening, migrations, healthchecks, runbooks, and storage persistence are documented.
- Remaining gap: target-host smoke test and SSL/reverse-proxy signoff.

Security readiness: 88%
- Tenant isolation, role gates, secret hardening, CORS production guard, and registration guard exist.
- Remaining gap: external security review, rate limiting, and penetration testing.

Operational readiness: 86%
- Backup, monitoring, alert thresholds, and incident runbooks exist.
- Remaining gap: live alert wiring and restore drill evidence from production-like host.

Support readiness: 82%
- Release notes, limitations, operations runbooks, and troubleshooting guidance exist.
- Remaining gap: customer-facing support SOPs and SLA definitions.

Overall RC1 readiness: 88%

Recommendation: approve `v1.0.0-RC1` for controlled production pilot after go-live checklist signoff.

