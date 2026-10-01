# Evaluation searches

## Search list (final, confirmed by the owner on 2026-10-01)

Use these strings character for character. They replace S1–S6 below.

| ID | Query string | Covers | Seeded excluded samples |
|---|---|---|---|
| A | `AI Engineer jobs for freshers in India` | target: AI / GenAI / LLM / NLP / Data Scientist / ML family | 10 |
| B | `Software Engineer fresher jobs in India` | target: software and Python backend family | 10 |
| C | `Data Analyst jobs for freshers in India` | target: Data Analyst | 10 |
| D | `Sales Executive jobs in India` | control | 5 |

Rules:

- "in India" stays in every string. Saved cities are never used for evaluation batches; the batch
  builder refuses a search whose location is not exactly `['India']`.
- The 300-match cap is not raised. `meta.json` records, per search, the match count, whether the cap
  was hit, and the date of the oldest included posting.
- A search with few results is kept, not swapped. C returns 3 jobs after the eligibility check.
- Labels are per job. Each batch also takes a seeded sample of the jobs each search's eligibility
  check excluded, so the labels can judge the filter.

- Freshness (Q2b, `app/services/freshness.py`) is judged on the day the copy was frozen, in India time
  (2026-10-01). Jobs it hides as stale or closed are not labelled; the excluded sample is drawn only
  from fresh jobs the eligibility check excluded.

Counts on the frozen copy with the freshness rule (dry run of `scripts/build_label_batch.py`, seed
20261001). No job in the copy was listed within 48 hours of the snapshot (all were last listed on
2026-09-28), so age alone decides:

| ID | Index matches | Cap hit | Oldest match | Hidden as stale | Excluded as ineligible | Results | of which "check before applying" | Excluded sampled |
|---|---|---|---|---|---|---|---|---|
| A | 100 | no | 2020-03-13 | 41 | 46 | 13 | 6 | 10 |
| B | 300 | yes | 2026-03-10 | 74 | 187 | 39 | 12 | 10 |
| C | 25 | no | 2026-01-06 | 5 | 17 | 3 | 0 | 10 |
| D | 112 | no | 2023-12-12 | 42 | 54 | 16 | 9 | 5 |

Expected batch: **106 distinct jobs** = 71 shown + 35 seeded excluded samples (Q2b2; it was 126 with
samples of 15/15/15/10).

The copy's last sync (2026-09-28) is more than 48 hours before its snapshot day, so a live search
would show the stale-sync notice. If the last sync day counted as confirmed open instead, nothing
would be hidden as stale and the batch would be 140 jobs (105 shown + 35 samples).

Decision (Rajdeep, 2026-10-01): a stale sync is announced only. The age rule keeps hiding and
grouping jobs exactly as above and the banner reports the last sync time with the hidden and check
counts; a stale sync never counts as verification. The batch stays at 106 jobs.

Before the freshness rule (same dry run at `e814378`):

| ID | Index matches | Cap hit | Oldest included posting | Excluded as ineligible | Results | Excluded sampled |
|---|---|---|---|---|---|---|
| A | 100 | no | 2020-03-13 | 75 | 25 | 15 |
| B | 300 | yes | 2026-03-10 | 251 | 49 | 15 |
| C | 25 | no | 2026-01-06 | 22 | 3 | 15 |
| D | 112 | no | 2023-12-12 | 84 | 28 | 10 |

That batch would have been 160 distinct jobs = 105 shown after the eligibility check + 55 seeded excluded samples.

**Finding (recorded, not fixed now):** every AI-family string (AI, GenAI, LLM, NLP, Data Scientist,
Machine Learning) returns one identical set of jobs, and "Python backend developer" returns the same
set as "Software Engineer". The index matches on role family and does not tell sub-roles apart. The
M3 requirement cards address this.

## History: the Q1 count run

Kept as a record of how the list above was chosen. The strings in this section are no longer the
search list.

### How they were run

- Index: `data/eval/frozen/20261001T032740Z/radar.sqlite3`
  (sha256 `08c5dc21420df1330f01321c98d25b5c213f3f1577e8729c15a377682825b2f4`), opened with
  `db.read_only()` (app/storage/db.py). It matched its manifest before and after the run.
- Path: `CareerAgent(providers=[RadarProvider()]).search(query, include_seen=True)`, network guards
  on, 0 provider requests. The search's own writes (session, history, cache) go to scratch databases.
- No other filter is passed. `search()` takes no location or experience argument: the location comes
  from the query text ("in India" gives `['India']`), or, when the text names none, from the saved
  preferences (8 entries: Kolkata, Chennai, Bengaluru, Noida, Hyderabad, Pune, Gurugram, Remote India).
  `experience_max` was `None` and the minimum match score 0 in every search. There is no
  "experience 0–1 yr" filter; experience is handled by the eligibility check.
- Results depend on the saved profile and preferences at run time (2026-10-01, code at `c9f4f79`).
- "Index matches" is the count before the eligibility check, "results" the count after it. The
  Radar provider returns at most 300 matches; the app shows at most 50 results.

### S1–S6

S1–S5 are the strings of the first count run (commit `6b789bb` report); S6 is new.

| ID | Query string | Role family | Location filter | Index matches | Excluded as ineligible | Results | Eligible / uncertain |
|---|---|---|---|---|---|---|---|
| S1 | `GenAI LLM Engineer jobs for freshers in India` | ai | India | 100 | 75 | 25 | 2 / 23 |
| S2 | `AI Engineer jobs for freshers in India` | ai | India | 100 | 75 | 25 | 2 / 23 |
| S3 | `NLP Engineer jobs for freshers in India` | ai | India | 100 | 75 | 25 | 2 / 23 |
| S4 | `Sales Executive jobs in India` | sales | India | 112 | 84 | 28 | 1 / 27 |
| S5 | `Software Engineer fresher jobs in India` | software | India | 300 (cap) | 251 | 49 | 5 / 44 |
| S6 | `Sales Executive fresher` | sales | saved preferences | 85 | 54 | 31 | 2 / 29 |

Jobs shared by each pair, after the eligibility check (diagonal = the search's own results):

| | S1 | S2 | S3 | S4 | S5 | S6 |
|---|---|---|---|---|---|---|
| S1 | 25 | 25 | 25 | 0 | 0 | 0 |
| S2 | 25 | 25 | 25 | 0 | 0 | 0 |
| S3 | 25 | 25 | 25 | 0 | 0 | 0 |
| S4 | 0 | 0 | 0 | 28 | 0 | 21 |
| S5 | 0 | 0 | 0 | 0 | 49 | 0 |
| S6 | 0 | 0 | 0 | 21 | 0 | 31 |

Before the eligibility check:

| | S1 | S2 | S3 | S4 | S5 | S6 |
|---|---|---|---|---|---|---|
| S1 | 100 | 100 | 100 | 0 | 0 | 0 |
| S2 | 100 | 100 | 100 | 0 | 0 | 0 |
| S3 | 100 | 100 | 100 | 0 | 0 | 0 |
| S4 | 0 | 0 | 0 | 112 | 0 | 85 |
| S5 | 0 | 0 | 0 | 0 | 300 | 0 |
| S6 | 0 | 0 | 0 | 85 | 0 | 85 |

Distinct jobs across S1–S6: **112** after the eligibility check (74 without the two sales controls),
**512** before it.

### Alternative strings

Run in the same session because S1–S5 above do not cover Data Analyst, Data Scientist or backend
roles. None names a location, so all use the saved-preference locations.

| ID | Query string | Role family | Index matches | Excluded as ineligible | Results | Eligible / uncertain |
|---|---|---|---|---|---|---|
| F1a | `AI engineer fresher` | ai | 87 | 68 | 19 | 2 / 17 |
| F1b | `GenAI engineer fresher` | ai | 87 | 68 | 19 | 2 / 17 |
| F1c | `LLM engineer fresher` | ai | 87 | 68 | 19 | 2 / 17 |
| F2 | `Data Analyst fresher` | analytics | 21 | 18 | 3 | 0 / 3 |
| F3a | `Data Scientist fresher` | ai | 87 | 68 | 19 | 2 / 17 |
| F3b | `Machine Learning engineer fresher` | ai | 87 | 68 | 19 | 2 / 17 |
| F4 | `Python backend developer fresher` | software | 300 (cap) | 256 | 44 | 5 / 39 |
| F5 | `Software Engineer fresher` | software | 300 (cap) | 256 | 44 | 5 / 39 |

- F1a, F1b, F1c, F3a and F3b return the same 19 jobs (the same 87 before the check); their union is 19.
- F4 and F5 return the same 44 jobs (the same 300 before the check).
- F2 shares no job with any other string.
- Distinct jobs across the eight: **66** after the eligibility check, **408** before it.
