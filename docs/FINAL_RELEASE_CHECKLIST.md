# Final Release Checklist

## Environment And Database

- [ ] `.venv311` exists and dependencies are installed
- [ ] Production variables are supplied outside source control
- [ ] Database backup completed
- [ ] `alembic upgrade head` reviewed and run by an operator
- [ ] Heads/current both report `0008 (head)`

## Automated Verification

- [ ] JavaScript check passes with duplicate IDs 0
- [ ] Application and Alembic compile checks pass
- [ ] `scripts/run_verify.ps1` passes 395 tests
- [ ] `scripts/run_e2e.ps1` reports 4/4 PASS

## Manual Demo

- [ ] PDF and image OCR flows
- [ ] XLSX and CSV tabular flows
- [ ] Dataset selection and mapping
- [ ] Correction learning
- [ ] ERP master import and resolution
- [ ] Blocked export returns HTTP 422
- [ ] Allowed JSON, Excel, and PDF exports succeed

## Release Risk

- [ ] No secrets or local user paths in release files
- [ ] No active source, migrations, tests, customer data, or templates removed
- [ ] Backup archive and rollback plan reviewed
- [ ] Legacy endpoints identified as compatibility paths
- [ ] Excluded credential-like smoke script reviewed separately
- [ ] Log and browser-profile retention approved separately
