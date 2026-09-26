# Career Agent

[![CI](https://github.com/Rajdeep1212/career-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Rajdeep1212/career-agent/actions/workflows/ci.yml)

Career Agent is a local FastAPI application for evidence-based job discovery,
application tracking, and user-approved outreach. It parses text-based PDF
resumes, persists profile and search preferences, searches configured job
providers, verifies listings, applies eligibility rules, ranks matches, and
stores application and outreach state.

The application is designed for a single user on `localhost`. It does not apply
to jobs automatically. Gmail sending requires a stored draft, explicit approval,
and a separate send action.

## Features

- Deterministic search intent, bounded query planning, deduplication, listing
  verification, eligibility checks, and explainable ranking.
- Persistent profiles, preferences, search sessions, applications, contacts,
  and outreach linkage in local storage.
- Application lifecycle tracking from `DISCOVERED` and `SAVED` through terminal
  outcomes. Opening an application link does not mark it `APPLIED`.
- Atomic Gmail draft sending with terminal handling for uncertain failures.
- Optional LangGraph conversation orchestration with deterministic tool routing,
  durable local resume, and an Ollama-compatible model adapter.
- Optional LinkedIn OpenID Connect connection for allowlisted profile metadata.
- Static browser dashboard served by the FastAPI application.

## Requirements

- Python 3.11 or newer
- Node.js for the frontend regression script
- A RapidAPI JSearch credential only when running live job searches
- Google or LinkedIn credentials only when enabling those optional integrations

## Setup

Create an environment and install the pinned dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

On macOS or Linux, activate with `source .venv/bin/activate` and copy the
example with `cp .env.example .env`.

Edit the new `.env` locally. Leave optional credentials empty when their
integration is not needed. Never replace an existing `.env`, commit it, or put
secrets in browser code. `TOKEN_ENCRYPTION_KEY` is required before storing OAuth
tokens; the example file includes a local generation command.

Start the application:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Open `http://localhost:8010/app/`. Windows users may instead run
`01_SETUP_WINDOWS.ps1` once and `02_START_WINDOWS.ps1` to start the app.

## Configuration

`.env.example` documents every public setting. The main options are:

| Setting | Purpose |
| --- | --- |
| `APP_ORIGIN` | The only browser origin allowed to change data; defaults to `http://localhost:8010`. |
| `JOB_PROVIDERS` | Job providers to use (default `radar,jsearch,adzuna,jooble`); `radar` is the local Company Radar index (used once it has data), and a provider without its key is skipped. |
| `RAPIDAPI_KEY` | Enables live JSearch requests. |
| `RAPIDAPI_HOST` | JSearch RapidAPI host. |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Enable Adzuna (India). Jobs are labeled "Jobs by Adzuna" as its terms require. |
| `JOOBLE_API_KEY`, `JOOBLE_HOST` | Enable Jooble; the key must match its country site (default `in.jooble.org`). |
| `ADZUNA_DAILY_LIMIT`, `ADZUNA_MONTHLY_LIMIT`, `JOOBLE_KEY_LIMIT` | Free-plan limits (250/day and 2,500/month for Adzuna, 500 per key in total for Jooble). Requests are counted on this machine, and the dashboard warns before a search that would exceed them. |
| `SEARCH_WARN_REQUESTS` | The dashboard asks before a search that will send at least this many provider requests (default 5), or more than a provider's remaining quota. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Enable Gmail OAuth. |
| `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` | Enable LinkedIn OpenID Connect. |
| `TOKEN_ENCRYPTION_KEY` | Encrypts locally stored OAuth tokens. |
| `REQUEST_TIMEOUT_SECONDS`, `VERIFY_SSL` | Bound and secure provider requests. |
| `CHAT_MODEL_PROVIDER` | Chat assistance provider; defaults to `none`. |
| `CHAT_MODEL_NAME`, `OLLAMA_BASE_URL` | Local Ollama model and loopback endpoint. |
| `GEMINI_API_KEY`, `GEMINI_MODEL_NAME` | Hosted benchmark only; accepts internal synthetic fixtures and uses the fixed Google API endpoint. |
| `CHAT_MODEL_TIMEOUT_SECONDS` | Bounds each optional model call; defaults to 45 seconds. |
| `CHAT_MODEL_STRUCTURED_OUTPUT`, `CHAT_MODEL_TOOL_CALLING` | Explicit opt-in model capabilities. |
| `LANGGRAPH_CHECKPOINT_PATH` | Ignored local conversation checkpoint database. |

Runtime data, uploaded CVs, OAuth records, and local databases remain under
ignored local paths. Tests use disposable storage and block live HTTP.

The browser can call `POST /chat/run` and `POST /chat/resume` with stable
`thread_id` and `turn_id` values. Both are localhost-origin mutations. The
graph stores only bounded sanitized text and safe IDs. Outreach pauses once at
the final editable draft; one confirmed resume approves it and enters the
existing atomic Gmail send boundary.

## Demo mode and Docker

`DEMO_MODE=true` runs a self-contained demo for sharing: a synthetic profile and
synthetic jobs in a separate `DEMO_DATA_DIR`. Every credential in `.env` is
ignored (only their names are logged), OAuth and email routes are unavailable,
sending is refused, listings are never fetched, and outbound HTTP is blocked.
The app refuses to start if `DEMO_DATA_DIR` overlaps the real data directory.
`tests/test_demo_mode.py` proves the real data directory is left untouched.

```powershell
docker build -t career-agent-demo .
docker run -p 7860:7860 career-agent-demo   # open http://localhost:7860/app/
```

The image defaults to demo mode and never contains `.env` or `data/`. On a
hosted Space, set `APP_ORIGIN` to the Space's public URL.

## Local data and upgrades

Everything the app stores lives in `data/` (ignored by Git). Database changes
are numbered migrations: before a pending migration runs, the database is
copied to `data/backups/<UTC time>/`, and nothing is applied if that copy
fails.

**Recovering from a failed upgrade:** stop the app, copy the database file from
the newest `data/backups/<time>/` folder back into `data/`, and start the
previous version. The seen-job history moved from `app/storage/` to
`data/job_history.sqlite3`; the old file is copied once and never modified or
deleted, so removing `data/job_history.sqlite3` re-imports it on the next start.

## Verification

Run the complete offline checks from the repository root. Tests live in
`tests/`, use recorded fixtures and never touch the live network:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
node tests/test_frontend.cjs
python -m ruff check .
python -m mypy
```

`python run_tests.py` runs the same suite without the dev tools.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Job providers](docs/PROVIDERS.md)
- [LinkedIn setup](LINKEDIN_SETUP.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Current build status](CAREER_AGENT_BUILD_REPORT.md)

The project remains under development. See the build report for implemented
stages and current limitations.
