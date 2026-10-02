"""The local labelling page for a relevance batch (docs/ROADMAP_QUEUE.md Q3, docs/M2_PLAN.md section 5).

One labeller, the owner, grades each job of a batch 0-3 with rubric r1 ("would I apply?").

- Blind: only the listing fields of blind.jsonl are ever sent to the page. No score, no eligibility,
  no search name, nothing that says what the system thinks of the job or the company.
- Resumable: labels are appended to data/eval/labels/<batch id>.jsonl, one JSON line each. The latest
  line for a job is its label; an earlier one is never rewritten (`relabel_of` points at the line it
  replaces). A refresh or a restart continues at the first unlabelled job.
- Local: scripts/label_page.py serves it on 127.0.0.1 only. The page is one document with inline CSS
  and JavaScript and a Content-Security-Policy that forbids loading anything else.

- Timed: the server records the seconds between showing a job and receiving its label. `label_quality()`
  refuses to call a label file complete when more than 10 in 106 jobs were labelled in under 8 seconds, or when
  one grade takes more than half of the labels. The first run of batch gold-20261001-r1 failed both
  (docs/eval/gold_labels.md).

`handle()` is the whole HTTP behaviour as a pure function, so it is tested without a socket.
"""
import hashlib
import html
import json
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

BLIND_FIELDS = ("title", "company", "location", "work_mode", "employment_type", "posted_date", "salary", "description",
                "application_url")
# Rubric r1, word for word from docs/M2_PLAN.md section 5. Changing the wording makes it r2.
RUBRIC: dict[str, Any] = {"version": "r1", "question": "Would I apply?", "grades": {
    "0": "A different field, or ineligible for a fresher.",
    "1": "The same area, but the wrong role or skills; I wouldn't open it.",
    "2": "The right family and plausible for a fresher; worth a look.",
    "3": "The right role, fresher-friendly, matches my core skills; I would apply today."}}
FAST_SECONDS = 8                # a job labelled in less time than this was not read
FAST_ALLOWED = (10, 106)        # at most 10 quick labels in 106 jobs
_LOCK = threading.Lock()


def label_quality(records: list[dict], total: int) -> dict:
    """Whether a label file can be called complete: every job labelled, few quick labels, no grade above half.

    The latest line of a job is its label; the longest look at it counts as its time. A label with no
    timing (the server had not shown the job) counts as quick."""
    grades: dict[str, int] = {}
    longest: dict[str, float] = {}
    for record in records:
        item = record["item_id"]
        grades[item] = record["label"]
        longest[item] = max(longest.get(item, 0.0), record.get("seconds_on_job") or 0.0)
    labelled, missing = len(grades), total - len(grades)
    fast = len([item for item in grades if longest[item] < FAST_SECONDS])
    fast_limit = total * FAST_ALLOWED[0] // FAST_ALLOWED[1]
    grade, count = Counter(grades.values()).most_common(1)[0] if grades else (None, 0)
    problems = []
    if missing:
        problems.append(f"{missing} of {total} jobs have no label")
    if fast > fast_limit:
        problems.append(f"{fast} of {total} jobs were labelled in under {FAST_SECONDS} seconds (at most {fast_limit} allowed; "
                        "a label with no timing counts as quick)")
    if count * 2 > labelled:
        problems.append(f"{count} of {labelled} labels are grade {grade}, more than half")
    summary = f"{fast} of {total} labelled in under {FAST_SECONDS} seconds"
    message = (f"Complete: {labelled} of {total} jobs labelled; {summary}." if not problems else
               "NOT complete: " + "; ".join(problems) + "."
               + ("" if len(problems) == 1 and missing else " Go back with the Left arrow and read those jobs again."))
    return {"total": total, "labelled": labelled, "complete": not missing, "accepted": not problems, "fast": fast,
            "fast_limit": fast_limit, "most_common_grade": grade, "most_common_count": count, "problems": problems,
            "summary": summary, "message": message}


@dataclass
class Response:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


class LabelSession:
    """One batch and its label file."""

    def __init__(self, batch: Path, labels_root: Path, *, clock: Callable[[], float] = time.monotonic):
        self.batch, self.labels_root = Path(batch), Path(labels_root)
        self._clock = clock
        self._shown: dict[str, float] = {}       # item_id -> when its job was last sent to the page
        self.meta = json.loads((self.batch / "meta.json").read_text(encoding="utf-8"))
        if self.meta.get("rubric_version") != RUBRIC["version"]:
            raise ValueError(f"The batch was built for rubric {self.meta.get('rubric_version')}; this page labels with {RUBRIC['version']}.")
        self.batch_id = str(self.meta["batch_id"])
        blind = self.batch / "blind.jsonl"
        self.blind_sha256 = hashlib.sha256(blind.read_bytes()).hexdigest()
        rows = [json.loads(line) for line in blind.read_text(encoding="utf-8").splitlines() if line.strip()]
        # An explicit list of fields: whatever else a batch file carries never reaches the page.
        self.items = [{"item_id": row["item_id"], **{name: row.get(name) for name in BLIND_FIELDS}} for row in rows]
        self.index_of = {item["item_id"]: index for index, item in enumerate(self.items)}
        self.path = self.labels_root / f"{self.batch_id}.jsonl"

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def quality(self) -> dict:
        return label_quality(self.records(), len(self.items))

    def _latest(self) -> dict[str, tuple[int, int]]:
        """item_id -> (label, 1-based line number) of its latest label."""
        latest: dict[str, tuple[int, int]] = {}
        if self.path.exists():
            for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
                if line.strip():
                    record = json.loads(line)
                    latest[record["item_id"]] = (record["label"], number)
        return latest

    def _first_unlabelled(self, latest: dict, after: int = -1) -> int | None:
        order = [*range(after + 1, len(self.items)), *range(0, after + 1)]
        return next((index for index in order if self.items[index]["item_id"] not in latest), None)

    def state(self, at: int | None = None, *, after: int = -1) -> dict:
        latest = self._latest()
        index = at if at is not None and 0 <= at < len(self.items) else self._first_unlabelled(latest, after)
        item = self.items[index] if index is not None else None
        total = len(self.items)
        if item:
            self._shown[item["item_id"]] = self._clock()
        return {"batch_id": self.batch_id, "total": total, "labelled": len(latest), "index": index, "done": index is None,
                "position": f"{(index + 1) if index is not None else total} of {total}", "item": item,
                "current_label": latest[item["item_id"]][0] if item and item["item_id"] in latest else None, "rubric": RUBRIC,
                "quality": self.quality() if index is None else None}

    def label(self, item_id: object, grade: object) -> dict:
        """Append one label and return the state at the next unlabelled job."""
        if not isinstance(item_id, str) or item_id not in self.index_of:
            raise ValueError("unknown item_id")
        if isinstance(grade, bool) or not isinstance(grade, int) or grade not in (0, 1, 2, 3):
            raise ValueError("grade must be 0, 1, 2 or 3")
        shown = self._shown.pop(item_id, None)
        seconds = None if shown is None else round(self._clock() - shown, 1)
        with _LOCK:
            previous = self._latest().get(item_id)
            record = {"batch_id": self.batch_id, "item_id": item_id, "label": grade, "scale": "graded_0_3",
                      "rubric_version": RUBRIC["version"], "labeller": "owner", "set": "gold",
                      "labelled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "relabel_of": previous[1] if previous else None, "seconds_on_job": seconds,
                      "snapshot_stamp": (self.meta.get("frozen") or {}).get("stamp"), "blind_sha256": self.blind_sha256}
            self.labels_root.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle_:
                handle_.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return self.state(after=self.index_of[item_id])


_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                                       "connect-src 'self'; base-uri 'none'; form-action 'none'"}


def _json(status: int, payload) -> Response:
    return Response(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    {**_HEADERS, "Content-Type": "application/json; charset=utf-8"})


def handle(session: LabelSession, method: str, target: str, headers: dict[str, str], body: bytes, *, port: int) -> Response:
    """The reply to one request. Only this machine is answered, and only three paths exist."""
    named = {name.lower(): value for name, value in headers.items()}
    if named.get("host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
        return _json(403, {"error": "This page answers on 127.0.0.1 only."})
    parts = urlsplit(target)
    if parts.path not in ("/", "/api/state", "/api/label"):
        return _json(404, {"error": "Not found."})
    if method == "GET" and parts.path == "/":
        return Response(200, page().encode("utf-8"), {**_HEADERS, "Content-Type": "text/html; charset=utf-8"})
    if method == "GET" and parts.path == "/api/state":
        wanted = parse_qs(parts.query).get("at", [""])[0]
        return _json(200, session.state(int(wanted) if wanted.isdigit() else None))
    if method == "POST" and parts.path == "/api/label":
        if not named.get("content-type", "").startswith("application/json"):
            return _json(415, {"error": "Send JSON."})
        try:
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("send an object with item_id and grade")
            return _json(200, session.label(payload.get("item_id"), payload.get("grade")))
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            return _json(400, {"error": str(exc)})
    return _json(405, {"error": "Method not allowed."})


def page() -> str:
    rubric = "".join(f'<li><button type="button" data-grade="{grade}"><b>{grade}</b></button> {html.escape(meaning, quote=False)}</li>'
                     for grade, meaning in RUBRIC["grades"].items())
    return _PAGE.replace("__QUESTION__", html.escape(RUBRIC["question"])).replace("__RUBRIC__", rubric)


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Label jobs</title>
<style>
  :root { color-scheme: light dark; --ink: #1c1c1c; --soft: #5b5b5b; --line: #d6d6d6; --paper: #fbfbfa; --mark: #0b5cad; }
  @media (prefers-color-scheme: dark) { :root { --ink: #ececec; --soft: #a9a9a9; --line: #3a3a3a; --paper: #171717; --mark: #7db7f0; } }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--paper); color: var(--ink); font: 16px/1.5 system-ui, "Segoe UI", sans-serif; }
  main { max-width: 60rem; margin: 0 auto; padding: 1rem 1.25rem 3rem; }
  header { display: flex; flex-wrap: wrap; gap: .5rem 1.5rem; align-items: baseline; border-bottom: 1px solid var(--line); padding-bottom: .5rem; }
  #position { font-size: 1.25rem; font-weight: 600; font-variant-numeric: tabular-nums; }
  #progress, #message, .keys { color: var(--soft); }
  #message { min-height: 1.5rem; margin: .5rem 0; }
  h1 { font-size: 1.5rem; line-height: 1.25; margin: 1rem 0 .25rem; }
  #facts { color: var(--soft); margin: 0 0 .75rem; }
  #description { white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid var(--line); border-radius: 6px; padding: .75rem 1rem;
                 max-height: 55vh; overflow: auto; }
  #rubric { list-style: none; padding: 0; margin: 1rem 0 0; display: grid; gap: .35rem; }
  #rubric button { min-width: 2.5rem; padding: .3rem .6rem; margin-right: .5rem; font: inherit; color: inherit; background: transparent;
                   border: 1px solid var(--line); border-radius: 6px; cursor: pointer; }
  #rubric button:hover, #rubric button:focus-visible { border-color: var(--mark); outline: none; }
  #rubric li.chosen button { border-color: var(--mark); color: var(--mark); }
  a { color: var(--mark); overflow-wrap: anywhere; }
</style>
</head>
<body>
<main>
  <header>
    <span id="position">loading</span>
    <span id="progress"></span>
    <span class="keys">Keys: 0 1 2 3 to label, Left arrow or B to go back one job, Right arrow to go forward.</span>
  </header>
  <p id="message" role="status"></p>
  <article id="job" hidden>
    <h1 id="title"></h1>
    <p id="facts"></p>
    <div id="description"></div>
    <p><a id="link" target="_blank" rel="noreferrer noopener"></a></p>
  </article>
  <section>
    <h2>__QUESTION__</h2>
    <ul id="rubric">__RUBRIC__</ul>
  </section>
</main>
<script>
"use strict";
let state = null;
const byId = (name) => document.getElementById(name);

function show(next) {
  state = next;
  byId("position").textContent = next.done ? "Done: " + next.position : "Job " + next.position;
  byId("progress").textContent = next.labelled + " labelled, " + (next.total - next.labelled) + " to go";
  const item = next.item;
  byId("job").hidden = !item;
  for (const row of document.querySelectorAll("#rubric li")) {
    row.classList.toggle("chosen", item !== null && String(next.current_label) === row.querySelector("button").dataset.grade);
  }
  if (!item) {
    byId("message").textContent = next.quality ? next.quality.message : "Every job in this batch has a label.";
    return;
  }
  byId("message").textContent = next.current_label === null ? "" : "You labelled this job " + next.current_label + ". A new key replaces it.";
  byId("title").textContent = item.title || "";
  const facts = [item.company, item.location, item.work_mode, item.employment_type, item.salary,
                 item.posted_date ? "posted " + String(item.posted_date).slice(0, 10) : null];
  byId("facts").textContent = facts.filter(Boolean).join(" · ");
  byId("description").textContent = item.description || "(no description in the listing)";
  byId("description").scrollTop = 0;
  const link = byId("link");
  const address = String(item.application_url || "");
  const safe = /^https?:\\/\\//i.test(address);
  link.textContent = safe ? address : "";
  if (safe) { link.setAttribute("href", address); } else { link.removeAttribute("href"); }
}

async function load(index) {
  const reply = await fetch("/api/state" + (index === null ? "" : "?at=" + index));
  show(await reply.json());
}

async function grade(value) {
  if (!state || !state.item) { return; }
  const reply = await fetch("/api/label", { method: "POST", headers: { "Content-Type": "application/json" },
                                           body: JSON.stringify({ item_id: state.item.item_id, grade: value }) });
  const answer = await reply.json();
  if (reply.ok) { show(answer); } else { byId("message").textContent = "Not saved: " + answer.error; }
}

function back() {
  if (!state) { return; }
  const index = state.done ? state.total - 1 : state.index - 1;
  if (index >= 0) { load(index); }
}

function forward() {
  if (state && !state.done && state.index + 1 < state.total) { load(state.index + 1); }
}

document.addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey) { return; }
  if (["0", "1", "2", "3"].includes(event.key)) { event.preventDefault(); grade(Number(event.key)); }
  else if (event.key === "ArrowLeft" || event.key === "b" || event.key === "B") { event.preventDefault(); back(); }
  else if (event.key === "ArrowRight") { event.preventDefault(); forward(); }
});
for (const button of document.querySelectorAll("#rubric button")) {
  button.addEventListener("click", () => grade(Number(button.dataset.grade)));
}
load(null).catch(() => { byId("message").textContent = "The labelling server is not answering."; });
</script>
</body>
</html>
"""
