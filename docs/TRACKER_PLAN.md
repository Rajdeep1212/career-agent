# TRK0 — Application tracker plan (plan only)

Status: approved by Rajdeep on 3 October 2026 (decisions at the end of section 9). Design: `docs/TRACKER_DESIGN.md`.
Every hard constraint in `CLAUDE.md` and `docs/RUNBOOK.md` still holds; section 9 lists where the design touches one.

## 1. What already exists (so this is not built twice)

The repo already has a single-user tracker from M2. The new tracker replaces it; it must not sit beside it.

| Existing piece | Where | Today |
|---|---|---|
| Applications, stored jobs, contacts, outreach links | `app/storage/career_store.py`, tables `career_applications`, `career_jobs`, `career_contacts`, `career_outreach` in `data/agent.sqlite3` | 1 application, 87 stored jobs, 0 contacts |
| Append-only event log, job snapshots, profile versions | `app/storage/career_events.py`, tables `job_events`, `job_snapshots`, `profile_versions`, with UPDATE/DELETE triggers | 1 event, 1 snapshot |
| Statuses | `FUNNEL_EVENTS`: SAVED, APPLIED, ONLINE_TEST, INTERVIEW, OFFER, REJECTED, WITHDRAWN, SKIPPED; no-response is derived, or confirmed by a `no_response_confirmed` event | |
| API | `app/api/career.py`: `/applications`, `/applications/applied`, `/applications/{id}/events`, undo, `/tracker/funnel`, `/contacts` | served to the vanilla JS dashboard |
| Migrations | `app/storage/db.py`: backup, one-time apply, read-only open path | |
| Access control | loopback hosts and exact-origin checks in `app/main.py`; no users, no login | |
| Outgoing email | draft → approve → send in `app/services/email_send_boundary.py`; Gmail scope `gmail.send` only | |

Already installed in `job-agent`: SQLAlchemy 2.0.36 and psycopg 3.2.3. Node 24 and Docker 29 are on the machine.
Not installed: Alembic, redis, arq, resend, a JWT library.

## 2. Data model

One schema, owned by SQLAlchemy models and Alembic migrations. Local and tests: `data/tracker.sqlite3` (a new file, so
`data/agent.sqlite3` and the radar index stay byte-identical). Deployment: Postgres. Every user-owned table carries
`owner_id`, and every query filters on it.

| Table | Columns (keys in bold) | Notes |
|---|---|---|
| `users` | **id**, email (unique), display_name, role (`student`, `mentor`, `admin`), google_sub, created_at, disabled_at | In `AUTH_MODE=local` one fixed local user exists |
| `mentor_links` | **id**, student_id, mentor_id, invited_at, accepted_at, revoked_at | A mentor reads a student's data only while a link is accepted and not revoked |
| `refresh_tokens` | **id**, user_id, token_hash, issued_at, expires_at, revoked_at, replaced_by | Only a hash is stored; rotation on every use |
| `companies` | **id**, owner_id, name, company_key, seed_company, ats, careers_url, reapply_after, notes | `company_key` uses `job_identity.company_key`; `seed_company` points at a row of `seeds/companies_seed.csv` when one matches |
| `contacts` | **id**, owner_id, company_id, name, role, channel, referral_status, notes, created_at | |
| `cv_versions` | **id**, owner_id, label, created_at, file_sha256, skills_json, notes | The CV file and its text are not stored here; `skills_json` is the local parser's skill list, for diffs |
| `applications` | **id**, owner_id, company_id, job_id (index key, nullable), title, company_name, url, source, channel, status, applied_at, cv_version_id, next_follow_up_at, notes, created_at, updated_at, and the snapshot: snapshot_json, snapshot_sha256, snapshot_captured_at | `status` is a cache of the event log. The snapshot holds title, company, URL, JD text, posted date and the eligibility card as they were at "Mark applied". Unique on (owner_id, job identity) |
| `application_events` | **id**, application_id, owner_id, event_type, occurred_at, occurred_at_exact, recorded_at, source (`user`, `derived`, `import`, `email_suggestion`), request_id, undoes_event_id, note, context_json | Append-only: UPDATE and DELETE are refused by triggers in both SQLite and Postgres. (owner_id, request_id) is unique, so a retried request records nothing twice |
| `reminders` | **id**, owner_id, application_id, kind (`nudge_7`, `nudge_14`, `no_response_30`, `custom`), due_at, state (`pending`, `sent`, `dismissed`, `done`), sent_at, dedupe_key (unique) | The unique key is what makes "none duplicated" testable |
| `email_suggestions` (TRK7) | **id**, owner_id, application_id, message_hash, suggested_event, quote, state (`open`, `confirmed`, `rejected`), created_at | A suggestion never changes a status by itself |

Migration from the M2 tracker (TRK1): back up `data/agent.sqlite3`, copy its 1 application, its events and the
snapshots they reference into the new store under the local user, verify counts, and leave the old tables in place,
read-only, until TRK2 retires the old routes. Idempotent and tested, as `CLAUDE.md` requires. The CSV of hand-logged
applications is imported the same way, each row as an `import` event.

## 3. Status transition rules

Statuses: SAVED, APPLIED, ONLINE_TEST, INTERVIEW, OFFER, REJECTED, WITHDRAWN, NO_RESPONSE. (The existing SKIPPED stays
as "dismissed before applying" and is not a Kanban column.)

| From | Allowed next |
|---|---|
| SAVED | APPLIED, SKIPPED |
| APPLIED | ONLINE_TEST, INTERVIEW, OFFER, REJECTED, WITHDRAWN, NO_RESPONSE |
| ONLINE_TEST | INTERVIEW, OFFER, REJECTED, WITHDRAWN, NO_RESPONSE |
| INTERVIEW | INTERVIEW (another round: an event, no status change), OFFER, REJECTED, WITHDRAWN, NO_RESPONSE |
| OFFER | WITHDRAWN (declined), REJECTED (rescinded) |
| NO_RESPONSE | ONLINE_TEST, INTERVIEW, OFFER, REJECTED (a late reply) |
| REJECTED, WITHDRAWN, SKIPPED | none |

- A status changes only by appending an event. Going backwards is done by undoing the latest event (an `undone`
  event), never by editing a row.
- NO_RESPONSE is set only when the user confirms the 30-day flag. The system proposes; it never sets it.
- An invalid transition returns HTTP 409 with the allowed list. `occurred_at` may be in the past (back-dated import)
  but not before the application's `applied_at` for post-apply events.

## 4. API surface

All under `/api/v1`, JSON, owner-scoped. Mutations take an `Idempotency-Key` header (stored as `request_id`).

| Method and path | Purpose | Item |
|---|---|---|
| GET `/applications?status=&company=&q=` | list, for the Kanban | TRK2 |
| POST `/applications` | create from an index `job_id` or from pasted fields; captures the snapshot | TRK2 |
| POST `/applications/quick-add` | create from a URL: match to the index, else store what was given | TRK2 |
| GET, PATCH `/applications/{id}` | detail with timeline; edit notes, follow-up date, CV version | TRK2 |
| POST `/applications/{id}/events` | append an event; validates the transition | TRK2 |
| POST `/applications/{id}/events/{event_id}/undo` | undo the latest event | TRK2 |
| GET, POST `/cv-versions` | list, register (label + file hash) | TRK2 |
| GET, POST, PATCH `/companies`, `/contacts` | company and contact records | TRK2 |
| POST `/import/applications` | CSV import (dry run first) | TRK1 |
| POST `/auth/login/google`, `/auth/refresh`, `/auth/logout`; GET `/auth/me` | sign-in, token rotation | TRK4 |
| POST `/mentors/invite`, `/mentors/{id}/accept`, DELETE `/mentors/{id}` | mentor links | TRK4 |
| GET `/reminders`, POST `/reminders/{id}/dismiss` | follow-up queue | TRK5 |
| POST `/applications/{id}/follow-up-draft` | a Gmail draft through the existing approve-then-send boundary | TRK5 |
| GET `/suggestions`, POST `/suggestions/{id}/confirm` or `/reject` | email-status suggestions | TRK7 |
| GET `/analytics/funnel`, `/analytics/rates?by=source` | numbers with intervals, or "too early" | TRK8 |

The existing routes in `app/api/career.py` keep answering until the dashboard has moved to the new ones, then are removed.

## 5. Auth and RBAC

- **Tokens**: JWT access token, 15 minutes, and a rotating refresh token, 14 days; both in httpOnly cookies, `SameSite=Lax`,
  `Secure` outside local mode. Refresh tokens are stored hashed; reuse of a rotated token revokes the chain.
- **CSRF**: mutations require the app's exact origin (the check `app/core/origin_security.py` already has) plus a
  custom header.
- **Google sign-in**: OIDC with scopes `openid email profile`, on its own OAuth client. It is separate from the Gmail
  client, whose code refuses any grant other than `gmail.send`; the two must never share a token.
- **`AUTH_MODE=local`**: no login, one local user, and the server refuses to start in this mode unless it is bound to a
  loopback host. This keeps the single-user Windows setup working.

| Role | Own data | Linked student's data | Other users | Users and roles |
|---|---|---|---|---|
| student | read, write | — | none | none |
| mentor | read, write (a mentor is also a user) | read only: applications, events, analytics; not notes, contacts or CV versions | none | none |
| admin | read, write | none by default | none (no content access) | list, disable, change role |

Gate for TRK4: a generated test matrix, every endpoint × every role × {own, linked, stranger}, asserting the expected
status code, plus tests that an id from another user returns 404, not 403.

## 6. Worker jobs

Redis + arq in deployment; in local mode an in-process scheduler calls the same functions. Every job takes the clock as
an argument, so tests use a fake clock.

| Job | Schedule | Does |
|---|---|---|
| `scan_follow_ups` | hourly | For each APPLIED, ONLINE_TEST or INTERVIEW application with no reply event: create `nudge_7`, `nudge_14`, `no_response_30` reminders when due. `dedupe_key = application:kind` makes a second run a no-op |
| `send_daily_digest` | 08:00 Asia/Kolkata | One email to the user through Resend: due reminders, open suggestions, this week's counts. No CV text, no JD text |
| `check_email_status` (TRK7) | every 30 minutes | Read the apply inbox over IMAP, match messages to applications, write suggestions |
| `poll_boards` (later) | per the radar's rules | The existing polite sync, moved onto the queue |

## 7. Test plan per item

| Item | Tests | Gate numbers |
|---|---|---|
| TRK1 | Alembic upgrade and downgrade on SQLite and on Postgres (Postgres in Docker, skipped with a clear message when absent); append-only triggers in both; M2 import and CSV import, each run twice to prove idempotence; owner scoping on every table | migrations up and down; rows before = rows after |
| TRK2 | every transition in section 3, allowed and refused; undo; idempotency key; snapshot survives the index job being removed; quick-add matching on recorded pairs | match precision and recall on the 50 labelled pairs |
| TRK3 | component tests; a Playwright smoke run: add, drag across columns, open the timeline, pick a CV version | all pass |
| TRK4 | the endpoint × role matrix; refresh rotation and reuse detection; `AUTH_MODE=local` refuses a non-loopback bind | full matrix passes |
| TRK5 | fake clock over 40 days: each reminder exactly once, none after a reply, digest content has no CV or JD text, Resend calls recorded not sent | none missed, none duplicated |
| TRK6 | round trip: shortlist → "Mark applied" → event → label export → MCP `track_application` (which writes, so it is a new, separately permitted tool) | round trip passes |
| TRK7 | rules and the local classifier on recorded `.eml` fixtures; nothing changes status without a confirm | precision, recall, false-"rejected" rate on about 100 labelled emails |
| TRK8 | funnel and rate functions on fixtures with known answers; bootstrap intervals reuse `app/eval/semantic.py`; "too early" below the minimum counts | numbers with intervals, or the message |

## 8. ADR: the stack

**Decision** (as the design states): FastAPI as the single backend; SQLAlchemy models with Alembic migrations;
PostgreSQL in deployment and SQLite for tests and single-user local mode; Redis with an arq worker, and an in-process
scheduler in local mode; a Next.js + Tailwind app in `web/`; Resend for emails to the user only. Recruiter outreach
stays on Gmail behind approval.

**Why**
- One Python backend and one schema owner: Prisma is replaced by SQLAlchemy + Alembic so two ORMs never share a database.
- Tracker data is user-owned and cannot be rebuilt, so it gets a real database with backups. The job index stays in
  SQLite because it is derived and the evaluation depends on frozen copies and hashes.
- The same models on SQLite keep the offline test suite and the Windows single-user setup working without Docker.
- Redis and arq give retries and schedules for reminders; the functions are plain and clock-driven, so local mode does
  not need Redis.
- Next.js and Tailwind for a Kanban and timeline UI; the vanilla JS dashboard keeps working meanwhile.

**Consequences**
- Two migration systems for a while: `app/storage/db.py` for `agent.sqlite3` and the radar, Alembic for the tracker.
  They never touch the same file.
- SQLite and Postgres differ (JSON columns, triggers, time zones). The models use portable types, and the migration
  tests run on both.
- A Node toolchain and `web/node_modules` join the repo, and the gate gains a frontend test step.
- New things to approve before they are installed (the runbook requires asking): Alembic, redis, arq, resend, a JWT
  library, the Node packages for `web/`, and the Postgres and Redis Docker images.
- Disk: C: had 8.2 GB free at the last check. Docker's data goes on F:, and `web/node_modules` should be measured at
  install, as SEM2's packages were.

## 9. Open questions, each with a recommendation

1. **The M2 tracker already exists.** Recommendation: TRK1 replaces it with a backed-up, idempotent import, as in
   section 2, and TRK2 retires the old routes. Never two live trackers.
2. **A new local database file.** `data/tracker.sqlite3` would be a third file the runbook's hash check should name.
   Recommendation: add it to the start-of-session check, with "changes only through tracker actions".
3. **Resend is a hosted service and the design says to verify its free tier.** Recommendation: approve it at TRK5, after
   checking the limits and the sending-domain rule then. If a verified domain is required and there is none, fall back
   to sending the digest to yourself through the existing Gmail `gmail.send` grant, behind the same approval boundary.
4. **Where does Postgres run in deployment?** The design does not say. Recommendation: decide before TRK4. Until
   then everything runs locally on SQLite, and Postgres is used only in the migration tests.
5. **What may a mentor see?** Recommendation: status pipeline and analytics only; not notes, contacts, CV versions or
   JD snapshots, as in section 5.
6. **IMAP on the apply inbox (TRK7).** `CLAUDE.md` allows IMAP on a dedicated alerts inbox only, read-only apart from
   `\Seen`. Recommendation: treat this as a constraint change that needs your explicit approval and an edit to
   `CLAUDE.md`, at TRK7, not before.
7. **CV versions.** Recommendation: store label, date, file hash and the local parser's skill list only. The CV file and
   its text stay on the machine and never go into digests, Resend, or a hosted model.
8. **MCP `track_application` writes.** The current MCP server is read-only by design. Recommendation: keep the three
   read tools as they are and add the writing tool as a separate, explicitly enabled one at TRK6.
9. **The design's "Keka and Workday adapters" step has no queue row.** Recommendation: add one after TRK3 when you
   approve this plan; I have not added it.
10. **Queue position.** TRK0–TRK3 sit before SEM3 (which waits for the next index sync) and TRK4–TRK8 before Q15, as the
    design's order says. Say if you want them elsewhere.

### Decisions (Rajdeep, 3 October 2026), in the order of the questions above

1. Yes. TRK1 imports the M2 tracker into the new store; TRK2 retires the old routes. Never two live trackers. The import
   is idempotent and re-runnable, and is run once more at the TRK2 cutover to pick up anything recorded in the old
   tables in between.
2. Yes. `data/tracker.sqlite3` joins the start-of-session hash check, noted "changes only through tracker actions".
3. Yes. Resend is decided at TRK5 after checking its limits; the fallback is the Gmail `gmail.send` grant behind the
   same approval boundary.
4. Deferred. Everything runs locally on SQLite. Postgres is used only in migration tests until Rajdeep decides, before TRK4.
5. Yes. Mentors see the pipeline and analytics only.
6. Yes. IMAP on the apply inbox is a constraint change decided at TRK7, not before.
7. Yes. CV versions store only label, date, file hash and the local parser's skill list.
8. Yes. The three read-only MCP tools stay; `track_application` is a separate, explicitly enabled tool at TRK6.
9. Yes. Two rows follow TRK3: KEKA1 (Keka adapter) and WD1 (Workday adapter).
10. Queue position as proposed.

Installs approved for TRK1: Alembic only. Postgres migration tests use the Docker image only if Docker's data root is
on F:; otherwise they are skipped with a clear message.
