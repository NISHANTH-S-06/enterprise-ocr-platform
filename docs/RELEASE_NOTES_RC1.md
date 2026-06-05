# Release Notes - OCR Enterprise Platform v1.0.0-RC1

## Audience: Internal Stakeholders

`v1.0.0-RC1` is the first release candidate for the OCR Enterprise Platform. It includes the full OCR workflow, validation, exports, batch processing, optional Gemini recovery, SaaS tenant management, deployment hardening, backup guidance, and observability runbooks.

Primary business capability:
- Upload business documents.
- Extract OCR text.
- Map fields.
- Validate extracted data.
- Export to PDF, Excel, JSON, and batch ZIP.
- Operate as a multi-tenant SaaS backend.

Release status:
- Tests: `154 passed`.
- E2E: PASS.
- Deployment documentation: complete.
- Monitoring documentation: complete.

## Audience: Operations Team

Deployment target:
- Recommended: Ubuntu 22.04 VPS or AWS EC2 with Docker Compose.
- Minimum: 4 vCPU, 8 GB RAM.
- Preferred: 4-8 vCPU, 16 GB RAM.
- Persistent storage required for `uploads`, `output`, `templates_store`, and database backups.

Runtime requirements:
- PostgreSQL.
- PaddleOCR runtime dependencies.
- Docker Compose for the packaged deployment path.
- `alembic upgrade head` runs before API startup in Docker.
- API Docker worker count defaults to one for OCR memory safety.

Health endpoints:
- `/api/v1/health`
- `/api/v1/health/ready`

Operational docs:
- `docs/PRODUCTION_DEPLOYMENT_RUNBOOK.md`
- `docs/OBSERVABILITY_MONITORING_RUNBOOK.md`
- `docs/PRODUCTION_CHECKLIST.md`

## Audience: Future Customers

Supported workflow:
- Upload PDF and image documents.
- Review OCR extraction and mapped fields.
- Export completed results.
- Use batch upload for multiple documents.
- Tenant-aware access controls separate organisations and users.

Supported file types:
- PDF
- PNG
- JPEG/JPG
- TIFF
- BMP
- WebP

Current limits:
- Single upload max defaults to 50 MB.
- Batch upload defaults to 10 files.
- Batch total defaults to 100 MB.
- Tenant-specific batch limits can override defaults.
- Gemini fallback is optional and disabled by default in production.

Production requirements:
- Strong secrets.
- Explicit CORS origin configuration.
- Public registration disabled unless approved.
- Persistent storage.
- Daily backups.
- Health monitoring.
- OCR verification on target host.

Known RC1 limitations:
- No bundled paid monitoring integration.
- No built-in rate limiter.
- No external object storage integration yet.
- Production pilot should capture OCR latency and memory baselines.

