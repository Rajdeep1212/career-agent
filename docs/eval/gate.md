# Offline gate: test counts

Measured on 9 October 2026 on Windows 11 (Python 3.11.16, Node 24.19.0) with the LAND1 changes. Claim
level L0: counts printed by the test runners. Every test is offline and uses recorded fixtures.

| Check | Command | Result |
|---|---|---|
| Python tests | `python run_tests.py` | 1003 offline Python tests run, 0 failed, 2 skipped |
| Dashboard script | `node tests/test_frontend.cjs` | passed |
| Lint | `python -m ruff check .` | no findings |
| Types | `python -m mypy` | no issues in 120 source files |
| Web types | `npm run typecheck` (in `web/`) | no errors |
| Web tests | `npm test` (in `web/`) | 57 web tests passed in 8 files |
| Browser tests | `npm run smoke` (in `web/`) | 8 browser tests passed (Playwright), on a disposable tracker database; the screenshot spec is skipped |

The 2 skipped Python tests: one needs a Windows account that can create symlinks; one is the Postgres migration
test, which runs only when `TRACKER_TEST_POSTGRES_URL` names a throwaway database (it passed on Postgres 16 on
8 October 2026).

These counts change with every item; `docs/RUNBOOK.md` carries the current gate line.
