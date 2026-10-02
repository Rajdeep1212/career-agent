"""Merge a researched careers patch into the company seed list (docs/ROADMAP_QUEUE.md Q2c3).

The patch is a CSV of careers pages found by hand or by research, one row per company. The seed
always wins: a careers_url is filled only where the seed's cell is empty, and only from rows whose
status says a page was found (`verified`, `check`, `fetch_blocked`). The one exception is a patch
URL that names the same board as the seed's URL in another spelling (boards.greenhouse.io/x and
job-boards.greenhouse.io/x); that spelling is taken. `ats` is never changed; detection decides it.
"""
import csv
from dataclasses import dataclass, field
from pathlib import Path

from app.sources.ats_detect import match_url
from app.sources.company_seed import SeedFile, fetch_allowed

PATCH_COLUMNS = ["company", "city", "list_type", "careers_url", "status", "ats_seen", "open_roles", "evidence", "checked_on"]
APPLIED_STATUSES = {"verified", "check", "fetch_blocked"}


@dataclass
class MergePlan:
    filled: dict[str, str] = field(default_factory=dict)                # company -> careers_url written into an empty cell
    respelled: dict[str, str] = field(default_factory=dict)             # company -> the same board, the patch's spelling
    kept: dict[str, tuple[str, str]] = field(default_factory=dict)      # company -> (seed URL kept, patch URL not used)
    unchanged: list[str] = field(default_factory=list)                  # the patch URL equals the seed's
    skipped: dict[str, str] = field(default_factory=dict)               # company -> patch status that fills nothing
    refused: dict[str, str] = field(default_factory=dict)               # company -> why its patch URL cannot be used

    def updates(self) -> dict[str, dict[str, str]]:
        return {company: {"careers_url": url} for company, url in {**self.filled, **self.respelled}.items()}


def read_patch(path: Path) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != PATCH_COLUMNS:
            raise ValueError(f"patch header does not match; expected {','.join(PATCH_COLUMNS)}")
        return [{name: (value or "").strip() for name, value in row.items()} for row in reader]


def _same_board(first: str, second: str) -> bool:
    one, two = match_url(first), match_url(second)
    return bool(one and two and (one.ats, one.key.lower(), one.region) == (two.ats, two.key.lower(), two.region))


def plan_merge(seed: SeedFile, patch: list[dict[str, str]]) -> MergePlan:
    current = {row.company: row.careers_url for row in seed.rows}
    strangers = sorted({row["company"] for row in patch} - set(current))
    if strangers:
        raise ValueError(f"patch companies not in the seed list: {', '.join(strangers)}")
    plan = MergePlan()
    for row in patch:
        company, url, existing = row["company"], row["careers_url"], current[row["company"]]
        if row["status"] not in APPLIED_STATUSES or not url:
            plan.skipped[company] = row["status"]
        elif not fetch_allowed(url):
            plan.refused[company] = f"'{url}' is not an http(s) URL this app may fetch"
        elif not existing:
            plan.filled[company] = url
        elif existing == url:
            plan.unchanged.append(company)
        elif _same_board(existing, url):
            plan.respelled[company] = url
        else:
            plan.kept[company] = (existing, url)
    return plan
