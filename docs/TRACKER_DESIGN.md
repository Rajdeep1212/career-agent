# Career Agent + Application Tracker: merged design (2026-10-03)

## The idea in one line

The career agent **finds and ranks** jobs. The tracker **manages what happens after you apply**. They are one product with one backend and one shared job/company model. The agent feeds jobs into the tracker, and the tracker's outcomes feed back into the agent.

Every feature from the original tracker plan (the anurag.builds carousel) is kept. Each one also gets something only the career agent can add.

## Feature map

| Tracker feature (original plan) | How it lives in the career agent | What the agent adds |
|---|---|---|
| Application status pipeline | Kanban: saved → applied → online test → interview → offer, plus rejected / withdrawn / no response. Every change is a row in an append-only `application_events` log. | "Mark applied" on any shortlisted job creates the application with a **snapshot of the job post** (title, company, URL, JD text, posted date, eligibility card), so it survives the posting being taken down. |
| Follow-up email reminders | Rules engine: nudge at 7 and 14 days with no reply, and flag "no response" at 30 days (you confirm). A daily **Resend** digest to yourself. | Follow-up emails to recruiters are drafted from your CV facts + the JD only, and sent through Gmail **only after you approve**. |
| Analytics dashboard | Funnel (applied → test → interview → offer), weekly activity, time to first response, ghost rate. | Answers real questions, each with a **bootstrap interval** like SEM2: do jobs applied to within 48 h of posting reply more? Referral vs portal? Which CV version works better? Which source leads to interviews? Later, **calibrated shortlist chances** (Brier score + reliability plot once there are ~30 outcomes). |
| Resume tracking | `cv_versions`: label, date, file hash. Every application records which version was sent. | The local CV parser diffs the skills between versions. The "what should I change?" analysis (idea #2) runs on real outcomes. |
| Company tracking | Applications link to the existing company radar (`companies_seed`, ATS type, careers URL). Contacts, referral status, notes, past applications, and a reapply cooldown warning. | Company prep playbooks (Q16) open automatically on an online-test or interview event. New-role alerts (Q15) cover watchlist companies. |
| JWT + OAuth authentication | FastAPI owns auth: JWT access token + refresh token in httpOnly cookies, plus Google sign-in (OIDC). `AUTH_MODE=local` skips login so the single-user Windows setup keeps working. | Makes the agent usable by **other students**, which the original brief asked for ("the app only works for me, not for any student"). |
| Role-based access | Roles: **student** (own data), **mentor** (read-only on students who invite them, e.g. a placement coordinator or senior), **admin**. Every query is scoped to the owner. | An authorization test matrix (every endpoint × every role) is a gate, not an afterthought. |

## Stack: what each tool does

| Slide stack | Role in the merged product |
|---|---|
| **Next.js + Tailwind** | New `web/` app for the tracker UI: Kanban, application timeline, quick-add, analytics. It talks to the FastAPI backend. The existing vanilla JS dashboard keeps working and can move over gradually. |
| **PostgreSQL** | Stores user-owned data (users, applications, events, CV versions, contacts) in deployment. Tests and single-user local mode run on SQLite through the same models. |
| **Prisma** | **Replaced by SQLAlchemy + Alembic** (the Python equivalent). The backend is Python, and one schema owner prevents two ORMs drifting apart on one database. This is the only substitution. |
| **Redis** | Job queue (arq worker) for follow-up reminders, email-status checks and ATS polling, plus rate limits and caching. In local mode, an in-process scheduler stands in. |
| **Resend** | Emails **to yourself**: reminders and the daily digest. Outreach to recruiters stays on Gmail with approval. Verify the free-tier limits and the sending-domain rules at build time. |
| FastAPI (existing) | Single backend for both the agent and the tracker API. |

**Two stores, on purpose.** The job index stays in SQLite: it's derived data that can be rebuilt, and the eval machinery depends on its frozen copies and hashes. Tracker data is user-owned and can't be recreated, so it goes to Postgres with backups. Applications reference index jobs by `job_id` and keep their own snapshot, so no cross-database joins are needed.

**Windows note.** Postgres + Redis run via `docker compose`. Put Docker's data on F:, because C: is short on space.

## How data flows

```
index (SQLite) ── shortlist ── "Mark applied" ──► applications + JD snapshot (Postgres)
                                                       │
outside jobs (URL paste / bookmarklet) ── match to index ┘
                                                       │
email replies ── status suggestion ── you confirm ──► application_events
                                                       │
           ┌───────────────────────────────────────────┤
           ▼                     ▼                     ▼
  ranker labels         calibrated chances     prep playbook on test/interview
  (applied/interview     (Brier, reliability)   (Q16), follow-up queue (Q17)
   = strong positives)
```

## Rules carried over from the agent

- Tests use recorded fixtures and a fake clock, never the live network.
- Full gate on every item. DB hashes are reported before and after.
- No auto-send. Every outgoing email is approved by a human.
- Hosted models never see the CV. Email-status detection runs locally or on rules.
- Gmail OAuth stays at `gmail.send`. Reading replies uses an IMAP app password on the apply inbox (needs your approval, TRK7).
- Every user's data is scoped to them. Cross-user access tests are part of the gate.

## Portfolio numbers this produces

- Email-status detection: precision and recall on about 100 labelled emails, and the false-"rejected" rate.
- URL-to-index matching: precision and recall on about 50 labelled pairs.
- Reminders: zero missed or duplicated in fake-clock tests.
- Authorization: full endpoint × role matrix passes.
- Outcome analytics with intervals; calibration once there are ~30 outcomes.

## Queue (format: ID | Item | Gate | YOU)

| ID | Item | Gate | YOU |
|---|---|---|---|
| TRK0 | PLAN: `docs/TRACKER_PLAN.md` + ADR for the stack (FastAPI + SQLAlchemy/Alembic + Postgres/SQLite + Redis/arq + Next.js/Tailwind + Resend). Also bump the runbook gate line to 949 and record the install footprint (150 MB C:, 65 MB F:) in `docs/eval/env_check.md`. | Plan doc only | Approve |
| TRK1 | Data model: `applications` (with JD snapshot), `application_events` (append-only), `cv_versions`, `contacts`, link to companies; Alembic migrations; CSV import of applications already logged by hand | Migrations up/down on SQLite and Postgres; import tests | Hand over the CSV of applications logged so far |
| TRK2 | Tracker API in FastAPI: CRUD, validated status transitions, event log, quick-add by URL with index matching | Tests; match P/R on 50 labelled pairs | Label the 50 pairs |
| TRK3 | `web/` Next.js + Tailwind: Kanban, application detail with timeline, quick-add, CV-version picker | Frontend tests + Playwright smoke | – |
| TRK4 | Auth: JWT access/refresh (httpOnly), Google sign-in, `AUTH_MODE=local`; RBAC student/mentor/admin, mentor invites | Endpoint × role authorization matrix; cross-user tests | Create the Google OAuth client for sign-in |
| TRK5 | Reminders: Redis + arq worker, follow-up rules (7/14/30 days), Resend daily digest to self, Gmail follow-up drafts behind approval | Fake-clock tests: none missed, none duplicated | Resend API key in `.env` |
| TRK6 | Agent integration: "Mark applied" from the shortlist, outcomes exported as ranker labels, test/interview event → playbook hook, MCP `track_application` | Round-trip tests | – |
| TRK7 | Email-status detection: IMAP app password on the apply inbox; rules + local classifier; suggestions you confirm | P/R on ~100 labelled emails; false-"rejected" rate | Decide on IMAP access; label the emails |
| TRK8 | Analytics dashboard: funnel and rates by source / channel / CV version / apply latency with bootstrap intervals; calibration when ≥30 outcomes | Numbers with intervals, or a "too early" message | – |

## Order against the rest of the roadmap

1. **Today, no code:** log every real application (ServiceNow, Observe.AI, Bosch, …) in a table with TRK1's columns: company, role, URL, source, channel, date applied, CV version, status, status date, next follow-up, notes. TRK1 imports it.
2. **TRK0–TRK3 next.** Outcome data takes weeks to accumulate, so capture can't wait.
3. Then the **Keka and Workday adapters** (more good jobs in).
4. Then **TRK4–TRK8**, alongside Q15–Q18.
