"""The company seed list (seeds/companies_seed.csv): read and validate, never repair.

Every row keeps the public page it was taken from (`source_url`). Problems are
returned as text with the file line they came from; nothing is fixed or
dropped silently. The vocabularies below are the values the list uses today.
"""
import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from app.services.job_identity import company_key

COLUMNS = ["company", "list_type", "category", "national_top", "city_group", "city", "sector", "why_listed",
           "source_url", "careers_url", "ats"]
EXPECTED_ROWS = 356
LIST_TYPES = {"mnc_gcc", "startup"}
CATEGORIES = {"startup", "scaleup", "it_services", "product_mnc", "gcc", "bank_fintech_mnc", "consulting"}
KNOWN_ATS = {"greenhouse", "lever", "ashby", "smartrecruiters", "workday", "zohorecruit", "darwinbox", "keka", "eightfold",
             "workable", "successfactors", "taleo", "icims", "freshteam", "recruitee", "teamtailor", "bamboohr", "oracle_cloud"}

# Sites the app never fetches; the user opens them (docs/ROADMAP_QUEUE.md, CLAUDE.md hard constraints).
_NEVER_FETCHED_HOST = re.compile(r"(?:^|\.)(?:linkedin\.com|lnkd\.in|naukri\.com|indeed\.[a-z.]+|wellfound\.com|angel\.co|foundit\.in|"
                                 r"instahyre\.com|internshala\.com|geeksforgeeks\.org|leetcode\.com|glassdoor\.[a-z.]+|ambitionbox\.com)$")


def _host(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return (parts.hostname or "").lower().rstrip(".") if parts.scheme in ("http", "https") else ""


def fetch_allowed(url: str) -> bool:
    """True for an http(s) URL whose host is not on the never-fetched list."""
    host = _host(url)
    return bool(host) and "." in host and not _NEVER_FETCHED_HOST.search(host)


@dataclass(frozen=True)
class SeedRow:
    line: int               # line in the file; the header is line 1
    company: str
    list_type: str
    category: str
    national_top: bool
    city_group: str
    city: str
    sector: str
    why_listed: str
    source_url: str
    careers_url: str
    ats: str                # what the list states; "unknown" when it states nothing


@dataclass
class SeedFile:
    rows: list[SeedRow] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    sha256: str = ""


def _row_problems(values: dict[str, str]) -> list[str]:
    problems = []
    if not values["company"]:
        problems.append("company is empty")
    if values["list_type"] not in LIST_TYPES:
        problems.append(f"list_type '{values['list_type']}' is not one of {', '.join(sorted(LIST_TYPES))}")
    if values["category"] not in CATEGORIES:
        problems.append(f"category '{values['category']}' is not one of {', '.join(sorted(CATEGORIES))}")
    if values["national_top"] not in ("", "yes"):
        problems.append(f"national_top '{values['national_top']}' must be 'yes' or empty")
    if not values["source_url"]:
        problems.append("source_url is missing")
    elif not _host(values["source_url"]):
        problems.append(f"source_url '{values['source_url']}' is not an http(s) URL")
    if values["careers_url"]:
        if not _host(values["careers_url"]):
            problems.append(f"careers_url '{values['careers_url']}' is not an http(s) URL")
        elif not fetch_allowed(values["careers_url"]):
            problems.append(f"careers_url '{values['careers_url']}' is on a site that is never fetched")
    if values["ats"] != "unknown" and values["ats"] not in KNOWN_ATS:
        problems.append(f"ats '{values['ats']}' is not 'unknown' or a known ATS name")
    return problems


def update_rows(path: Path, updates: dict[str, dict[str, str]]) -> list[str]:
    """Set careers_url and/or ats for the named companies; every other line of the file keeps its bytes.

    Returns the companies whose line changed. An unknown company or any other column raises ValueError
    and nothing is written."""
    columns = {column for values in updates.values() for column in values}
    if columns - {"careers_url", "ats"}:
        raise ValueError(f"only careers_url and ats can be updated, not {', '.join(sorted(columns - {'careers_url', 'ats'}))}")
    lines = Path(path).read_bytes().decode("utf-8").splitlines(keepends=True)
    missing, changed = set(updates), []
    for index, line in enumerate(lines[1:], start=1):
        body = line.rstrip("\r\n")
        cells = next(csv.reader([body]), [])
        if len(cells) != len(COLUMNS) or cells[0].strip() not in updates:
            continue
        company = cells[0].strip()
        missing.discard(company)
        new = list(cells)
        for column, value in updates[company].items():
            new[COLUMNS.index(column)] = value
        if new != cells:
            buffer = io.StringIO()
            csv.writer(buffer, lineterminator="").writerow(new)
            lines[index] = buffer.getvalue() + line[len(body):]
            changed.append(company)
    if missing:
        raise ValueError(f"not in the seed list: {', '.join(sorted(missing))}")
    if changed:
        Path(path).write_bytes("".join(lines).encode("utf-8"))
    return changed


def read_seed(path: Path, *, expected_rows: int = EXPECTED_ROWS) -> SeedFile:
    raw = Path(path).read_bytes()
    seed = SeedFile(sha256=hashlib.sha256(raw).hexdigest())
    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
    header = next(reader, [])
    if header != COLUMNS:
        missing = [name for name in COLUMNS if name not in header]
        extra = [name for name in header if name not in COLUMNS]
        seed.problems.append(f"header does not match: missing {missing or 'nothing'}, unexpected {extra or 'nothing'}; "
                             f"expected {','.join(COLUMNS)}")
        return seed
    first_line: dict[str, int] = {}
    for cells in reader:
        line = reader.line_num
        if len(cells) != len(COLUMNS):
            seed.problems.append(f"line {line}: {len(cells)} fields, expected {len(COLUMNS)}")
            continue
        values = {name: cell.strip() for name, cell in zip(COLUMNS, cells)}
        problems = _row_problems(values)
        key = company_key(values["company"])
        if key and key in first_line:
            problems.append(f"'{values['company']}' is the same company as line {first_line[key]}")
        first_line.setdefault(key, line)
        seed.problems += [f"line {line}: {problem}" for problem in problems]
        seed.rows.append(SeedRow(line=line, **{**values, "national_top": values["national_top"] == "yes"}))  # type: ignore[arg-type]
    if len(seed.rows) != expected_rows:
        seed.problems.append(f"expected {expected_rows} rows, found {len(seed.rows)}")
    return seed
