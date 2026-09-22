# Career Agent Build Report

Status date: 2026-09-21

The Career Agent remains under development. The core search foundation is stable under the current offline regression suite; later workflow and release stages remain incomplete.

## Completed stages

### Stage A — schemas, candidate intelligence, intent and role discovery

- Backward-compatible candidate, job, preference and career-agent models are implemented.
- Resume evidence, candidate analysis, deterministic English search intent, explicit-role preservation and bounded role expansion are implemented.
- Strict-mode negation preserves the prior role and location.
- “Strong profile match” deterministically maps to a 75% minimum match score.

### Stage B — planning and provider normalization

- Search planning emits at most four concise, distinct provider queries without forwarding the raw prompt.
- Provider normalization covers identity, URLs, employment, work mode, experience, graduation years, salary and skills.
- Canonical identity and cross-provider deduplication preserve distinct listing URLs and legacy seen history.
- One malformed listing or application URL is skipped or normalized safely without aborting its batch.

### Stage C — verification, eligibility and matching

- Safe outbound verification validates destinations, redirects and public IPs and applies bounded time, body and concurrency limits.
- Verification distinguishes verified, likely active, unverified and closed listings using role-scoped evidence.
- Eligibility and matching use candidate evidence, explicit constraints, transferable skills and explainable score components.
- Candidate experience is no longer globally capped at one year.
- Industry and freshness preferences are enforced with explicit rejection reasons when listing evidence does not satisfy them.
- FAQ, footer and unrelated-role closure wording do not mark the current listing closed.

### Stage D — orchestration, diagnostics and recommendations

- `CareerAgent.search` coordinates intent, role expansion, bounded query planning, providers, deduplication, verification, eligibility, matching, history and stored sessions.
- `/agent/search` now delegates to `CareerAgent`, forwards `include_seen`, `session_id` and `strict_mode`, and retains the legacy `agent_action` response field.
- Score-only follow-ups reuse stored ranked candidates without provider calls.
- Provider failures are aggregated without exposing response bodies, prompts or credentials.
- The dashboard renders interpreted intent, role suggestions, planned queries, diagnostic counters and recommendation evidence.
- One controlled live Career Agent validation completed through JSearch, normalization, verification, eligibility and ranking. It returned 20 unique jobs and 8 recommendations from four bounded queries without changing normal history or saved user data.
- Explicit references to saved or preferred locations now merge persisted locations into intent and bounded planning while retaining explicit locations and removing duplicates.

### Stage E — application tracker

- The existing career router is registered in the running FastAPI application.
- Application list/get/save/update, job reads, stored sessions and contact routes use the existing `career_store` lifecycle.
- Tracker and contact mutations require the exact localhost origin; read responses remove private cache, credential and raw-provider fields without changing stored session data.
- Reading jobs or applications has no application-state side effect, while outreach preparation and sending retain their separate `PREPARED` and `SENT` states.

### Stage G — structured conversation and settings

- `GET/PUT /profile/current` and `GET/PUT /preferences/current` use the existing profile and preference stores.
- Partial updates preserve untouched CV evidence and reject unknown, private and invalid fields.
- Profile, work-mode, location, role, experience, fresher, match-score and strict-verification settings persist and are loaded by each Career Agent search.
- Profile, preference and CV-upload mutations require the exact localhost origin; existing CV parsing and attachment behavior remains compatible.

### Stage H — documentation and offline release verification

- The public README and `.env.example` provide generic clone, configuration, run and test instructions without candidate-specific data.
- Architecture, provider, contribution, security and MIT license documents are present.
- Local credentials, user data, uploads, databases, validation artifacts, caches, environments and build output are excluded by `.gitignore`.
- The source tree passed a filename-only secret/private-data scan; no high-confidence credentials, private keys, authorization headers, local absolute paths or candidate-specific CV data were found.
- Unused SQLAlchemy and psycopg dependencies and obsolete setup reports were removed without changing runtime storage or API behavior.

### Stage I - conversational orchestration foundation

- A provider-independent chat-model factory defaults to `CHAT_MODEL_PROVIDER=none`; the first optional adapter supports loopback-only Ollama.
- Explicit model capability flags control structured output and tool calling. Invalid structured output fails closed, and free text is limited to safe explanations.
- LangGraph calls the existing deterministic CareerAgent, tracker, outreach and atomic Gmail services through allowlisted tools; it does not replace provider, verification, eligibility, ranking or persistence logic.
- Stable thread/turn IDs provide durable request idempotency. A separate ignored SQLite checkpoint stores bounded sanitized text and safe identifiers, excluding recipients, draft bodies, credentials, CVs and provider payloads.
- Outreach uses one final editable-draft confirmation interrupt. Confirmation performs deterministic approval followed by the existing one-attempt Gmail send boundary.
- Offline evaluation covers normal and ambiguous requests, read-only model routing, tracker and outreach actions, malformed model output, injection text, restart/resume, checkpoint content and duplicate turn IDs.
- Ollama calls are bounded by a configurable 45-second default timeout and fail safely to deterministic behavior. Checkpoint sanitization redacts both secret labels and values, while secret-exfiltration and safeguard-bypass prompts do not enter the model boundary.
- One controlled local evaluation used `qwen3:1.7b` with mocked CareerAgent tools. The model produced valid structured output but was too slow and inconsistent for authoritative routing on the tested CPU-only host, so deterministic routing and the `none` default remain unchanged.

## Partial stages

### Stage F — outreach and approval

Grounded outreach drafting and contact provenance exist. Runtime preparation now resolves stored job, application and optional contact identifiers; stored job facts and stored contact methods are authoritative. It idempotently creates a `SAVED` application when needed, persists `job -> application -> contact -> draft` linkage and the short message, and records outreach as `PREPARED` without changing application status. Existing outreach rows are preserved by the additive contact-link migration. The Gmail send endpoint uses an atomic `approved -> sending` claim, permits at most one Gmail API send attempt per approved draft, records `sent` or terminal `send_failed`, updates linked tracker outreach without marking the application `APPLIED`, and requires the exact localhost origin for browser email mutations.

## Remaining stages

- Perform the separately authorized live Gmail validation for Stage F; no live Gmail call was made during offline linkage work.
- Build the conversational frontend only after its interaction and approval flow is separately reviewed.

## Exact test status

- Required focused gate: `python -m unittest test_intent test_provider test_verification test_requirements test_matching test_planning -q` — **68 tests passed**.
- Tracker/outreach focused gate after real-app router integration: `python -m unittest test_tracker test_outreach test_smoke -q` — **40 tests passed**, smoke passed.
- Profile/settings regression gate: `python -m unittest test_profile_settings test_regressions test_tracker test_outreach -q` — **52 tests passed**.
- Complete repository offline runner after local-model hardening: `python run_tests.py` — **197 tests passed**, dashboard smoke passed, smoke passed.
- Conversational model/graph/outreach gate: `python -m unittest test_chat_models test_career_graph test_chat_api test_outreach -q` — **43 tests passed**.
- Dashboard JavaScript DOM simulation: `node test_frontend.cjs` — **passed**.
- Application compilation: `python -m compileall -q app` — **passed**.
- Controlled live validation: **1 Career Agent session**, **4 bounded JSearch requests**, **20 provider results**, **8 final recommendations**.

## Known limitations

- Search intent parsing is deterministic and English-focused. Ambiguous or unsupported wording is surfaced through warnings and can require user review.
- JSearch is the only enabled external job provider. Other connectors are declared unavailable until lawful implementations and credentials are supplied.
- Verification is evidence-based but cannot guarantee that a third-party listing remains open after it is checked. Uncertain pages remain unverified rather than being presented as active.
- PDF extraction supports text-based PDFs; scanned resumes require OCR before upload.
- Search quality depends on the evidence available in the saved profile and provider listing. Missing evidence is reported rather than invented.
- Freshness remains a per-search intent because the existing persistent preference model does not define a freshness field.
- A terminal `send_failed` means Gmail delivery may be uncertain. The application will not retry that draft automatically; the user must deliberately create and approve a new draft.
- The live run included a sanitized JSearch availability/quota error and one provider title with a replacement character; provider data quality and availability remain external limitations.
- A separately authorized live Gmail validation is pending. The tested local model remains optional because its latency and routing quality are not suitable for authoritative use on the current CPU-only host, so the Career Agent must not be described as complete.
