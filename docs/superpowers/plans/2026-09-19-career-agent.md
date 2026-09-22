# Career Agent Implementation Plan

> Historical implementation plan. Checklist state is preserved as authored;
> see `CAREER_AGENT_BUILD_REPORT.md` for the current verified status.

> Execute stages with tests-first changes and the existing offline runner. The user explicitly authorizes execution without further confirmation.

**Goal:** Extend the existing local FastAPI app into a reusable career agent with evidence-based discovery, explainable recommendations, tracking and approved outreach.
**Architecture:** Backward-compatible Pydantic models; deterministic, extensible role/skill catalog plus arbitrary-title fallback; structured intent and conversation state; bounded Python service orchestration; lawful provider registry; additive SQLite migrations. No new LLM credential is required and no claim of unconstrained language understanding is made.
**Tech stack:** Existing Python/FastAPI/httpx/Pydantic/SQLite/Fernet and static JavaScript.
**Spec:** User's 32-section request supplied 2026-09-19.

## Constraints and preservation

- `.verification/career_baseline.json` records hashes before edits; private files are never printed.
- Preserve `.env`, encryption key, OAuth implementation/data, saved profile/uploads, legacy history and drafts.
- No real Gmail sending, LinkedIn publishing, applications, scraping or new consent.
- Tests use isolated data and block live HTTP. One final controlled live search session, bounded to four provider queries, is the sole live provider validation.
- New schemas accept old profiles and jobs. Existing response fields remain for compatibility.
- No Git metadata exists; document changes and hashes rather than fabricate commits.

## Stage A — schemas, candidate and intent intelligence

- [ ] Extend `app/models/schemas.py` with optional evidence fields and generic preferences; define career models in `app/models/career.py`.
- [ ] Add `candidate_intelligence.py`, generic CV extraction and an extensible role catalog. Only observed resume facts enter the profile.
- [ ] Add `interpret_search_request(text, previous=None) -> SearchIntent`, `suggest_role_families(profile) -> list[RoleSuggestion]`, and `expand_roles(intent, profile) -> list[RoleSuggestion]`.
- [ ] Tests: legacy profile round-trip, multiple degrees/years, transferable QA/analytics evidence, explicit/multiple/arbitrary/noncoding roles, location exclusions, score refinements, no unsupported skills.
- [ ] Run complete suite before advancing.

## Stage B — planner and provider normalization

- [ ] `plan_search_queries(profile, intent, roles, limit=4) -> list[SearchQuery]`: 4–10 unique concise queries, round-robin roles/locations, explicit budget, no raw prompt forwarding.
- [ ] Provider registry retains JSearch; unavailable future providers are declared honestly. Normalize source IDs, URLs, employment, experience, dates, salary.
- [ ] Canonical identity and cross-provider deduplication preserve legacy seen history with a new identity table.
- [ ] Tests: bounded query count, arbitrary titles, requested locations, remote, old/new payloads, safe provider errors, URL/ID identity.
- [ ] Run complete suite.

## Stage C — verification, eligibility and matching

- [ ] Add safe outbound fetch with HTTP(S)/public-IP validation, checked redirects, DNS pinning, time/body limits and bounded concurrency.
- [ ] Verification records ACTIVE_VERIFIED/LIKELY_ACTIVE/UNVERIFIED/CLOSED and evidence. 403/timeout/JS-only is not fabricated closure.
- [ ] Eligibility separates hard rejection/warnings from matching; graduation and experience constraints derive from profile/preferences/intent.
- [ ] Explainable match components, transferable skills and missing evidence; no personal project bonuses or unexplained percentages.
- [ ] Tests: private/loopback/DNS/redirect blocking, ATS/error cases, experience/year language, seniority, relevance and unknown skills.
- [ ] Run complete suite.

## Stage D — orchestration, diagnostics and recommendations

- [ ] `CareerAgent.search(query, include_seen=False, session_id=None, strict_mode=None)` coordinates all stages and returns compatibility fields plus intent, roles, queries, counts, reasons and session ID.
- [ ] Aggregate provider failures safely, verification budget and latency logs without request text/secrets.
- [ ] Render understood intent, planned queries, all diagnostic counters and richer result cards in existing dashboard.
- [ ] Test multi-query search, empty paths, partial provider failure and legacy POST /agent/search.
- [ ] Run complete suite.

## Stage E — application tracker

- [ ] Add versioned `career_store.py` tables for sessions/jobs/applications/contacts/outreach links; no destructive migrations.
- [ ] API list/save/update applications with enumerated states, explicit APPLIED timestamp and notes. Merely opening a link never changes state.
- [ ] Dashboard tracker supports save, status updates and notes.
- [ ] Tests: migrations idempotent, old data retained, state/timestamps, duplicate saves and restart persistence.
- [ ] Run complete suite.

## Stage F — outreach and approval

- [ ] ContactCandidate records only user-entered or public evidence; no invented addresses or private discovery. No reliable contact is an explicit result.
- [ ] Personalized email and short message draw only from actual profile/job facts; no unsupported graduation/research claims.
- [ ] Reuse draft→approve→send; associate outreach with application without marking APPLIED. Harden send against duplicate concurrent sends.
- [ ] Tests: contact provenance, grounded text, unapproved and repeated send blocked with mocked Gmail.
- [ ] Run complete suite.

## Stage G — structured conversation and settings

- [ ] Persist intent and recommendations per session. Score-only refinements filter stored results without provider use; role/location refinements merge structured fields and re-plan.
- [ ] Dashboard navigation: Career Agent, Recommendations, Profile, Tracker, Outreach, Connections, Settings. Keep existing OAuth controls.
- [ ] Generic fictional fallback/demo, profile review/edit and persisted preferences; preserve existing private profile.
- [ ] Test session restart/refinement, no prompt concatenation, UI controls/escaping/URLs and OAuth regressions.
- [ ] Run complete suite and JavaScript scenarios.

## Stage H — documentation and final validation

- [ ] README and docs/ARCHITECTURE, SECURITY, PROVIDERS, CAREER_AGENT, CONTRIBUTING; practical setup for another student.
- [ ] Compile, full offline suite, smoke/dashboard/LinkedIn/Gmail/CV/JS tests, credential scan and protected hash comparison.
- [ ] Run one controlled live JSearch session with four concise queries, bounded verification, isolated validation storage. Record actual counts/errors; never rerun to manufacture success.
- [ ] Produce CAREER_AGENT_BUILD_REPORT.md covering all requested sections, exact test/live results, limitations and manual steps.
