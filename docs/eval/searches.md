# Evaluation searches

The query strings below are fixed: reuse them character for character. Which of them make up the
labelling search list is still the owner's decision (docs/ROADMAP_QUEUE.md Q2).

## How they are run

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

## S1–S6

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

## Alternative strings

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
