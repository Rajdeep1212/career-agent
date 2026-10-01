"""Fixed dates for search tests: freshness (app/services/freshness.py) depends on "today"."""
from datetime import date
from unittest.mock import patch

FIXTURE_TODAY = date(2026, 9, 28)
POSTED = '2026-09-20'   # eight days before FIXTURE_TODAY: shown without being confirmed open


def pin_today(case, day: date = FIXTURE_TODAY) -> None:
    """Searches in this test case judge freshness on `day`, not on the day the suite runs."""
    patcher = patch('app.services.career_agent.today_ist', return_value=day)
    patcher.start()
    case.addCleanup(patcher.stop)
