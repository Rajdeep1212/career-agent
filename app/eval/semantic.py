"""SEM2: the rankers, metrics and bootstrap of docs/SEMANTIC_PLAN.md, with every parameter fixed there.

Relevance and eligibility stay two separate scores. Relevance: BM25 (rank_bm25 defaults), dense cosine similarity
(FastEmbed, BAAI/bge-small-en-v1.5, local), or their reciprocal rank fusion with k = 60. Eligibility: a tier from
the existing check plus the fresher detectors in app/services/eligibility.py. The combined ranker sorts by tier,
then by relevance. Nothing here is wired into the app.

Ties are broken by item_id (fixed before any result was seen, so every order is reproducible).
"""
import ctypes
import os
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np

from app.eval.hosted_labels import ndcg_at_k

RRF_K = 60
JOB_TEXT_CHARS = 2000
RELEVANT_GRADE = 2                  # Precision@5 counts grade 2 or 3 as relevant
BOOTSTRAP_RESAMPLES = 1000
SEED = 20261003
MODEL = "BAAI/bge-small-en-v1.5"
MIN_FREE_RAM = 1024 ** 3            # the runtime guard decided in SEM0
PRIMARY = "hybrid x eligibility"
TIER_ORDER = {"eligible": 0, "uncertain": 1, "excluded": 2}


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#]+", (text or "").casefold())


def job_text(item: dict) -> str:
    return f"{item.get('title') or ''}\n{(item.get('description') or '')[:JOB_TEXT_CHARS]}"


def order_by(scores: dict[str, float]) -> list[str]:
    return sorted(scores, key=lambda item: (-scores[item], item))


def bm25_scores(query: str, texts: dict[str, str]) -> dict[str, float]:
    from rank_bm25 import BM25Okapi
    ids = sorted(texts)
    model = BM25Okapi([tokenize(texts[item]) for item in ids])            # library defaults: k1 = 1.5, b = 0.75
    return dict(zip(ids, (float(score) for score in model.get_scores(tokenize(query)))))


def dense_scores(embedder, query: str, texts: dict[str, str]) -> dict[str, float]:
    ids = sorted(texts)
    question = np.asarray(embedder.query(query), dtype=float)
    passages = np.asarray(embedder.passages([texts[item] for item in ids]), dtype=float)
    similarity = passages @ question / (np.linalg.norm(passages, axis=1) * np.linalg.norm(question))
    return dict(zip(ids, (float(score) for score in similarity)))


def rrf(orders: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    fused: dict[str, float] = {}
    for order in orders:
        for rank, item in enumerate(order, start=1):
            fused[item] = fused.get(item, 0.0) + 1 / (k + rank)
    return fused


def tier(status: str, detector_outcomes: list[str]) -> str:
    """The existing check's status, made stricter by the detectors: any exclusion excludes; any doubt is uncertain."""
    if status == "excluded" or "excluded" in detector_outcomes:
        return "excluded"
    if status == "uncertain" or "uncertain" in detector_outcomes:
        return "uncertain"
    return "eligible"


def gated_order(relevance: dict[str, float], tiers: dict[str, str]) -> list[str]:
    return sorted(relevance, key=lambda item: (TIER_ORDER[tiers[item]], -relevance[item], item))


def pool(jobs: list[dict], search: str) -> set[str]:
    return {job["item_id"] for job in jobs if any(entry["search"] == search for entry in job["surfaced_by"])}


def v1_order(jobs: list[dict], search: str) -> list[str]:
    """The current ranker: the shown jobs in the order the search showed them, then the sampled excluded jobs by v1's score."""
    shown, sampled = [], []
    for job in jobs:
        for entry in job["surfaced_by"]:
            if entry["search"] != search:
                continue
            if entry["stage"] == "excluded_sample":
                sampled.append((-(job.get("total_score") or 0), job["item_id"]))
            else:
                shown.append((entry["rank"], job["item_id"]))
    return [item for _, item in sorted(shown)] + [item for _, item in sorted(sampled)]


def precision_at_k(grades: list[int], k: int = 5) -> float:
    return sum(1 for grade in grades[:k] if grade >= RELEVANT_GRADE) / k


def ndcg10(ranked: list[int], pooled: list[int]) -> float:
    return ndcg_at_k(ranked, 10, pool=pooled)


def precision5(ranked: list[int], pooled: list[int]) -> float:
    return precision_at_k(ranked, 5)


Metric = Callable[[list[int], list[int]], float]


def _score(order: list[str], items: list[str], grades: dict[str, int], metric: Metric) -> float:
    position = {item: index for index, item in enumerate(order)}
    ranked = sorted(items, key=lambda item: position[item])                 # duplicates from resampling stay adjacent
    return metric([grades[item] for item in ranked], [grades[item] for item in items])


def bootstrap(pools: dict[str, list[str]], orders: dict[str, dict[str, list[str]]], grades: dict[str, int], metric: Metric,
              *, resamples: int = BOOTSTRAP_RESAMPLES, seed: int = SEED) -> dict[str, dict[str, tuple[float, float, float]]]:
    """Per ranker: (point, 2.5th, 97.5th percentile) of its metric minus v1's, per search and averaged over searches.

    Jobs are resampled with replacement within each search's pool; every ranker is scored on the same resample."""
    rng = np.random.default_rng(seed)
    searches = sorted(pools)
    members = {search: sorted(pools[search]) for search in searches}
    draws = [{search: [members[search][index] for index in rng.integers(0, len(members[search]), len(members[search]))]
              for search in searches} for _ in range(resamples)]
    result: dict[str, dict[str, tuple[float, float, float]]] = {}
    for name, order in orders.items():
        def difference(items_by_search: dict[str, list[str]], search: str, order=order) -> float:
            items = items_by_search[search]
            return _score(order[search], items, grades, metric) - _score(orders["v1"][search], items, grades, metric)
        point = {search: difference(members, search) for search in searches}
        samples = {search: np.array([difference(draw, search) for draw in draws]) for search in searches}
        mean_samples = np.mean([samples[search] for search in searches], axis=0)
        summary = {search: (point[search], *np.percentile(samples[search], [2.5, 97.5])) for search in searches}
        summary["mean"] = (float(np.mean(list(point.values()))), *np.percentile(mean_samples, [2.5, 97.5]))
        result[name] = {key: (round(float(values[0]), 6), round(float(values[1]), 6), round(float(values[2]), 6))
                        for key, values in summary.items()}
    return result


def ships(*, gold_mean: tuple[float, float, float], hosted_mean: tuple[float, float, float]) -> bool:
    """The decision rule of docs/SEMANTIC_PLAN.md section 6, applied to the primary candidate's averaged NDCG@10 difference."""
    return gold_mean[1] > 0 and hosted_mean[0] >= 0


def free_ram_bytes() -> int:
    if os.name == "nt":
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("available", ctypes.c_ulonglong), ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                        ("avail_extended", ctypes.c_ulonglong)]
        status = Status()
        status.length = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))       # type: ignore[attr-defined]
        return int(status.available)
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")      # type: ignore[attr-defined]


def cache_dir() -> Path:
    return Path(os.environ.get("HF_HOME") or "F:/huggingface") / "fastembed"


class FastEmbedder:
    """Local embeddings with FastEmbed (ONNX Runtime). Refuses to start with under 1 GB of free RAM."""

    def __init__(self, model: str = MODEL):
        free = free_ram_bytes()
        if free < MIN_FREE_RAM:
            raise MemoryError(f"Embedding needs about 1 GB of free RAM; {free / 1024 ** 3:.1f} GB is free. Close something and retry.")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")      # Windows without Developer Mode: files are copied
        from fastembed import TextEmbedding
        self.model = model
        self._model = TextEmbedding(model_name=model, cache_dir=str(cache_dir()))

    def query(self, text: str) -> np.ndarray:
        return np.asarray(next(iter(self._model.query_embed(text))))

    def passages(self, texts: list[str]) -> np.ndarray:
        return np.asarray(list(self._model.passage_embed(texts)))


def save_vectors(path: Path, ids: list[str], model: str, matrix: np.ndarray) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, ids=np.array(ids), model=np.array(model), vectors=matrix)


def load_vectors(path: Path) -> tuple[list[str], str, np.ndarray]:
    data = np.load(path)
    return [str(item) for item in data["ids"]], str(data["model"]), data["vectors"]
