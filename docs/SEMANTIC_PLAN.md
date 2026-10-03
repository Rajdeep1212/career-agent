# SEM1 — Semantic ranking plan (plan only)

Status: plan, 3 October 2026. Nothing here is built. Design input: `docs/SEMANTIC_MATCHING_DESIGN.md`. Decisions not to
reopen: no PyTorch; embeddings run locally with FastEmbed on ONNX Runtime and `BAAI/bge-small-en-v1.5`; hosted models
never see the CV or text extracted from it.

## 1. The problem, from Q4

Measured on batch `gold-20261001-r1` (106 jobs) against Rajdeep's run-3 gold labels (`docs/eval/agreement_gold-20261001-r1.md`):

| Search | NDCG@10 of the current ranker (v1), shown jobs | NDCG@10, judged pool |
|---|---|---|
| A, "AI Engineer jobs for freshers in India" | 0.735 | 0.708 |
| B, "Software Engineer fresher jobs in India" | 0.660 | 0.557 |

- Agreement with the hosted labels is moderate: quadratic weighted kappa 0.498. In every disagreement Rajdeep's grade is
  the higher one. The gap is eligibility (years required, seniority, GPA or batch cutoffs, "students only"), not topic:
  the hosted labeller and Rajdeep mostly agree on what a job is about and disagree on whether a 2025 fresher can get it.
- Only 14 of 106 jobs are grade 3. However good the ranker, the index holds few jobs worth applying to (section 7).

Two consequences shape this plan. A similarity score cannot see eligibility: "Senior AI Engineer, 8+ years" is close
to the profile in meaning and wrong for the candidate. And with 23 and 49 judged jobs per search, any measured gain
will have a wide interval, so the decision rule is written down before anything runs.

## 2. Two scores, never blended

- **Relevance**: does the job match what the candidate does? A rank or score from text matching (section 3).
- **Eligibility**: can a 2025 fresher with 0 years get it? A tier with quoted evidence (section 4).

They are never added or multiplied into one similarity number. The combined ranker sorts by eligibility tier first,
then by relevance within a tier (a lexicographic gate). That needs no weight, so nothing has to be tuned:

| Tier | Meaning | Order |
|---|---|---|
| eligible | positive evidence, nothing excluding | first |
| uncertain | no explicit disqualifier, but missing or ambiguous evidence | second |
| excluded | an explicit disqualifier quoted from the listing | last (kept, not deleted, so it can still be measured) |

Both scores are shown separately to the user, each with its claim level: relevance is L0 (a deterministic text
score); eligibility is L0 with the quoted sentence.

## 3. Relevance candidates (local, free)

All three read the same text and the same query, fixed in advance:

- **Job text**: title, then the description, cut at 2,000 characters. 36 of the 106 jobs have under 200 characters of
  description (mostly title-only listings); for them relevance rests on the title.
- **Query**: the target profile (`data/eval/target_profile.md`) without its heading line, the same text the hosted
  labeller saw. The search string only decides which jobs are in the pool; it is not part of the query. Never the CV.

Candidates:

1. **BM25** with `rank_bm25` (`BM25Okapi`, library defaults k1 = 1.5, b = 0.75). Tokens: lower-case runs of letters,
   digits, `+` and `#`; no stemming, no stop list.
2. **Dense** with FastEmbed and `BAAI/bge-small-en-v1.5` (384 dimensions, quantised ONNX). Profile through
   `query_embed`, jobs through `passage_embed`; cosine similarity. Model cache under `F:\huggingface` (HF_HOME is already
   set there). The embedding script refuses to start with under 1 GB of free RAM (the runtime guard decided in SEM0).
3. **Hybrid**: reciprocal rank fusion of 1 and 2, score = 1/(60 + rank in BM25) + 1/(60 + rank in dense). k = 60 is fixed
   now and is not tuned.

Vectors for the evaluation live in `data/eval/vectors/gold-20261001-r1.npz` (job item ids, model name, vectors). For
the app later, a file beside the index, `data/radar_vectors.npz`, keyed by job key and model; never in `agent.sqlite3`.

## 4. Eligibility score (no ML model)

Extend `app/services/eligibility.py`, keeping its three-way `EligibilityResult` and quoted evidence. New detectors,
each returning the sentence it matched:

| Detector | Excludes when the listing says | Uncertain when |
|---|---|---|
| Years required | a minimum of 2 or more years with no fresher wording ("2-4 years", "5+ years", "Bachelors + 2 years") | a range starting at 1 ("1-3 years") |
| Seniority | Staff, Senior Staff, Principal, Lead, Manager, AVP, Vice President, Director, Head of (title or role text) | "Senior" alone in the title with no years stated |
| Batch or GPA cutoff | a graduation window that excludes 2025, a GPA or CGPA floor above the profile's 7.64 | a cutoff that cannot be read (no number) |
| Students only | "final year students", "currently enrolled", returnships for people on a career break | internship with no eligibility sentence |

The profile facts used (graduation year 2025, 0 years, CGPA 7.64) come from the target profile, which Rajdeep wrote;
none comes from the CV. Every exclusion quotes the listing, as the eligibility model already requires.

## 5. The experiment

Five rankers, each ordering the same candidate set:

| Ranker | Order |
|---|---|
| v1 | the current ranker: the shown jobs in the order the search showed them, then the sampled excluded jobs by v1's own `total_score` |
| BM25 | section 3, candidate 1 |
| Dense | section 3, candidate 2 |
| Hybrid | section 3, candidate 3 |
| Hybrid × eligibility | eligibility tier first, then hybrid score (section 2) |

- **Candidate set**: each search's judged pool, the only jobs with labels. A: 23 jobs (13 shown, 10 sampled excluded).
  B: 49 jobs (39 shown, 10 sampled excluded). Rankers may not surface jobs outside the pool, because they cannot be scored.
- **Metrics**: NDCG@10 (gain 2^grade − 1, log2 discount, ideal order from the pool) and Precision@5 with "relevant"
  meaning grade 2 or 3, fixed now.
- **Labels**: Rajdeep's run-3 gold labels, and separately the hosted labels (batch order). Hosted labels are never used
  to tune anything.
- **Intervals**: bootstrap over jobs, 1,000 resamples with seed 20261003, stratified by search; each resample re-scores
  every ranker on the same resampled jobs, and the paired difference to v1 gives a 95% percentile interval.
- **No tuning on these 106 jobs**: every number above (k = 60, BM25 defaults, 2,000 characters, the tier order, the
  relevant threshold) is fixed in this document. If a run shows a bug, the fix is recorded and the run repeated in full.
- **Output**: `docs/eval/semantic_eval_gold-20261001-r1.md`, one table per label set and search, every ranker, point
  estimate and interval, plus the decision.

## 6. Decision rule (written before running)

A ranker ships only if all of these hold:

1. On the gold labels, its NDCG@10 minus v1's, averaged over A and B, has a 95% bootstrap interval entirely above zero.
2. On the hosted labels, the same averaged difference is not below zero (point estimate at least 0).
3. It is the primary candidate, hybrid × eligibility. The other three are reported for understanding and cannot ship from
   this run, so four comparisons do not get four chances to pass by luck.

If it fails, v1 stays and the report says so. Precision@5 is reported but does not decide.

With 23 and 49 jobs the intervals will be wide. The likeliest honest outcome is "not shown to be better", which is a
result, not a reason to tune.

## 7. What a better ranker cannot fix (the ceiling)

Grade-3 and grade-2-or-3 jobs in each judged pool, gold labels:

| Search | Pool | Grade 3 | Of which shown by v1 | Grade 2 or 3 | Hosted grade 3 |
|---|---|---|---|---|---|
| A | 23 | 10 | 9 | 15 | 1 |
| B | 49 | 4 | 2 | 25 | 0 |
| C | 13 | 0 | 0 | 1 | 0 |
| D (control) | 21 | 0 | 0 | 0 | 0 |

- In A, a perfect ranker could fill the top 10 with grade-3 jobs. v1 already shows 9 of the 10.
- In B, at most 4 of the top 10 can be grade 3 however the 49 jobs are ordered; at most 4 of the top 5.
- The pool is a sample. A matched 100 index jobs and B 300; only 23 and 49 are judged, so a ranker that would surface a
  good unjudged job gets no credit. Widening the pool means labelling more jobs, not changing the ranker.
- Across the batch, 14 of 106 jobs are grade 3. Finding more of them is a sourcing problem (Q9 adapters, Keka and
  Darwinbox boards), not a ranking one.

## 8. Install footprint

Measured from PyPI and Hugging Face metadata on 3 October 2026; nothing has been installed. Download sizes are exact;
installed sizes will be measured at install time.

| Package (none is in `job-agent` today) | Version available for Python 3.11, Windows | Wheel |
|---|---|---|
| fastembed | 0.8.1 | 0.13 MB |
| onnxruntime | 1.30.0 | 14.3 MB |
| numpy | 2.4.6 | 12.6 MB |
| pillow (a fastembed dependency) | 12.3.0 | 7.2 MB |
| tokenizers | 0.23.2 | 2.9 MB |
| huggingface-hub | below 2.0, as fastembed requires | about 0.8 MB |
| py-rust-stemmers, mmh3, loguru, tqdm | | 0.4 MB together |
| rank-bm25 | 0.2.2 | 0.01 MB |
| **Total download** | | **about 38 MB** |

- Model `Qdrant/bge-small-en-v1.5-onnx-Q` (what FastEmbed serves for `BAAI/bge-small-en-v1.5`; to be confirmed at install
  with `TextEmbedding.list_supported_models()`): `model_optimized.onnx` 66.5 MB plus tokenizer files, on F:.
- On C: (3.9 GB free at the last check): the conda env lives there. Unpacked wheels are usually two to three times their
  download size, so an estimated 0.1 to 0.15 GB; install with `pip install --no-cache-dir` so no copy stays in the pip
  cache on C:. `scripts/check_env.py` runs before and after, and installing stops if C: would fall under 2 GB.
- `requests` is already installed; nothing else above is.

## 9. Commit plan (for the build item, not this one)

1. `scripts/check_env.py` before; install the packages above (with approval: new dependencies); `check_env.py` after,
   with the measured installed size recorded in `docs/eval/env_check.md`.
2. `feat(eval)`: BM25 and RRF as plain functions, tests on fixture text.
3. `feat(eval)`: FastEmbed wrapper with the 1 GB free-RAM guard and the vector file; tests with a stub embedder (no model).
4. `feat(eligibility)`: the four detectors, test-first, one regression test per detector built from fictional listings.
5. `feat(eval)`: the experiment script, bootstrap and report; tests on a 5-job fixture with known metrics.
6. `docs(eval)`: run it once, commit the report and the decision.

## 10. Open questions, each with a recommendation

1. **The eligibility detectors were designed after reading the 16 Q4 disagreements.** That makes these 106 jobs a weak
   test for the eligibility part. Recommendation: before shipping anything, freeze a new index copy, build a fresh batch
   of about 60 jobs, label it with the guard, and apply the decision rule there; use the 106 for development only.
2. **Embed requirement cards or raw text?** Cards (Q10) do not exist yet. Recommendation: raw title and description now;
   re-run the same experiment when cards exist, with the same decision rule.
3. **Cross-encoder rerank of the top 50?** Recommendation: not in this build. It needs another model (about 280 MB) and
   CPU time per query; add it only if hybrid × eligibility ships and a later batch shows room above it.
4. **Profile only, or profile plus the search string, as the query?** Recommendation: profile only, as fixed above, so
   relevance means "matches what I do" whatever was typed; the search string already chose the pool.
5. **How should title-only listings (36 of 106) be treated?** Recommendation: rank them by title like any other, and
   show "the listing has no description" next to them; do not guess.
6. **One primary candidate, or let any of the four ship?** Recommendation: one primary (hybrid × eligibility), as in
   section 6. Letting four candidates compete on 72 judged jobs mostly rewards luck.
7. **Does a new ranker need a new queue item?** Recommendation: yes. Add SEM2 "build and run the experiment" after this
   plan is approved, with "approve new dependencies" as its YOU condition.
