# Preparing this standalone backend for GitHub

Run these commands from the `backend` directory. This folder did not contain
Git metadata when checked on 2026-11-03; no existing commit history could be reviewed.

The `.gitignore` excludes local environment secrets, certificates, virtual
environments, database files, private uploads, disposable PostgreSQL test clusters,
local audit reports, caches and build output. Keep `.env.example`, `data/.gitkeep`,
the entire `tests` directory, and all Alembic migrations in source control.

Before a public upload, review personal contact details in existing source and
documentation. The hardcoded recipient in `tests/test_live_integration.py` was
removed during upload preparation; it now requires `NOTIFICATION_TEST_PHONE`.
Its tests remain unconditionally skipped and do not send live messages.

Initialize and review locally:

```powershell
git init -b main
git check-ignore -v .env data/procurement.db certs/aiven-ca.pem phone-audit.local.json
git add --dry-run .
git add .
git diff --cached --stat
git diff --cached
```

Inspect the staged diff for passwords, tokens, personal contacts and organization
data before committing. A `.gitignore` does not remove files already tracked in
another repository or erase secrets from existing commits. If a credential was
previously published, rotate it and separately review that repository's history.

After reviewing the staged files, create an empty GitHub repository, then use its
actual URL in place of `YOUR_GITHUB_REPOSITORY_URL`:

```powershell
git commit -m "Initial standalone backend"
git remote add origin YOUR_GITHUB_REPOSITORY_URL
git push -u origin main
```

The existing GitHub Actions workflow runs dependency installation, pytest and
Python compilation from the repository root. Upload the contents of `backend`
as the repository root so its paths remain correct. Check the Actions result
after pushing; a local check cannot establish hosted-runner compatibility.

On a new machine, recreate `.env` from `.env.example` and provision any required
CA certificate separately. Do not upload `.env`, local databases or private files
to make a deployment work. Publishing source does not authorize production mode
or changes to a hosted database.
