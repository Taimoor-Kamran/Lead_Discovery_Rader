# Release checklist — v1.0.0

Walk this by hand on the production machine. Tick every box (or write why it is consciously
skipped) before tagging. The tag itself is a human action.

- [ ] `make check`, `make e2e` (development stack, `CRM_DESTINATION=fake`) and `make audit` pass
- [ ] `.env.prod` filled in from `.env.prod.example` with a fresh `JWT_SECRET` and `POSTGRES_PASSWORD`; a copy stored somewhere safe outside the machine
- [ ] `make prod-up` starts cleanly; the api and worker logs show no refused settings (`make prod-logs`)
- [ ] The API answers only on 127.0.0.1: `curl -s http://127.0.0.1:8000/api/v1/health` works, `curl http://<LAN address>:8000/api/v1/health` does not connect
- [ ] `make migrate PROD=1` applied; `make seed-admin PROD=1` created the real administrator
- [ ] Signed in as the real admin; `admin@example.com` deactivated (or never existed); Users page shows no `@example.com` accounts
- [ ] `make backup PROD=1` then `make backup-verify PROD=1` both succeed; the Health page shows the backup time and *last verify OK*
- [ ] Health page shows no open alerts (a `backup_age` warning on a brand-new install clears with the first backup)
- [ ] Legal/compliance review of the outreach done by the client (blueprint slide 43) — who, when
- [ ] Pilot (`docs/pilot.md`) completed with its results table filled in, or consciously postponed — reason:
- [ ] `git tag -a v1.0.0` by a human after the above (`scripts/spec-finish.sh` for the merge)
