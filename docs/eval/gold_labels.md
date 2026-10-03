# Gold labels for batch gold-20261001-r1

The gold labels are Rajdeep's own grades (rubric r1, "would I apply?", 0-3) for the 106 jobs of
`data/eval/batches/gold-20261001-r1`. They are the reference every ranker is measured against, so a bad
gold file makes every later number meaningless. Label files live under `data/eval/labels/` and are not in git.

## Run 1 is void (2 October 2026)

File: `data/eval/labels/gold-20261001-r1.run1.jsonl` (sha256 `82d0d185379d9c62`, first 16 hex). Kept, never scored against.

| What | Measured |
|---|---|
| Labels | 106 of 106, in page order, none changed afterwards |
| Time | 339 seconds in total; a median of 2 seconds per job |
| Grades 0 / 1 / 2 / 3 | 8 / 12 / 22 / 64 |
| Jobs with a sales title (the control) | 21, of which 13 graded 3; the target profile says "Never: sales" |
| Sampled jobs the eligibility check excluded | 35, of which 21 graded 3 |
| Agreement with the hosted labels | exact match 14 of 106; quadratic weighted kappa 0.029 |
| Hosted labels against themselves, two orders | exact match 95 of 106; quadratic weighted kappa 0.904 |

Rajdeep's own verdict: the labels were rushed and are not usable. This file is evidence that fast labelling
produces unusable gold. It is not data.

The numbers computed from it are void, including NDCG@10 of the ranker (search A 0.866, search B 0.701). They are
kept only in `docs/eval/agreement_gold-20261001-r1.run1.md`, which is marked void. SEM1 has no baseline until
run 2 exists.

## The guard added after run 1

`app/eval/label_page.py` now records, for every label, the seconds between the server showing the job and
receiving its grade (`seconds_on_job`). `label_quality()` refuses to call a label file complete when:

- any job has no label;
- more than 10 in 106 jobs were labelled in under 8 seconds (the longest look at a job counts; a label with no
  timing counts as quick);
- more than half of all labels are the same grade.

The page shows the verdict when the last job is labelled. `python scripts/label_page.py --batch <batch> --check`
prints it and exits with 1 when the file is refused. `scripts/hosted_labels.py report` will not score a gold file
that fails. Run 1 would have failed on both the speed rule and the one-grade rule (there is a test for exactly that).

The guard measures effort, not judgement. A file can pass it and still be wrong, which is why the agreement with the
hosted labels is reported as well.

## Run 2 is void too (2 October 2026)

File: `data/eval/labels/gold-20261001-r1.run2.jsonl` (sha256 `9ba666e02338172d`, first 16 hex). Kept, never scored against.
It was labelled after the guard existed, and the guard refused it on both rules.

| What | Measured |
|---|---|
| Labels | 106 of 106, in page order, none changed afterwards |
| Time | 203 seconds in total; a median of 1.6 seconds on a job; the longest look 56.9 seconds |
| Labelled in under 8 seconds | 105 of 106 (at most 10 allowed) |
| Grades 0 / 1 / 2 / 3 | 2 / 12 / 33 / 59 (more than half at grade 3) |
| Jobs with a sales title (the control) | 21, of which 11 graded 3 |
| Sampled jobs the eligibility check excluded | 35, of which 16 graded 3 |

Run 2 was faster than run 1. Like run 1 it is evidence that fast labelling produces unusable gold, and it shows
that a check at the end is too late: the whole file was done before the refusal appeared.

## What changed after run 2

- The page shows the count per grade after every 20 labels ("After 20 labels: 3 at grade 0, ..."), and at the end;
  the line stays on screen until the next one replaces it.
- `--check` prints the same counts ("Grades so far: ...").
- Labelling is done in sittings of 20.

## Run 3 is the gold file (3 October 2026)

File: `data/eval/labels/gold-20261001-r1.jsonl` (sha256 `b00062c13e6c06a9`, first 16 hex). It passes the guard.

| What | Measured |
|---|---|
| Label lines | 131 for 106 jobs; 23 jobs re-read and graded again (5 grades changed) |
| Time | median 29.8 seconds per job (the longest look at each); 0 of 106 under 8 seconds |
| Grades 0 / 1 / 2 / 3 | 39 / 26 / 27 / 14 |
| Labelled | 2 October 17:47 to 3 October 07:41 UTC, in sittings of 20 |

The agreement report and the NDCG@10 baseline computed from it are in `docs/eval/agreement_gold-20261001-r1.md`.
The run 1 and run 2 reports (`agreement_gold-20261001-r1.run1.md`, `agreement_gold-20261001-r1.run2.md`) are void.
