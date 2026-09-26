"""One-off JSearch check you run with your own key: python -m app.sources.jsearch_probe

It answers what the public docs don't state clearly, using RapidAPI's quota
header (x-ratelimit-requests-remaining) before and after each call:
  1. how many requests one page costs;
  2. whether num_pages=2 costs one request or two;
  3. whether "A OR B" returns jobs for both roles;
  4. whether job_requirements=no_experience / under_3_years_experience are accepted.
It sends 5 requests in total and prints results only; the key is never printed.
"""
import argparse
import asyncio
import re
import sys

from app.core.config import settings
from app.providers.base import ProviderError
from app.providers.jsearch_provider import JSearchProvider, parse_jobs

REQUESTS = 5
ROLE_A, ROLE_B = "data analyst", "python developer"


def _remaining(response) -> int | None:
    value = response.headers.get("x-ratelimit-requests-remaining", "").strip()
    return int(value) if value.isdigit() else None


def _cost(before: int | None, after: int | None) -> str:
    return f"{before - after} request(s)" if before is not None and after is not None else "unknown (no quota header)"


async def probe(provider: JSearchProvider | None = None) -> list[str]:
    provider = provider or JSearchProvider()
    lines = []

    one = await provider.request({"query": ROLE_B, "num_pages": 1, "date_posted": "week"})
    two = await provider.request({"query": ROLE_B, "num_pages": 2, "date_posted": "week"})
    lines.append(f"1 page: {len(parse_jobs(one))} jobs; quota remaining {_remaining(one)}")
    lines.append(f"num_pages=2: {len(parse_jobs(two))} jobs; cost {_cost(_remaining(one), _remaining(two))}")

    either = await provider.request({"query": f"{ROLE_A} OR {ROLE_B}", "num_pages": 1, "date_posted": "week"})
    titles = [job.title.casefold() for job in parse_jobs(either)]
    a = sum(bool(re.search(r"\banalyst\b", title)) for title in titles)
    b = sum(bool(re.search(r"\bpython\b|\bdeveloper\b", title)) for title in titles)
    verdict = "looks supported (both roles returned)" if a and b else "not confirmed (only one role returned)"
    lines.append(f"'{ROLE_A} OR {ROLE_B}': {len(titles)} jobs, {a} analyst titles, {b} python/developer titles; OR {verdict}")

    last = either
    for value in ("no_experience", "under_3_years_experience"):
        try:
            response = await provider.request({"query": ROLE_B, "num_pages": 1, "job_requirements": value})
            lines.append(f"job_requirements={value}: accepted, {len(parse_jobs(response))} jobs; "
                         f"cost {_cost(_remaining(last), _remaining(response))}")
            last = response
        except ProviderError as exc:
            lines.append(f"job_requirements={value}: rejected ({exc.status_code} {exc.reason})")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.sources.jsearch_probe", description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args(argv)
    if not settings.rapidapi_key:
        print("RAPIDAPI_KEY is not set in .env.")
        return 2
    if not args.yes and input(f"This sends {REQUESTS} JSearch requests from your monthly quota. Continue? [y/N] ").strip().lower() != "y":
        print("Nothing sent.")
        return 0
    try:
        for line in asyncio.run(probe()):
            print(line)
    except ProviderError as exc:
        print(f"Probe stopped: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
