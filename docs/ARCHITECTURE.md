# Architecture

Career Agent is a local, single-process FastAPI application with a static
JavaScript dashboard and SQLite-backed storage. It separates job discovery from
application tracking and approved outreach.

## Runtime flow

1. Profile and preference stores load the latest persisted candidate settings.
2. `CareerAgent` interprets a request, discovers bounded role families, and
   creates a limited set of provider queries.
3. Enabled providers return normalized jobs. Canonical identities remove
   duplicates and previously seen jobs unless the request includes them.
4. The verifier inspects safe public destinations and records
   `ACTIVE_VERIFIED`, `LIKELY_ACTIVE`, `UNVERIFIED`, or `CLOSED` with evidence.
5. Eligibility rules reject incompatible listings. Matching ranks the remaining
   jobs and explains supporting and missing evidence.
6. Search sessions and normalized jobs are persisted for refinement and tracker
   use.

The optional conversational path wraps this flow without replacing it:

1. `/chat/run` loads safe conversation references and applies deterministic
   routing first.
2. A configured chat model may add a validated read-only routing hint,
   explanation, or advisory role ideas. Advisory roles never alter provider
   search scope.
3. Allowlisted graph tools call `CareerAgent`, `career_store`, and the shared
   outreach services.
4. Outreach pauses at one final send-confirmation interrupt. The confirmed
   resume applies any draft edits outside graph state, approves the draft, and
   calls the existing atomic send boundary.

## Components

- `app/main.py` assembles FastAPI routes, the dashboard, OAuth, profile,
  preferences, search, and email endpoints.
- `app/api/` contains the tracker and LinkedIn routers.
- `app/agent/` contains minimal LangGraph state, deterministic routing, nodes,
  and allowlisted adapters to existing services.
- `app/llm/` contains the provider-independent model interface, disabled
  default, and loopback-only Ollama adapter.
- `app/providers/` contains provider adapters, registry selection, and provider
  normalization boundaries.
- `app/services/` contains orchestration, intent, planning, verification,
  eligibility, matching, CV parsing, OAuth, and outreach services.
- `app/storage/` contains additive SQLite stores for local profile, preference,
  search history, applications, contacts, OAuth, attachments, and email state.
- `app/static/` contains the browser dashboard.

## Tracker and outreach state

Applications retain the lifecycle `DISCOVERED`, `SAVED`, `APPLIED`,
`OUTREACH_PREPARED`, `OUTREACH_SENT`, `INTERVIEW`, `REJECTED`, `OFFER`, and
`SKIPPED`. Reading a job or opening its URL does not change application status.

Outreach persists the linkage
`job_id -> application_id -> optional contact_id -> draft_id`. Preparing or
sending outreach updates its own tracker state without marking the application
`APPLIED`.

Email drafts move through `draft -> approved -> sending -> sent`. An exception
after the atomic send claim produces terminal `send_failed`, because the remote
Gmail outcome may be uncertain.

## Conversation persistence

`data/langgraph.sqlite3` is separate from application and outreach storage and
is ignored by Git. Stable thread and turn IDs create durable idempotency
receipts, so duplicate browser submissions cannot replay an action. Checkpoints
contain the latest sanitized user message, a bounded summary, workflow state,
and safe identifiers. They exclude OAuth data, credentials, CV contents,
provider payloads, recipient addresses, tracker notes, and email bodies.

`CHAT_MODEL_PROVIDER=none` is the default. Ollama capabilities are explicit;
models without validated structured output can contribute only safe free-text
explanations. Free text can never select side-effect tools. Model calls have a
bounded timeout and fail back to deterministic behavior. Requests that ask the
model to reveal credentials, override safeguards, or bypass approval are kept
out of the model boundary, and value-bearing secret patterns are redacted from
checkpoint text.

## Security boundaries

The service binds to loopback, validates exact-local origins for browser
mutations, encrypts OAuth records, allowlists response fields, and keeps
provider errors sanitized. Verification rejects private and unsafe network
destinations and bounds redirects, response size, concurrency, and time.

The automated suite replaces storage with disposable locations and blocks live
external HTTP.
