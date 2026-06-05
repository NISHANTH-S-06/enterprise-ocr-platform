# Changelog - v1.0.0-RC1

Release candidate: RC1 Candidate

## OCR Engine

- Added full upload-to-OCR processing pipeline.
- Added PaddleOCR-first engine loading with Tesseract fallback and mock fallback disabled in production.
- Added PDF/image validation and safe upload handling.
- Added OCR job lifecycle tracking and job polling.

## Mapper Engine

- Added document classification and form field mapping.
- Added sales invoice mapping support.
- Added line item handling and mapper debug visibility.
- Preserved legacy-compatible mapped field shape.

## Validation Engine

- Added mapped field validation.
- Added confidence/warning/error reporting.
- Preserved source method metadata for Gemini-recovered fields.

## Exports

- Added PDF export.
- Added Excel export.
- Added JSON export.
- Added safe output generation and download flow.

## Batch Processing

- Added multi-file batch upload.
- Added batch status and child job visibility.
- Added batch ZIP export.
- Added batch storage summary support.

## Gemini Fallback

- Added optional Gemini fallback for missing or low-confidence fields.
- Added confidence and source-evidence protections.
- Added audit metadata for AI fallback usage.
- Added tenant-level AI fallback setting foundation.

## Detection Report

- Added per-field detection reporting.
- Added missing, weak, AI recovered, and needs-review counts.
- Added safe evidence snippets.

## Organisation Management

- Added superadmin organisation management API.
- Added list/create/get/patch operations.
- Added slug generation, duplicate rejection, unsafe slug rejection, and secret redaction.
- Added organisation created/updated audit events.

## Tenant User Management

- Added organisation user listing.
- Added user assignment to organisation.
- Added user role update.
- Added user organisation move.
- Enforced superadmin-global and admin-own-organisation behavior.

## Tenant Limits

- Added tenant limit/settings helper foundation.
- Added batch file count and total size enforcement by organisation setting.
- Preserved org-less legacy defaults.
- Added safe setting redaction.

## Deployment Hardening

- Added `.dockerignore`.
- Added Docker migration startup.
- Tuned Docker worker count to one for OCR memory safety.
- Added Docker and Compose healthchecks.
- Added persistent `templates_store` volume.
- Hardened production `SECRET_KEY`, CORS, and public registration behavior.

## Monitoring Package

- Added production deployment runbook.
- Added production checklist.
- Added observability and monitoring runbook.
- Added alert thresholds and incident response procedures.
- Added production environment example.

