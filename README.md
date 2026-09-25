# Career Agent

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
| `RAPIDAPI_KEY` | Enables live JSearch requests. |
| `RAPIDAPI_HOST` | JSearch RapidAPI host. |
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

## Verification

Run the complete offline checks from the repository root:

```powershell
python run_tests.py
node test_frontend.cjs
python -m compileall -q app
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Job providers](docs/PROVIDERS.md)
- [LinkedIn setup](LINKEDIN_SETUP.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Current build status](CAREER_AGENT_BUILD_REPORT.md)

The project remains under development. See the build report for implemented
stages and current limitations.
