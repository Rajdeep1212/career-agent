# M2 plan: outcome data engine

Status: **approved by the owner on 2026-09-30**, with the answers recorded in §8 ("Decisions"). The
core (commits 1–3) is built first. The rest follows in order, and nothing in "M2b" starts before the core and the harness.
Baseline: `5332bf0`, run_tests.py 623 OK (1 skipped), frontend, ruff, mypy green.
Sources: `docs/AUDIT_AND_ROADMAP.md` §3.4 (D1–D5), §5.0 (migration and snapshot rules), §5 "M2",
"Pre-M2 fixes" (relevance labelling), §6 (evaluation), §7.2 decision A; the code as of `5332bf0`.

## 0. Facts this plan rests on (checked read-only on 2026-09-30)

| Fact | Where |
|---|---|
| Tracker state is one row per job in `career_applications` (`status`, `applied_at`, `outreach_state`, `notes`); no history (D2). | `app/storage/career_store.py:30-34` |
| Status set today: `DISCOVERED, SAVED, APPLIED, OUTREACH_PREPARED, OUTREACH_SENT, INTERVIEW, REJECTED, OFFER, SKIPPED`. | `app/models/career.py:72`, `app/static/app.js:18` |
| `career_jobs.job_json` is overwritten on every upsert, so the job as it was when you applied is lost (D3). | `career_store.upsert_job` |
| Migrations: `db.migrate` backs up the file first, runs each migration in its own transaction, records it in `schema_migrations`. `agent.sqlite3` has `career_v1`, `career_outreach_contact_v2`. | `app/storage/db.py` |
| Removed alert/saved jobs move to `deleted_alert_jobs` in a **different** database (`alerts.sqlite3`); `career_jobs` and `career_applications` rows are not touched. | `alert_store.delete`, `api/capture.py:105` |
| Tracker writes need the exact local origin (`require_tracker_origin`). | `app/api/career.py:45` |
| Real data now: 1 application (`SAVED`, never applied), 87 `career_jobs`, 22 alert jobs, 1 row in `deleted_alert_jobs` (a captured job), 2,491 active Radar jobs; `radar.sqlite3` is 14.8 MB. | read-only counts |
| `app/core/skills/v1.json`, `v2.json` exist; `app/core/scoring/` does **not** (§5.0 expects `scoring/v{N}.json`). No `eval/` directory yet. | repo |
| `data/` is ignored by git and by Docker. | `.gitignore`, `.dockerignore` |

## 1. Event log schema

All new tables live in `data/agent.sqlite3`, added by one migration in `career_store.MIGRATIONS`
(`career_v3_events`), so the existing backup, transaction and `schema_migrations` machinery applies.

### 1.1 `job_events` (append-only)

Keyed by **job**, not application (decision Q1). Thumbs labels are given on search results that
were never saved, so an application-only log cannot hold them. Every application has exactly one job
(`career_applications.job_id` is `UNIQUE`), so per-application history is a filter on `job_id`.
This replaces the roadmap's `application_events` name.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | order of recording |
| `job_id` | TEXT NOT NULL → `career_jobs(id)` | |
| `application_id` | TEXT NULL → `career_applications(id)` | set for tracker events, NULL for thumbs on unsaved jobs |
| `event_type` | TEXT NOT NULL, CHECK in the list below | |
| `occurred_at` | TEXT NOT NULL (UTC ISO 8601) | when it happened; the user may backdate ("applied yesterday") |
| `occurred_at_exact` | INTEGER NOT NULL DEFAULT 1 | 0 for backfilled or estimated times |
| `recorded_at` | TEXT NOT NULL (UTC) | when the app stored it; never backdated |
| `source` | TEXT CHECK in (`user`, `derived`, `import`, `migration`) | |
| `request_id` | TEXT UNIQUE NULL | client-generated UUID; idempotency key (§3) |
| `undoes_event_id` | INTEGER NULL → `job_events(id)` | only for `undone` |
| `snapshot_id` | INTEGER NULL → `job_snapshots(id)` | required for `applied`, thumbs and outcome events |
| `context_json` | TEXT NULL | e.g. search query, result position, ranker version for thumbs |
| `note` | TEXT NOT NULL DEFAULT '' | |

Append-only is enforced by the database, not by convention:
`CREATE TRIGGER job_events_no_update BEFORE UPDATE ON job_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;`
and the same for `DELETE`.

**Event types.**
- Funnel (status-changing): `saved`, `applied`, `online_test`, `interview`, `offer`, `rejected`,
  `withdrawn`, `skipped`.
- Response (not status-changing): `recruiter_reply` (decision Q3) and `no_response_confirmed`.
- Outreach (not status-changing, decision Q2): `outreach_prepared`, `outreach_sent`. These are
  written where `career_store.link_outreach` and `mark_outreach_sent` already run (commit 5).
- Relevance: `thumbs_up`, `thumbs_down`, `thumbs_cleared` (commit 6).
- Notes: `removed_from_results` (decision Q7, commit 5).
- Correction: `undone` (points at the event it cancels).

The CHECK constraint lists every type from commit 1, so later commits need no schema change.

**Derived, never stored:** a **response** is any non-undone `recruiter_reply`, `online_test`,
`interview`, `offer` or `rejected` after `applied`. With no response, an application is
`PENDING_CENSORED` if it was applied under `response_window_days` ago, and `NO_RESPONSE` otherwise;
the user may confirm the latter with `no_response_confirmed`. `response_window_days = 21` becomes a
setting (§5 M2).

### 1.2 Status cache

`career_applications.status` stays, as a **cache** of the latest non-undone funnel event, rewritten in
the same transaction as each event. The API and UI keep reading it, so existing screens keep working.
`applied_at` becomes the cache of the first non-undone `applied` event. New columns (roadmap §5 M2):
`applied_via` TEXT NULL (`company_site | ats | job_board | email | referral | other`),
`effort_minutes` INTEGER NULL, `follow_up_at` TEXT NULL.

New status set: `SAVED, APPLIED, ONLINE_TEST, INTERVIEW, OFFER, REJECTED, WITHDRAWN, SKIPPED`.
Decision Q2:
- `DISCOVERED` is folded into `SAVED`.
- `OUTREACH_PREPARED` and `OUTREACH_SENT` stop being statuses. Outreach lives in `outreach_state`
  and in outreach events. A row with one of these values becomes `APPLIED` if it has `applied_at`,
  otherwise `SAVED`, and the old value goes into the backfilled event's `note`.

The only real row is `SAVED`, so no real row changes.

### 1.3 `job_snapshots`

The job and features as they were when the event happened (§5.0 "Reproducible feature snapshots").

| Column | Notes |
|---|---|
| `id` INTEGER PK | |
| `job_id` TEXT NOT NULL | |
| `captured_at` TEXT NOT NULL (UTC) | |
| `job_json` TEXT NOT NULL | frozen raw job, exactly as stored at that moment |
| `job_sha256` TEXT NOT NULL | hash of `job_json` |
| `cv_version` TEXT NULL → `profile_versions` | NULL on backfilled snapshots (`captured_late=1`): the CV in use at that time is unknown. Always set on snapshots taken at event time. |
| `feature_schema_version`, `vocabulary_version`, `ranker_version`, `model_version` | `model_version` NULL until M4. `ranker_version` is a code constant `"v1"` plus `+<short git SHA>` when git is available, else `"v1"`; it may be NULL for backfilled rows (decision Q5: no `app/core/scoring/` file in M2) |
| `features_json` TEXT NOT NULL | the L0 match components and eligibility at capture time, labelled L0 |
| `captured_late` INTEGER NOT NULL DEFAULT 0 | 1 when the snapshot was taken after the fact (backfill); excluded from training by default |

A new snapshot row is written only if `(job_id, job_sha256, cv_version, versions)` differs from the
job's latest snapshot. Otherwise the event points at the existing row, so thumbs on a job the user
sees 10 times do not store 10 copies.

### 1.4 `profile_versions`

`(cv_version TEXT PK = sha256 of the canonical profile JSON, profile_json TEXT, created_at TEXT)`.
Written on each `save_profile` and on demand when a snapshot needs it. This is real CV content: it
stays in the local database, like `current_profile.json` today, and is never sent anywhere (CLAUDE.md).

### 1.5 Migration `career_v3_events` and backfill

Runs through `db.migrate`, which backs up `agent.sqlite3` first and is idempotent through `schema_migrations`.
1. Create the tables, indexes (`job_events(job_id, id)`, `job_events(application_id, id)`) and triggers.
2. Add the new columns to `career_applications`.
3. Backfill each existing application, with `source='migration'` and `occurred_at_exact=0`:
   - `saved` at `created_at`;
   - `applied` at `applied_at`, if set;
   - if the current status is none of `SAVED`/`APPLIED`: one event for it at `updated_at`, with the
     original value in `note`;
   - one snapshot from the current `career_jobs.job_json` with `captured_late=1`. The job as it was
     when you applied cannot be recovered (D3). Say so, and never present it as a true snapshot.
4. Remap legacy status values (Q2). Unmappable values are kept and reported, never dropped (§5.0 rule 5).

Real effect today: 1 application → 1 `saved` event and 1 late snapshot.

**`deleted_alert_jobs` and removed jobs.**
- The existing `deleted_alert_jobs` row is left alone. It is in `alerts.sqlite3`, and the migration does
  not open that database.
- Removing a job (the Remove button) never deletes events or applications. `career_jobs` keeps the
  row, and the tracker still shows an applied job whose alert copy was removed.
- Decision Q7 (commit 5): removing a job that has tracker history appends a `removed_from_results`
  note event and keeps all history. A job with no history records nothing. Removal does not count
  as `withdrawn`.
- Naming clash: the job **source** "Saved by you" (bookmarklet) is not the tracker status `SAVED`.
  The UI keeps them visibly separate ("Saved by you" vs "In tracker").

## 2. "Shortlisted": exact rule

An application is **shortlisted** iff it has at least one funnel event with `event_type` in
{`online_test`, `interview`, `offer`} that has not been cancelled by an `undone` event.

- Evaluated at read time from `job_events`, never stored.
- It stays true after a later `rejected` or `withdrawn`: reaching the stage is what counts.
- An `offer` without an earlier `interview` still counts.
- Undoing the only qualifying event makes it false again.
- **Shortlist rate** = shortlisted ÷ applications with a non-undone `applied` event. The companion
  rate over **resolved** applications excludes `PENDING_CENSORED` ones, because a young application is
  unknown, not negative (§6.2).
- These are descriptive counts (L0), never estimates.
- A `recruiter_reply` without a test or interview does **not** count (decision Q3). It is a
  response (§1.1), so it does end censoring.

## 3. One-click Applied from the job card

`POST /applications/applied`, body
`{job_id, request_id, occurred_at?, applied_via?, effort_minutes?, note?}`.

- **Origin:** `require_tracker_origin`, the exact local origin, same as the other tracker writes.
- **Job:** must exist in `career_jobs` (cards already carry the stored id); otherwise 404.
- **One transaction:**
  1. create the application if missing, without inventing a `saved` event;
  2. write the snapshot;
  3. append `applied`;
  4. update the caches.
- **Idempotency:**
  - `request_id` is UNIQUE, so a retried or double-clicked request returns the first result unchanged.
  - If the job already has a non-undone `applied` event, the response is 200 with
    `already_applied: true` and no new event.
  - `occurred_at` in the future is 422, and more than 365 days in the past is 422 (Q8).
- **Undo:** `POST /applications/{application_id}/events/{event_id}/undo` with its own `request_id`.
  - It appends `undone` and recomputes the caches.
  - It is idempotent: undoing an already-undone event is a 200 no-op.
  - It is allowed for any funnel or response event of that job. A thumbs label is not undone here:
    it is removed with `label: clear` (§4).
  - The UI offers Undo in the confirmation toast for 10 seconds, and later from the application's history.
- **UI:**
  - "Mark applied" on search-result cards and tracker cards. The button is disabled while the request
    is in flight, and it reads "Applied ✓ (Undo)" after.
  - Outcome buttons on tracker cards: recruiter reply, online test, interview, offer, rejected,
    withdrawn. Each is one click, meeting the roadmap's "under 10 seconds". They post to
    `POST /applications/{application_id}/events` with `{event_type, request_id, occurred_at?, note?}`,
    with the same origin check, `request_id` idempotency, date limits and undo as Applied.
- **Compatibility:** the existing `PATCH /applications/{id}` stays. Its status changes are written as
  funnel events (`source='user'`), so nothing bypasses the log.

## 4. Thumbs labels (in-app)

- `POST /jobs/{job_id}/relevance` with `{label: up|down|clear, request_id, context}`. The client sends
  the search session id and the 1-based result position; the server adds the scale, the rubric
  version and the ranker version. The card sends no query text: the search is identified by its
  session id. (The API accepts an optional `query` field, which the UI does not use.) The CV version is on the label's snapshot. The origin check is the same as §3.
- Stored as `thumbs_up` / `thumbs_down` / `thumbs_cleared` events with a snapshot. The current label is
  the latest thumbs event; `clear` appends `thumbs_cleared` and means no label. Every label's `context_json` records
  `scale: "thumbs"` and `rubric_version: "thumbs-v1"` (decision Q6).
- **Two scales, never mixed** (decision Q6): live thumbs (binary) and the batch labels (graded 0–3,
  §5). No code path converts one into the other, and the harness reports them separately.
- They are **ranking feedback, not outcomes** (decision A; "ranking from outcomes" was rejected).
  They never change ranking at runtime in M2.
- **Harness feed:** a script `scripts/export_labels.py` writes `data/eval/labels/inapp_<UTC>.jsonl`.
  Each line holds the snapshot id, `job_sha256`, label, scale, rubric version, search session id,
  position, ranker version, `cv_version` (a hash, never CV text), `captured_at` and the job's title,
  company, location and description as snapshotted. The harness reads that export, not the live tables.
- In-app thumbs are **biased**: they are only given on what the current ranker showed. The harness
  reports them separately from the batch labels (§5), which are the primary ground truth (§6.2 D-rel).

## 5. Batch relevance labelling from a frozen copy of the index

**Freeze** with `scripts/freeze_index.py` (built in commit 7):
- It copies `data/radar.sqlite3` (`radar_store.DB_PATH`) with the SQLite backup API, a consistent
  copy that includes WAL content, to `data/eval/frozen/<UTC stamp>/radar.sqlite3`.
- The copy is switched to rollback-journal mode, so it is one self-contained file with no `-wal` or
  `-shm` beside it.
- It writes `manifest.json` in the same folder, holding:
  - the file name, the source path and the UTC stamp;
  - the SHA-256 of the copy;
  - the row count of every table, read from the copy, never estimated;
  - the app's git short SHA (null without git).
- It marks the copy and the manifest read-only (`os.chmod(path, stat.S_IREAD)`).
- It refuses to overwrite an existing folder, never modifies `data/radar.sqlite3`, and refuses to
  run in `DEMO_MODE`.
- The vocabulary version and the profile used are recorded per batch (commit 8), not in this manifest.

**Read-only in use:**
- The labelling code opens a frozen copy only by URI
  `file:…?mode=ro&immutable=1`, after checking the file's SHA-256 against its manifest. On a mismatch it refuses.
- The search pipeline reads `radar_store.DB_PATH` as a module global, so the batch builder points that
  global at the frozen file for the run, in its own process.
- A test asserts that the live `radar.sqlite3` is byte-identical before and after a batch build.

**Batches:**
- `scripts/build_label_batch.py --snapshot <stamp> --size 50..100 --seed N` runs the fixed search
  list below against the frozen copy with the current ranker, taking up to 100 results each.
- It writes `data/eval/batches/<stamp>/batch_<NNN>.json`, holding the query, the job key, the rank,
  the title, company, location and listing text shown, the ranker version and the `cv_version`.
- A batch file is never edited after it is issued. Several batches may be open at once
  ("parallel"), labelled in any order.

**Search list** (decision Q4). **Proposed, not yet confirmed by the owner.** Up to 100 results each
from the frozen index would give about 500 query–job pairs. The owner confirms the list after seeing
the result counts per search measured on the frozen copy (commit 7).

| # | Search | Kind |
|---|---|---|
| S1 | GenAI/LLM Engineer, fresher, India | target |
| S2 | AI Engineer, fresher, India | target |
| S3 | NLP Engineer, fresher, India | target |
| S4 | Sales Executive, India | control: unrelated role |
| S5 | Software Engineer, fresher, India | control: broad search |

The exact text typed into the search for each row is fixed in the batch builder (commit 8) and
recorded in every batch file, so a batch can be rebuilt from the same frozen copy.

**Labels (graded 0–3, decision Q6):**
- They live in `data/eval/labels.sqlite3`, outside the app databases (roadmap: "Labels are stored
  outside the app data").
- The table is `batch_labels(id, snapshot_stamp, batch_id, query_id, job_key, label 0–3,
  scale='graded_0_3', rubric_version, labeller, set='gold'|'batch', labelled_at, relabel_of NULL)`.
  It is append-only through the same kind of triggers.
- **Rubric `r1`** (proposed, not yet confirmed by the owner): one question, "would I apply?", answered 0–3.

  | Label | Meaning |
  |---|---|
  | 0 | A different field, or ineligible for a fresher. |
  | 1 | The same area, but the wrong role or skills; I wouldn't open it. |
  | 2 | The right family and plausible for a fresher; worth a look. |
  | 3 | The right role, fresher-friendly, matches my core skills; I would apply today. |

  `rubric_version='r1'` is stored on every label. Changing the wording creates `r2`; labels under
  different rubric versions are never pooled.
- **Batch labelling:** parallel subagents label the batches against the confirmed rubric and a
  **target profile** kept under `data/eval/` (not in git).
  - Privacy decision (owner, 2026-10-01): the owner hand-writes the target profile. It holds roles,
    fresher level, India and core skills, with no text copied from the CV, no employers and no
    contact details. Nobody else drafts it.
  - Hosted labellers may see that profile only. They never see the CV file or any text extracted
    from it (`CLAUDE.md`, hard constraints).
  - The same profile file, identified by its hash, is used for every batch and recorded with each
    label, along with `labeller='subagent:<model>'`.
- **Gold subset:** the owner labels about 100 pairs themselves (`set='gold'`), stratified across the
  five searches, **blind**: without seeing any subagent label. The subagents also label the gold
  pairs, blind to the gold labels (`set='batch'`).
- **Trust gate:** agreement on the gold subset is reported (exact agreement, and weighted Cohen's κ
  with quadratic weights, each with a bootstrap 90% interval) before any batch label is used by the
  harness. The harness refuses batch labels until an agreement report exists for their rubric version.
- After about a week, 50 gold labels are re-rated (`relabel_of` set) to measure the owner's own
  consistency (roadmap).

**UI and export (decision Q9):**
- A page in the existing web UI, shown only when `EVAL_TOOLS=true` and never in the hosted demo.
  Keys 0–3 label the pair, then it moves to the next.
- A CLI, `scripts/export_labels.py`, exports the live thumbs to `data/eval/labels/inapp_<UTC>.jsonl`
  (built in commit 6). The graded batch labels get their own export in commit 8.
- Labelling files stay under `data/eval/`, outside the app databases.

## 6. Tests to write first (per feature)

- **Migration `career_v3_events`:**
  - on a fixture copy of the *previous* schema with data: all rows kept, the backfilled events and
    late snapshots correct, `occurred_at_exact=0`, and a second run is a no-op;
  - a backup is taken; unmappable statuses are reported, not dropped;
  - the real-shape fixture (1 SAVED app) gives exactly 1 event.
- **Append-only:** `UPDATE` or `DELETE` on `job_events` and `batch_labels` raises.
- **Status cache:** after any sequence of events and undos, the cache equals the fold of the log (a
  property test over random sequences).
- **Derived states:** `PENDING_CENSORED` below 21 days, `NO_RESPONSE` at or above 21, on the day
  boundary, with a changed window setting and after `no_response_confirmed`.
- **Shortlisted:** each qualifying type counts, rejection after an interview stays true, undo flips
  it, and a reply alone is false. The rate's denominators exclude censored applications where stated.
- **One-click Applied:**
  - 403 without the exact origin; 404 for an unknown job;
  - it creates the application; the same `request_id` twice gives one event;
  - a second click gives `already_applied` and no new event;
  - future or too-old `occurred_at` is a 422; the snapshot is written in the same transaction (a
    failure rolls back both);
  - undo is idempotent, and applying again after an undo works.
- **Frontend (DOM simulation):**
  - the "Mark applied" button calls the endpoint once when double-clicked;
  - Undo appears and calls undo;
  - outcome buttons exist on tracker cards;
  - thumbs toggle and send `clear`.
- **Thumbs:**
  - origin check; latest-wins; clear; the context is stored;
  - no snapshot duplication when the job is unchanged;
  - the export JSONL is well-formed and contains no CV text.
- **Freeze:**
  - the copy's hash matches its manifest, and the file is read-only after freezing;
  - `DEMO_MODE` refuses; the live DB is unchanged;
  - an opened frozen DB rejects writes; a tampered copy (hash mismatch) is refused.
- **Batches:**
  - deterministic for the same seed and snapshot; size within 50–100;
  - an issued batch file is never rewritten;
  - labels are append-only and relabels are linked.
- **Outreach events:** `link_outreach` and `mark_outreach_sent` append their events; the existing
  outreach tests still pass.

## 7. Commit breakdown (in order, one concern each, gate green before each)

1. `feat(store): job event log, snapshots and profile versions (career_v3_events)`: schema, triggers,
   backfill, status cache; no API change.
2. `feat(tracker): new status set and derived response states`: remap, `response_window_days`
   setting, `PATCH /applications` writes events, UI status list.
3. `feat(tracker): one-click Applied with idempotency and undo`: endpoint, undo endpoint, outcome
   buttons, job-card button, frontend tests.
4. `feat(tracker): shortlisted and funnel counts`: read-only derivation and a `GET /tracker/funnel`
   endpoint, labelled L0.
5. `feat(outreach): record outreach events, and a note event when a tracked job is removed` (Q7).
6. `feat(labels): in-app thumbs up/down and label export`.
7. `feat(eval): freeze a read-only copy of the job index`.
8. `feat(eval): label rubric, batches, graded label store, labelling page and CLI export`. The search
   list is confirmed by the owner before this commit collects anything.
9. `docs: record M2 decisions in AUDIT_AND_ROADMAP.md`.

**M2b, after the core and the harness** (decision Q10): Kanban, the due follow-ups panel, the stale
banner, `.ics` export, and CSV import/export with anonymize.

**Deferred:**
- index.html uses Inter; hook flags it; revisit with the M2b UI polish.

## 8. Decisions, risks and open questions

### Decisions (owner, 2026-09-30)

| Q | Decision |
|---|---|
| Q1 | Key the event log by job (`job_events`). |
| Q2 | Fold `DISCOVERED` into `SAVED`. Drop `OUTREACH_*` as statuses; outreach is events. |
| Q3 | Add a `recruiter_reply` event. It counts as a response (ends censoring) but not as shortlisted. |
| Q4 | Batches come from the frozen index: 3 target searches (GenAI/LLM Engineer, AI Engineer, NLP Engineer) and 2 controls (Sales Executive, and a broad "Software Engineer fresher India" search), up to 100 results each (about 500 pairs). The list in §5 is proposed; the owner confirms it after the result counts from the frozen copy. |
| Q5 | No scoring refactor and no `app/core/scoring/v1.json` in M2. `ranker_version` is a code constant, `"v1"` plus the short git SHA when available, and null-safe. |
| Q6 | Two scales, never mixed: live thumbs (`thumbs`, `thumbs-v1`) and graded 0–3 batch labels (`graded_0_3`, rubric versioned). Scale and rubric version are stored on every label. The owner's gold subset is about 100 pairs, labelled blind; agreement is reported before any batch label is trusted. The graded rubric `r1` ("would I apply?", §5) is proposed and not yet confirmed. Decided: parallel subagents label the batches against the confirmed rubric and the owner's hand-written target profile (privacy decision below). |
| Q7 | A removed job with tracker history gets a `removed_from_results` note event, and all history is kept. Without history, nothing is recorded. |
| Q8 | Backdating up to 365 days; future dates are rejected. |
| Q9 | A local page in the existing web UI (keys 0–3), plus a CLI export. Labelling files stay in `data/eval/`, outside the app databases. |
| Q10 | Kanban, follow-ups, the stale banner, `.ics` and CSV import/export move to M2b, after the core and the harness. |

### Open questions

- None open on labelling privacy: it was decided on 2026-10-01 (§5, "Batch labelling"). The target
  profile file does not exist yet; the owner writes it before commit 8.
- The search list S1–S5 and rubric `r1` await the owner's confirmation (§5).
- Subagent labels never count as ground truth. They are usable only after the agreement gate passes
  for rubric `r1`, and the gold labels remain the reference.

### Risks

- **Biggest: the batch labels are only as good as the agreement gate.**
  - With about 100 gold pairs, κ has a wide interval; the report shows it.
  - Batch labels stay unused if the agreement is weak. That is the intended outcome, not a failure
    to work around.
  - The rubric version pins what each label meant.
- **Snapshot leakage (D3).** Only snapshots taken at event time are valid training inputs.
  Backfilled ones are marked `captured_late` and excluded by default.
- **You stop logging** (roadmap risk). The one-click buttons, undo and backdating reduce the cost of
  a late entry.
- **Frozen-copy searches** rely on patching `radar_store.DB_PATH`. A search takes about 7 s, so
  the 5 searches take under a minute per batch build. That is acceptable, but a future refactor of the
  global could break the builder; a test pins it.
- **Disk:** each frozen copy is about 15 MB today; C: has about 12 GB free. Keep at most a few copies,
  and never delete one that has labels.
- **Two databases:** events are in `agent.sqlite3`, removals in `alerts.sqlite3`. There is no
  cross-database transaction; §1.5 avoids needing one.

### Bigger than expected

- The status remap touches the tracker UI, the agent tools (`app/agent/tools.py` saves with
  `SAVED`), and the tests `test_tracker.py`, `test_outreach.py`, `test_alert_delete.py` and
  `test_career_store_migrations.py`.
- §5.0 asks for a `scoring_version` from a versioned scoring file. Decision Q5 defers that, so
  snapshots carry `ranker_version` instead, and M4 must add the scoring file before any model uses
  snapshots.
