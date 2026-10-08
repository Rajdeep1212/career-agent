# Career Agent: master prompt (continuous runbook)

Paste everything below the line into a fresh Claude Code session in the repo, every session.
The first session saves the queue. Each later session runs exactly one queue item, stops at its gate, and reports.

---

ACT AS
A senior AI engineer and careful maintainer of Career Agent: FastAPI + SQLite, Windows, local, free.
- Repo: C:\Users\rmaha\Downloads\carrer agent. Conda env: job-agent (Python 3.11.16).
- Work through a fixed roadmap queue, one item per session.
- Design before you build, write tests first, commit small, and report briefly.
- You never invent facts about the code; cite file paths.

CONTEXT
Career Agent is a privacy-first job agent for Indian freshers. The goal: upload a CV, name a role, and get 15–20 eligible jobs grouped as MNC / startup / remote / hybrid. Each job comes with:
- fit and reachable fit
- an honest chance range
- gaps closable in a week
- referral paths
- an interview prep plan

It learns from the user's own outcomes.

Built and verified:
- Company Radar index (~2,491 jobs), alert IMAP, bookmarklet capture.
- PDF and .docx CV parsing (bounded zip reading). Hardened attachment store (5332bf0).
- M2 tracker:
  - append-only job_events, snapshots, profile_versions
  - Applied with undo, outcomes, derived shortlisted / no-response, funnel
  - outreach events, thumbs, label export
- Frozen eval copy: data/eval/frozen/20261001T032740Z/ (sha256 08c5dc21…b2f4).
- Read-only DB open path: db.read_only(path) (c9f4f79).
- Label batch builder: scripts/build_label_batch.py (e814378).
- Freshness rule: app/services/freshness.py; search(return_excluded=True) (240872c).
- Seen-on dates, resolve_relative(), stale-sync banner (9848923).
- Patch merged (39db76f): 287 rows have a careers_url; 27 boards answer, 22 pollable by the one rule (app/sources/board_rule.py, b4aa7d1). Keka 12 + Darwinbox 11 > Greenhouse/Lever/Ashby for Indian startups; Workday 17 + SuccessFactors 8 + Eightfold 6 cover the MNCs.
- Careers-page research 2 Oct 2026: 262 rows checked, 167 verified; seeds/careers_patch_2026-10-02.csv.
- Seed list + ATS detection: scripts/detect_ats.py, docs/eval/ats_detection.md (5fbdd0a); 8 boards confirmed pollable.
- Gate: run_tests.py (999 OK, 2 skipped), `node tests/test_frontend.cjs`, ruff, mypy; then in `web/`: `npm run typecheck`, `npm test` (22 tests) and `npm run smoke` (2 Playwright tests on a disposable tracker database; needs `PLAYWRIGHT_BROWSERS_PATH=F:\playwright-browsers` and `CAREER_AGENT_PYTHON` set to the `job-agent` Python).
- Tracker cutover (TRK3b, 8 Oct 2026, narrow by Rajdeep's decision): the vanilla dashboard's Applications view, its job-card Save / Mark applied, and MCP `list_applications` use the tracker (`/api/v1`, `data/tracker.sqlite3`); the M2 tracker routes in `app/api/career.py` are gone and the funnel line is hidden until TRK8. TRK3c (8 Oct 2026): outreach linking and mark-sent and the chat's save/update tools write the tracker too, through `career_store`'s facade (a stored-job id becomes the tracker row's `job_id`); `outreach_prepared` / `outreach_sent` are tracker events, not shown to Undo. `career_applications` and the application and outreach events of `job_events` are read-only history (`career_events.append_event` refuses them); only thumbs and "removed from results" notes are still added to `job_events`. Draft links stay in `career_outreach` (migration `career_outreach_tracker_v4` dropped its foreign key to `career_applications`). A re-run of the M2 import no longer overwrites tracker rows.
- Tracker web app: `web/` talks only to `/api/v1` through its own proxy; FastAPI sends no CORS header and trusts `WEB_ORIGIN` (one exact origin) on the tracker routes only. npm's cache goes to `F:\npm-cache` (`npm_config_cache`, set per command) and Playwright's browsers to `F:\playwright-browsers`; neither goes on C:. No new web package without asking.

Decisions already made (do not reopen):
- Hosted models never see the CV file or text extracted from it. A target profile and per-project briefs written by Rajdeep may be shared.
- Labels are per JOB. Gold = all distinct jobs in the batch, labelled by Rajdeep blind. Hosted labellers are for agreement only:
  - score both orders
  - reasons before the grade
  - report exact match + quadratic weighted kappa
- Batches include up to 15 seeded samples per search from jobs the eligibility filter excluded.
- Eval searches (fixed after Q1; recorded in docs/eval/searches.md). Every string ends "in India"; saved cities are not used for eval batches:
  - A: "AI Engineer jobs for freshers in India" (AI/GenAI/LLM/NLP/DS/ML all return this one set)
  - B: "Software Engineer fresher jobs in India" (Python backend returns this same set; hits the 300-match cap)
  - C: "Data Analyst jobs for freshers in India"
  - D (control): "Sales Executive jobs in India"
- Seeded excluded samples: 10 per search for A, B and C; 5 for D. Never raise the match cap; record cap hits in meta.json instead.
- Freshness: "open today" is the main criterion. Show a job if it was verified open within 48 h (any age), or if its status is unknown and it was posted 30 days ago or less. Unknown status and 31–60 days old goes in a separate "check before applying" group. Hide closed jobs, jobs past their validThrough date, and unknown jobs older than 60 days. On the frozen copy, "today" = its snapshot date. Alert-email jobs with no posted date use the email's arrival date (shown as "seen on"). If the last sync is more than 48 h old, keep the age rule and show a banner (last sync time, hidden and check counts); a stale sync never counts as verification. Being listed on a board does not make a very old posting fresh: anything posted more than 180 days ago is flagged as a possible evergreen or ghost listing even when verified open (built in Q9/Q18).
- "Pollable" means: the board API answers, the company name matches, at least one job is in India or remote-India, and the newest posting is within 180 days. A company name that matches only after stripping a suffix ("Labs", "Technologies", "Systems", "Solutions", "India", "Pvt Ltd") is a partial match, not verified: that board is "name_mismatch". Boards failing only the last two are "stale/no-India" and are kept in the seed but excluded from the count. One rule, used by both the probe and detection.
- Source ladder, in order, per company: (1) public ATS feed, (2) JSON-LD JobPosting on the careers page, (3) a jobs sitemap or RSS/XML feed, (4) link only. Verified feed patterns (2 Oct 2026):
  - Greenhouse GET boards-api.greenhouse.io/v1/boards/{token}/jobs (content=true for descriptions)
  - Lever GET api.lever.co/v0/postings/{slug}?mode=json (epoch-ms dates)
  - Ashby GET api.ashbyhq.com/posting-api/job-board/{name}
  - SmartRecruiters GET api.smartrecruiters.com/v1/companies/{id}/postings (limit/offset)
  - Workable GET apply.workable.com/api/v1/widget/accounts/{account}
  - Recruitee GET {company}.recruitee.com/api/offers/
  - Keka GET {tenant}.keka.com/careers/api/jobs/default/active - returns publishedOn + publishedSinceDays; checked live on disprz (6 jobs) and gokwik (10 jobs)
  - Workday POST {tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs, body {"appliedFacets":{},"limit":20,"offset":0,"searchText":""}. limit must be 20 (a larger limit returns zero with no error); postedOn is a display string, so take startDate from the detail endpoint.
  - Darwinbox: no public feed; the portal is {tenant}.darwinbox.in/ms/candidate/careers. Treat as link-only until proven otherwise.
  - SuccessFactors and Eightfold: no public cross-tenant API. Use JSON-LD or an RSS/XML feed when the career site exposes one.
- Ghost-listing evidence: a LiftmyCV study of 100,000 job IDs (Jan 2026) found 40%+ of listings had no human interaction for 30+ days; other 2026 reporting puts ghost listings near 1 in 4 on LinkedIn. This is why freshness and last_seen are gates, not decorations.
- Embeddings (decided 3 Oct 2026): no PyTorch. The embedding backend is FastEmbed on ONNX Runtime, default model BAAI/bge-small-en-v1.5 (384-dim, quantised), run locally. Reason: C: has about 4 GB free and a torch install would take most of it. The preflight (scripts/check_env.py, docs/eval/env_check.md) needs RAM total >= 7.5 GB, cache drive free >= 8 GB and repo drive free >= 2 GB; free RAM is a runtime guard in the embedding script (about 1 GB), not a preflight gate. If C: drops below 2 GB free, stop and tell Rajdeep. Design input: docs/SEMANTIC_MATCHING_DESIGN.md.
- Similarity-only ranking (BM25, dense, hybrid) is worse than v1 on the gold labels (SEM2); any semantic ranker must sit behind the eligibility gate.
- Tracker (docs/TRACKER_PLAN.md, approved 3 Oct 2026): one tracker, never two. TRK1 imports the M2 tracker into data/tracker.sqlite3 with an idempotent, re-runnable import that is run once more at the TRK2 cutover; TRK2 retires the old routes.
- Tracker storage: everything runs locally on SQLite; Postgres is used only in migration tests until Rajdeep decides where it runs, before TRK4. Docker's data root is on E:\DOCKER DATA\DockerDesktopWSL, which is approved: the requirement is "not on C:". An agent never starts, reconfigures or moves Docker; when the daemon is not running, the Postgres tests are skipped and the report says so.
- Tracker email: Resend is decided at TRK5 after checking its limits; the fallback is the Gmail gmail.send grant behind the same approval boundary. IMAP on the apply inbox is a constraint change decided at TRK7, not before.
- Tracker privacy: mentors see the pipeline and analytics only. CV versions store only label, date, file hash and the local parser's skill list.
- MCP: the three read-only tools stay as they are; track_application is a separate, explicitly enabled tool at TRK6.
- Public demo (docs/DEPLOY_PLAN.md, approved 8 Oct 2026): a read-only demo at ₹0 with no card, separate from the laptop; real user data never leaves the laptop.
  - Web: `web/` as a Next.js static export on Cloudflare; the browser calls the API directly, with CORS for that one exact origin. OpenNext is the fallback only.
  - API: FastAPI in Docker on Render Free with DEMO_MODE; no embeddings in the image; no sign-in.
  - Data: synthetic tracker rows on Supabase Postgres, with the SQLite fallback built first; the public snapshot holds radar index rows only.
  - No Gemini in the demo. No Supabase Auth: TRK4 stays as planned.
  - Rajdeep creates the accounts, checks for a card at sign-up and stops if one is asked for. An agent never signs up.
  - `psycopg[binary]` is approved for DEPLOY1. How Render forwards the client address is verified in DEPLOY3 before the rate limit trusts a header.
  - Do not rely on a workflow's own commits to keep the daily schedule alive.
- No scraping of LinkedIn, Naukri, Indeed, Wellfound, Foundit, Instahyre, Internshala, GeeksforGeeks, LeetCode, Glassdoor or AmbitionBox. Use only pre-filled links, the user's own exports, and pages the user opens (bookmarklet).
- No auto-apply. Nothing is sent, applied or deleted without Rajdeep's approval.
- Interview features are practice-only. Never assist during a live interview or test.
- Openings count:
  - show it only when a page states it (schema.org totalJobOpenings, or a page the user captured); otherwise show "not stated"
  - show applicants-per-opening only when both numbers are known
- Company seed list: 356 companies with source_url per row. Rajdeep will place it at seeds/companies_seed.csv. Columns: company, list_type, category, national_top, city_group, city, sector, why_listed, source_url, careers_url, ats.
- Multi-agent design (when built):
  - Named agents over shared SQLite state plus the event log: Scout, Reader, Gatekeeper, Matcher, Verifier, Writer, Critic, Coach, Analyst.
  - A LangGraph state machine orchestrates them.
  - Every agent has a step and token budget.
  - Human approval gates stay in place.
  - The multi-agent setup must beat the single-agent version on a measured ablation, or stay simpler.

REQUEST
1. **Start-of-session checks.**
   - `git status` must be clean and HEAD must equal origin/main.
   - Nothing else is writing in this repo.
   - Record sha256 (first 16 hex) of data/agent.sqlite3 and the radar DB.
   - Also record sha256 (first 16 hex) of data/tracker.sqlite3 when it exists. It changes only through tracker actions (imports, the tracker API, and since TRK3c the chat's save/update tools and outreach prepare/send); any other change is a failure.
   - If any check fails, STOP and report.
2. **First run only.** If docs/ROADMAP_QUEUE.md does not exist:
   - Create it from QUEUE below, verbatim, with a Status column.
   - Commit it as a docs-only commit, push, report, and STOP.
3. **Pick the item.** Otherwise, take the first item whose Status is not DONE.
   - If its YOU condition is not met, STOP and tell me exactly what you need.
   - If the item says PLAN, write only the named plan doc, commit, report and STOP.
4. **Plan, then build.**
   - Post a 5–10 line plan in chat, then implement.
   - Small commits; tests first (show the red run).
   - Run the full gate before every commit.
   - Write only inside this repo.
   - Dev agents (`.claude/agents/`) are read-only helpers and the main session is the only writer: use `researcher` to find where something lives before changing it, `test-writer` to draft failing tests for a described change, and `reviewer` on the diff before a commit that touches storage, network code, email or anything near the CV.
5. **Close the item.**
   - Set its Status to DONE with one line: result plus commit SHAs.
   - Commit, push, report, and STOP.
   - Never start a second item in the same session.

TERMS
- Never print .env contents.
- Do not run git restore, checkout, reset, stash or clean.
- No global git or conda changes. Don't touch Rajdeep_Job_Agent. Don't use task-observer.
- Live agent.sqlite3 and the radar DB must be byte-identical before and after, unless the item explicitly includes a migration via the existing backup + one-time-apply machinery.
- No new paid APIs. No heavy dependencies without asking.
- Tests use recorded fixtures, never the live network.
- Network code:
  - polite (robots.txt, a delay per host, caching, stop on 429)
  - attribution kept for Adzuna, Remotive, Himalayas, RemoteOK
- Leave Inter and the design hook alone. Tell me only if the hook BLOCKS a commit.
- Never invent facts. If unclear, STOP and ask.

OUTPUT (short chat report)
- Queue item id and title. Commits (SHA + one line each).
- Gate results. DB hashes before and after.
- What the gate measured: numbers, not adjectives.
- Anything the results argue against. What you need from me next.
- Then STOP.

QUEUE

| ID | Item | Gate | YOU |
|---|---|---|---|
| Q1 | M2-8a: read-only open path in app/storage/db.py (sqlite URI mode=ro, no WAL or writing pragmas); tests; then run S1–S6 read-only on the frozen copy and report counts + pairwise overlap table + distinct-job total | Frozen copy still matches manifest; hashes unchanged | – |
| Q2 | M2-8b: scripts/build_label_batch.py: per-job lines, meta.json (manifest sha, profile version, preferences hash, ranker_version, rubric r1, seed, surfacing searches, pre/post-filter), blind view, seeded order, refuses overwrite; tests on a temp DB | Tests green | Search list confirmed (A–D) |
| Q2b | Freshness: date-distribution report, freshness(job, today) rule in search and batch builder, explicit return_excluded hook, batch dry run | Tests green; new batch size reported | – |
| Q2b2 | Freshness follow-up: alert-email arrival date as "seen on" date; relative "posted N days ago" parsing; stale-sync banner; excluded samples 10/10/10/5; batch dry run | Tests green; new batch size | – |
| Q2c | Company list detection only: validate seeds/companies_seed.csv, scripts/detect_ats.py (polite, cached, fixtures), docs/eval/ats_detection.md | Report; hashes unchanged | Copy the CSV into seeds/ |
| Q2c2 | Find careers boards for rows without careers_url: probe Greenhouse/Lever/Ashby/SmartRecruiters/Workable by candidate slug with name verification; review CSV; HTML fallback for live boards whose API 404s; Postman/PhonePe fixes | Review file + new pollable count | Review the "probable" matches |
| Q2c3 | Merge careers_patch_2026-10-02.csv into the seed (fill empty careers_url only; keep the 8 API-confirmed boards; keep job-boards.greenhouse.io spelling); re-run detection on new pages only | New pollable count vs 20 | – |
| Q2c4 | One pollable rule in app/sources/ (India + 180-day freshness + name match), used by probe and detection; apply it to the 15 probable rows automatically; re-report counts | Counts by rule; tests | – |
| Q2d | Dev agents + read-only MCP: .claude/agents reviewer, test-writer, researcher (read-only; main session is the only writer); MCP server (stdio) with search_jobs, explain_fit, list_applications via db.read_only; no CV text in any tool output | Server answers from Claude Desktop; tests | Add the server to Claude Desktop config |
| Q3 | M2-8c: generate the real batch; local labelling page (keyboard 0–3, blind, resumable, writes data/eval/labels/) | Batch built; page works on 5 test jobs | Write data/eval/target_profile.md yourself |
| Q4 | M2-8d: hosted batch labels on the same jobs (both orders, reasons first); agreement report: exact, quadratic weighted kappa, confusion matrix | Report in docs/eval/ | Finish labelling all jobs in the page |
| Q5 | M2-9: docs: M2_PLAN.md final, README "Evaluation" section with the numbers | Docs only | – |
| Q6 | Security: triage the Claude Security report Rajdeep pastes; apply chosen patches one per commit with a regression test each | Full gate per patch | Run /claude-security scan and paste the findings |
| Q7 | PLAN: docs/M3_PLAN.md: company universe, ATS detection, adapters (Greenhouse, Lever, Ashby first), aggregators, requirement-card JSON schema, ESCO normalisation, three-way eligibility with evidence, JD keyword engine, data model + migrations, commit plan, open questions with recommendations | Plan doc only | Approve the plan |
| Q8 | M3a: import seeds/companies_seed.csv (validate 356 rows, keep source_url), companies table, ATS detection script producing a report (no polling yet) | Detection report; tests | Copy the CSV into seeds/ |
| Q9 | M3b: Greenhouse, Lever, Ashby adapters + scheduled polite polling + diff by job id + attribution; recorded fixtures | New jobs counted per source; tests | – |
| Q10 | M3c: requirement cards (JSON schema, JD-only LLM extraction, validation), ESCO skill ids, three-way eligibility with quoted evidence, why-not list | Every stored job has a card; eligible AI fresher jobs N reported vs 2 | – |
| Q11 | M3d: JD keyword engine: skill share per role family over 30 days, must-have weight 2, min 20 postings | Top-25 table per role family | – |
| Q12 | PLAN: docs/M4_PLAN.md including the multi-agent graph (roles, state, budgets) and the ablation design | Plan doc only | Approve |
| Q13 | M4a: chat shortlist (upload CV, name role, 15–20 jobs in four buckets, why-not list) | Ranker vs v1 on M2 labels: NDCG@10 | – |
| Q14 | M4b: gap closer (Microsoft Learn API + courses.yaml; learn/prove/show) and local CV + project highlighter (green/amber/red, deterministic) | Highlighter tests on fixture CVs | Write project briefs in data/eval/projects/ |
| Q15 | M4c: new-role alerts (30–60 min polling of watchlist, gate + score, Telegram or ntfy push) + apply kit + referral draft from Connections.csv | Alert latency measured | Provide Telegram bot token in .env |
| Q16 | M5a: interview coach: company playbooks (source_type, last_verified), question banks 30–40 per skill (labelled generated, keep/drop), project deep-dive bank, timed drills, mock rounds with rubric, debrief bank; triggered by online_test/interview events | Playbook opens on a test event; rubric tests | – |
| Q17 | M5b: weekly review, follow-up queue, calibrated chance range (prior first; logistic + isotonic when ~30 outcomes) | Reliability plot or "too early" message | – |
| Q18 | M6: ghost/scam filter, name-swap bias audit, MCP server (search_jobs, explain_fit, get_playbook, track_application), README with measured results, demo | Fresh-clone install from README works | Record the demo |
