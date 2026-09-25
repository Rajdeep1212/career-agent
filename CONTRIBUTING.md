# Contributing

Contributions should preserve the application's local-first safety boundaries
and deterministic behavior.

## Development setup

1. Create a Python 3.11 or newer virtual environment.
2. Install `requirements.txt`.
3. Copy `.env.example` to `.env` only when `.env` does not already exist.
4. Keep provider and OAuth credentials out of source, fixtures, logs, and issue
   reports.

## Making changes

- Add a focused failing regression test before changing behavior.
- Use disposable storage and mocked HTTP for automated tests.
- Keep provider normalization separate from intent, eligibility, matching, and
  ranking.
- Preserve the explicit approval boundary for outreach and Gmail sending.
- Do not include real resumes, contact details, OAuth records, job history, or
  provider responses in examples or fixtures.
- Avoid changing existing API contracts without documenting compatibility.

Run all required checks before submitting a change:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
node tests/test_frontend.cjs
python -m ruff check .
python -m mypy
```

Describe the user-visible behavior, tests run, storage migrations, and security
impact in the pull request. Report security issues through the process in
[SECURITY.md](SECURITY.md), rather than a public issue.
