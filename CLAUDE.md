# CLAUDE.md

Read `docs/AUDIT_AND_ROADMAP.md` first. It is the baseline for the current state, the milestone
order (M1 → M5) and the decisions already made. Don't re-audit the repo; update that doc instead.

## Goal

Career Agent is a local-first job-search agent for freshers, especially students from tier-2/3
colleges in India. It should work for any student's profile, not one person's. The research goal is
honest, measurable outcomes: estimates labeled at the right claim level, evidence-backed job risk
signals, and outreach grounded only in real CV and job-post facts.

## Hard constraints

- No scraping of LinkedIn or Indeed. Use official APIs, JSearch, public ATS job-board endpoints and
  data the user exports themselves.
- Human approval is mandatory for every outgoing email. Never auto-send. Keep the
  draft → approve → send boundary in `app/services/email_send_boundary.py`.
- Never fabricate CV content or claims.
- Secrets stay in `.env`. Never log, print or commit them.
- Gmail scope is `gmail.send` only. Changing it needs the owner's explicit approval.
- Hosted models never see real CV content. Gemini stays gated to synthetic evaluation. Ollama (local)
  is the default when a model is enabled.
- The app must keep running locally on Windows (see the `.ps1` scripts).
- Ask before adding paid APIs or new dependencies.

## Claim levels

Every score or estimate shown to a user, or written in docs, must carry its level:

| Level | Meaning | Example wording |
|---|---|---|
| L0 | Deterministic heuristic score | "Heuristic fit 72/100" |
| L1 | Prior estimate (heuristic mapped to [0,1]); not calibrated | "Prior estimate ~12% (not calibrated)" |
| L2 | Model estimate; not yet validated | "Model estimate 15% (90% interval 7–28%)" |
| L3 | Calibrated probability; validated on held-out outcomes | "Estimated 15%", with a link to the reliability diagram |
| L4 | Causal claim | Never made. "What should I change?" is predictive or coverage-based. |

Ghost/scam output is a set of evidence-backed risk signals, never a factual accusation.

## Eligibility model

`app/services/eligibility.py` returns a three-way `EligibilityResult` (claim level L0):
`status` is `eligible`, `uncertain` or `excluded`; `summary` and `evidence` quote the listing
(e.g. `excluded: quoted '5+ years experience required' (...)`). Exclude only on an explicit
disqualifier in the listing or an explicit user constraint; ambiguous or missing evidence is
`uncertain` and is shown, ranked below `eligible`. `eligible: bool` remains for compatibility and
is False only when excluded. Search results carry `eligibility_status` and `eligibility_summary`.

## Job sourcing

The Company Radar index (`app/sources/`, `app/storage/radar_store.py`) is the primary source:
official ATS APIs, and robots-allowed sitemaps with schema.org JobPosting data. Interactive search
reads the index and cached results; aggregators (JSearch, Adzuna, Jooble) run only on a manual
refresh. Seed files (`app/core/radar/seed_vN.json`) are versioned and never edited after release;
only entries with `enabled` and `reviewed` true are synced, and undocumented endpoints stay opt-in.
Evidence and run-log counts must come from parsed data (`len()` of the fetched list), never estimates.
Tests use recorded fixtures; the sync never runs in DEMO_MODE.

Alert emails (`app/sources/alerts/`) are read over IMAP from a dedicated inbox only, or from `.eml`
files; the only mailbox change allowed is flagging processed messages `\Seen`. Their jobs, and jobs
saved with the bookmarklet (`/capture`), live in `data/alerts.sqlite3` and are served by the local
`alerts` provider with sources `LinkedIn alert`, `Naukri alert`, `Indeed alert` or `Saved by you`.
LinkedIn, Naukri and Indeed pages are never fetched (`application_verifier.never_fetched`).

## Working rules

- Run the full offline suite after every change: `python -m pytest` (or `python run_tests.py`),
  `node tests/test_frontend.cjs`, `python -m ruff check .` and `python -m mypy`.
- Write a failing regression test before fixing a bug.
- Keep commits small, with one concern each.
- Database and path migrations need a backup first, must be idempotent, and must be tested. Never
  lose existing user state silently.
