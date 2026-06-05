# OCR Enterprise Platform Production Checklist

## Security

- [ ] `APP_ENV=production`.
- [ ] `SECRET_KEY` is unique, random, at least 32 characters, and not committed.
- [ ] `CORS_ALLOWED_ORIGINS` is explicit and does not use `*`.
- [ ] `ALLOW_PUBLIC_REGISTRATION=false` unless there is a controlled onboarding policy.
- [ ] `.env` is not in Docker build context or source control.
- [ ] Admin and superadmin accounts are created through a controlled process.
- [ ] HTTPS/TLS is terminated by a reverse proxy or load balancer.
- [ ] Firewall exposes only required ports.

## Deployment

- [ ] Docker image builds from a clean context.
- [ ] `alembic upgrade head` runs before app startup.
- [ ] API starts with one worker unless OCR memory has been profiled.
- [ ] `/api/v1/health` responds.
- [ ] `/api/v1/health/ready` responds.
- [ ] Logs are collected and rotated.

## Database

- [ ] PostgreSQL is used, not SQLite.
- [ ] DB credentials are strong and not defaults.
- [ ] Migrations are current.
- [ ] Daily DB backup is scheduled.
- [ ] Restore procedure has been tested.

## OCR

- [ ] PaddleOCR loads successfully in production.
- [ ] OCR runtime flags are set:
  - [ ] `FLAGS_use_mkldnn=0`
  - [ ] `FLAGS_enable_pir_api=0`
  - [ ] `OMP_NUM_THREADS=1`
- [ ] Host has enough RAM for OCR.
- [ ] Sample upload and OCR polling pass.

## Gemini

- [ ] `AI_FALLBACK_ENABLED=false` unless needed.
- [ ] `GEMINI_API_KEY` is present only if fallback is enabled.
- [ ] Gemini failure path is understood and does not block core OCR.

## Backups

- [ ] Database backup runs daily.
- [ ] `uploads` backup runs daily.
- [ ] `output` backup runs daily.
- [ ] `templates_store` backup runs daily.
- [ ] Backups are copied off-host.
- [ ] Retention policy is documented.

## Monitoring

- [ ] API healthcheck monitored.
- [ ] DB health monitored.
- [ ] Disk usage alert configured.
- [ ] Memory usage alert configured.
- [ ] OCR failure/error log alert configured.
- [ ] Backup freshness alert configured.
- [ ] 5xx rate alert configured at greater than 5% over 10 minutes.
- [ ] OCR failure rate alert configured at greater than 10% over 15 minutes.
- [ ] Batch failure rate alert configured at greater than 10% over 15 minutes.
- [ ] Gemini quota/API failure alert configured if AI fallback is enabled.
- [ ] Incident runbook reviewed by operators.

## Storage

- [ ] `uploads` is persistent.
- [ ] `output` is persistent.
- [ ] `templates_store` is persistent.
- [ ] Backup storage has enough capacity.
- [ ] Disk cleanup policy is documented.

## Tenant Security

- [ ] Superadmin access is restricted.
- [ ] Admin users are assigned only to their organisation.
- [ ] Tenant limits are configured.
- [ ] Organisation settings contain no secret-like keys.
- [ ] Cross-tenant access tests are passing.

## Registration Policy

- [ ] Public registration disabled in production.
- [ ] User provisioning process defined.
- [ ] Role assignment process defined.
- [ ] Deactivation process defined.
