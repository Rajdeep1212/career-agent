import unittest
import tempfile
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from app.storage import history
from app.models.schemas import JobPosting


class HistoryTests(unittest.TestCase):
    def test_specific_jobs_remain_distinct_and_legacy_history_survives(self):
        with tempfile.TemporaryDirectory() as d, patch.object(history,'DB_PATH',Path(d)/'history.sqlite3'):
            a=JobPosting(company='Example',title='Analyst',location='India',application_url='https://example.com/jobs/1')
            b=a.model_copy(update={'application_url':'https://example.com/jobs/2'})
            history.mark_seen(a)
            self.assertTrue(history.is_seen(a))
            self.assertFalse(history.is_seen(b))
            with closing(sqlite3.connect(history.DB_PATH)) as conn,conn:
                conn.execute('INSERT INTO seen_jobs(fingerprint,company,title) VALUES(?,?,?)',(history.fingerprint(b),'Example','Analyst'))
            self.assertTrue(history.is_seen(b))
