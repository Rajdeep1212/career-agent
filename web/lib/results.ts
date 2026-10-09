// The measured results shown on the landing page. One source of truth: the page renders these, and
// tests/results.test.ts fails when the README's Evaluation table or the cited docs/eval file disagrees.
// All are claim level L0: measurements on one labelled batch or one run, never calibrated probabilities.

export const REPOSITORY = "https://github.com/Rajdeep1212/career-agent";

export interface Result {
  id: string;
  title: string;
  /** The sentence shown on the page; every figure in it is listed in `figures`. */
  text: string;
  /** Exact strings that must appear in the README's Evaluation section. */
  figures: string[];
  /** Exact strings that must appear in the cited file (compared with spaces removed). */
  inSource: string[];
  /** Repository path of the file the figures come from. */
  source: string;
  level: "L0";
}

const gold = { jobs: "106", grades: "39/26/27/14", seconds: "29.8" };
const agreement = { exact: "54 of 106", batch: "0.498", reverse: "0.458" };
const baseline = { aShown: "0.735", aPool: "0.708", bShown: "0.660", bPool: "0.557" };
const sem2 = { eligibility: "+0.070 [-0.103, +0.254]", bm25: "-0.241 [-0.436, -0.004]", dense: "-0.279 [-0.428, -0.056]", hybrid: "-0.229 [-0.422, -0.037]" };
const filter = { matches: "300", stale: "74", ineligible: "187", shown: "39" };
const boards = { pollable: "32", seed: "356" };
const tests = { python: "1054 offline Python tests", web: "57 web tests", browser: "8 browser tests" };

export const RESULTS: Result[] = [
  {
    id: "gold",
    title: "Hand-labelled gold set",
    text: `${gold.jobs} jobs graded 0-3 ("would I apply?") by hand and blind. Grades 0/1/2/3: ${gold.grades}; median ${gold.seconds} s per job. Two rushed earlier runs were voided by a labelling guard.`,
    figures: [`${gold.jobs} jobs`, gold.grades, gold.seconds],
    inSource: [gold.jobs, "39 / 26 / 27 / 14", gold.seconds],
    source: "docs/eval/gold_labels.md",
    level: "L0",
  },
  {
    id: "agreement",
    title: "Agreement with a hosted labeller",
    text: `Exact match ${agreement.exact}; quadratic weighted kappa ${agreement.batch} in batch order and ${agreement.reverse} in reverse order. The hosted labels never saw CV text and are used for agreement only.`,
    figures: [agreement.exact, agreement.batch, agreement.reverse],
    inSource: [agreement.exact, agreement.batch, agreement.reverse],
    source: "docs/eval/agreement_gold-20261001-r1.md",
    level: "L0",
  },
  {
    id: "baseline",
    title: "Ranking baseline",
    text: `NDCG@10 of the current ranker against the gold labels: ${baseline.aShown} for the AI Engineer search (${baseline.aPool} over the judged pool) and ${baseline.bShown} for the Software Engineer search (${baseline.bPool}).`,
    figures: [baseline.aShown, baseline.aPool, baseline.bShown, baseline.bPool],
    inSource: [baseline.aShown, baseline.aPool, baseline.bShown, baseline.bPool],
    source: "docs/eval/agreement_gold-20261001-r1.md",
    level: "L0",
  },
  {
    id: "sem2",
    title: "Semantic ranking was tested and not adopted",
    text: `BM25, dense embeddings and their hybrid all ranked worse than the current ranker (mean NDCG@10 change ${sem2.bm25}, ${sem2.dense} and ${sem2.hybrid}). With eligibility detectors the hybrid gained ${sem2.eligibility}, a 95% interval that includes zero, so the current ranker stays.`,
    figures: [sem2.bm25, sem2.dense, sem2.hybrid, sem2.eligibility],
    inSource: [sem2.bm25, sem2.dense, sem2.hybrid, sem2.eligibility],
    source: "docs/eval/sem2_results.md",
    level: "L0",
  },
  {
    id: "filter",
    title: "What the freshness and eligibility checks remove",
    text: `For "Software Engineer fresher jobs in India", of ${filter.matches} index matches ${filter.stale} were hidden as stale and ${filter.ineligible} excluded as ineligible; ${filter.shown} were shown.`,
    figures: [`${filter.matches} index matches`, `${filter.stale} were hidden as stale`, `${filter.ineligible} excluded as ineligible`, `${filter.shown} were shown`],
    inSource: ["| B | 300 | yes | 2026-03-10 | 74 | 187 | 39 |"],
    source: "docs/eval/searches.md",
    level: "L0",
  },
  {
    id: "boards",
    title: "Official company boards that can be polled",
    text: `${boards.pollable} of ${boards.seed} seed companies pass one rule: the board's public feed answers, at least one job is in India, and the newest posting is at most 180 days old.`,
    figures: [`${boards.pollable} of ${boards.seed}`],
    inSource: ["| pollable | 32 |", "Rows: 356"],
    source: "docs/eval/ats_detection.md",
    level: "L0",
  },
  {
    id: "tests",
    title: "Tests",
    text: `${tests.python}, ${tests.web} and ${tests.browser}, all run without the network.`,
    figures: [tests.python, tests.web, tests.browser],
    inSource: [tests.python, tests.web, tests.browser],
    source: "docs/eval/gate.md",
    level: "L0",
  },
];

export const sourceUrl = (result: Result) => `${REPOSITORY}/blob/main/${result.source}`;
