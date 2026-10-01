# Roadmap queue

One item per session, in order. Take the first item whose Status is not DONE; stop at its gate.
When an item closes, set its Status to `DONE` followed by one line: the result and the commit SHAs.

| ID | Item | Gate | YOU | Status |
|---|---|---|---|---|
| Q1 | M2-8a: read-only open path in app/storage/db.py (sqlite URI mode=ro, no WAL or writing pragmas); tests; then run S1–S6 read-only on the frozen copy and report counts + pairwise overlap table + distinct-job total | Frozen copy still matches manifest; hashes unchanged | – | DONE: `db.read_only()` added (`c9f4f79`); S1–S6 on the frozen copy give 112 distinct jobs after the eligibility check, 512 before; strings and tables in docs/eval/searches.md |
| Q2 | M2-8b: scripts/build_label_batch.py: per-job lines, meta.json (manifest sha, profile version, preferences hash, ranker_version, rubric r1, seed, surfacing searches, pre/post-filter), blind view, seeded order, refuses overwrite; tests on a temp DB | Tests green | Confirm the search list after Q1 counts | DONE: search list A–D fixed in docs/eval/searches.md; `scripts/build_label_batch.py` with 11 tests (721 green); dry run on the frozen copy expects 160 distinct jobs (105 shown + 55 excluded samples). Commits: see `git log --grep "Q2"` |
| Q3 | M2-8c: generate the real batch; local labelling page (keyboard 0–3, blind, resumable, writes data/eval/labels/) | Batch built; page works on 5 test jobs | Write data/eval/target_profile.md yourself | TODO |
| Q4 | M2-8d: hosted batch labels on the same jobs (both orders, reasons first); agreement report: exact, quadratic weighted kappa, confusion matrix | Report in docs/eval/ | Finish labelling all jobs in the page | TODO |
| Q5 | M2-9: docs: M2_PLAN.md final, README "Evaluation" section with the numbers | Docs only | – | TODO |
| Q6 | Security: triage the Claude Security report Rajdeep pastes; apply chosen patches one per commit with a regression test each | Full gate per patch | Run /claude-security scan and paste the findings | TODO |
| Q7 | PLAN: docs/M3_PLAN.md: company universe, ATS detection, adapters (Greenhouse, Lever, Ashby first), aggregators, requirement-card JSON schema, ESCO normalisation, three-way eligibility with evidence, JD keyword engine, data model + migrations, commit plan, open questions with recommendations | Plan doc only | Approve the plan | TODO |
| Q8 | M3a: import seeds/companies_seed.csv (validate 356 rows, keep source_url), companies table, ATS detection script producing a report (no polling yet) | Detection report; tests | Copy the CSV into seeds/ | TODO |
| Q9 | M3b: Greenhouse, Lever, Ashby adapters + scheduled polite polling + diff by job id + attribution; recorded fixtures | New jobs counted per source; tests | – | TODO |
| Q10 | M3c: requirement cards (JSON schema, JD-only LLM extraction, validation), ESCO skill ids, three-way eligibility with quoted evidence, why-not list | Every stored job has a card; eligible AI fresher jobs N reported vs 2 | – | TODO |
| Q11 | M3d: JD keyword engine: skill share per role family over 30 days, must-have weight 2, min 20 postings | Top-25 table per role family | – | TODO |
| Q12 | PLAN: docs/M4_PLAN.md including the multi-agent graph (roles, state, budgets) and the ablation design | Plan doc only | Approve | TODO |
| Q13 | M4a: chat shortlist (upload CV, name role, 15–20 jobs in four buckets, why-not list) | Ranker vs v1 on M2 labels: NDCG@10 | – | TODO |
| Q14 | M4b: gap closer (Microsoft Learn API + courses.yaml; learn/prove/show) and local CV + project highlighter (green/amber/red, deterministic) | Highlighter tests on fixture CVs | Write project briefs in data/eval/projects/ | TODO |
| Q15 | M4c: new-role alerts (30–60 min polling of watchlist, gate + score, Telegram or ntfy push) + apply kit + referral draft from Connections.csv | Alert latency measured | Provide Telegram bot token in .env | TODO |
| Q16 | M5a: interview coach: company playbooks (source_type, last_verified), question banks 30–40 per skill (labelled generated, keep/drop), project deep-dive bank, timed drills, mock rounds with rubric, debrief bank; triggered by online_test/interview events | Playbook opens on a test event; rubric tests | – | TODO |
| Q17 | M5b: weekly review, follow-up queue, calibrated chance range (prior first; logistic + isotonic when ~30 outcomes) | Reliability plot or "too early" message | – | TODO |
| Q18 | M6: ghost/scam filter, name-swap bias audit, MCP server (search_jobs, explain_fit, get_playbook, track_application), README with measured results, demo | Fresh-clone install from README works | Record the demo | TODO |
