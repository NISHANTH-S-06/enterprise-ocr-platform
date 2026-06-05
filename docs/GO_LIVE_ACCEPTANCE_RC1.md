# Go-Live Acceptance Criteria - v1.0.0-RC1

## Required Validation

- [ ] `python -m compileall app` passes.
- [ ] `.venv311\Scripts\python.exe -m pytest tests -q` passes with `154 passed`.
- [ ] E2E run passes required thresholds.
- [ ] `/api/v1/health` passes.
- [ ] `/api/v1/health/ready` passes with database check `ok`.
- [ ] OCR upload smoke test passes.
- [ ] Export smoke test passes.
- [ ] Batch upload smoke test passes.

## Production Configuration

- [ ] `APP_ENV=production`.
- [ ] `SECRET_KEY` is unique and strong.
- [ ] `DATABASE_URL` points to production Postgres.
- [ ] `CORS_ALLOWED_ORIGINS` is explicit.
- [ ] `ALLOW_PUBLIC_REGISTRATION=false` unless approved.
- [ ] `AI_FALLBACK_ENABLED` decision approved.
- [ ] Gemini key configured only if fallback is enabled.

## Backups

- [ ] Database backup scheduled.
- [ ] File backups scheduled for `uploads`, `output`, and `templates_store`.
- [ ] Backups copied off-host.
- [ ] Restore tested.
- [ ] Backup freshness alert configured.

## Monitoring

- [ ] Healthcheck alert configured.
- [ ] DB unavailable alert configured.
- [ ] Disk usage alert configured.
- [ ] Memory usage alert configured.
- [ ] 5xx rate alert configured.
- [ ] OCR and batch failure alerts configured.
- [ ] Gemini alert configured if fallback is enabled.

## Security

- [ ] Superadmin accounts reviewed.
- [ ] Admin org assignment reviewed.
- [ ] Tenant isolation tests passing.
- [ ] Public registration policy approved.
- [ ] `.env` excluded from Docker build context and source control.
- [ ] TLS/reverse proxy configured.

## Final Decision

Go-live status:
- [ ] Approved
- [ ] Approved with conditions
- [ ] Rejected

Approvers:
- Engineering:
- Operations:
- Database:
- Security:
- Product:

