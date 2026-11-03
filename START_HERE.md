# Start here — Saudi B2B backend

Your files are in the `backend/` directory. Use the commands below from inside that folder.

## Windows PowerShell (Python 3.15)

```powershell
cd backend
py -3.15 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Set a strong JWT_SECRET in .env before running.
python -m app.cli init-db
python -m app.cli seed-demo
python -m app.cli create-admin --username admin --phone +966500000000
python -m pytest -q
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs

### Automatic matching worker (second terminal)

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.notification_worker
```

## Important

- `APP_ENV=development` and SQLite are for local evaluation only.
- The registration OTP appears in the **development** response for testing, but must never be used live.
- Live SMS/email delivery requires configured credentials and the notification worker in the second terminal. Production mode remains gated.
- The uploaded real Saudi supplier spreadsheet is deliberately **not** included in this ZIP. Upload it yourself later using an admin account, beginning with a dry run.
- This runnable backend is independently generated and must not be merged into your existing Codex M00/M01/M02 database without schema and code review.

Detailed instructions: `backend/README.md` and `backend/IMPLEMENTATION_STATUS.md`.
