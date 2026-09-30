"""M2 commit 1: the append-only job event log, job snapshots, CV versions and the career_v3_events
backfill (docs/M2_PLAN.md §1). Fictional data and temporary databases only."""
import hashlib
import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.storage import career_events, career_store, db

# The schema before career_v3_events, with one application per legacy case.
OLD_SCHEMA = """
CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE career_sessions (id TEXT PRIMARY KEY, intent_json TEXT NOT NULL, response_json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE career_jobs (id TEXT PRIMARY KEY, job_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE career_applications (id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES career_jobs(id),
    status TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    applied_at TEXT, outreach_state TEXT NOT NULL DEFAULT 'NONE');
CREATE TABLE career_contacts (id TEXT PRIMARY KEY, company TEXT NOT NULL, contact_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE career_outreach (draft_id INTEGER PRIMARY KEY, application_id TEXT NOT NULL REFERENCES career_applications(id),
    contact_id TEXT, short_message TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT);
INSERT INTO schema_migrations VALUES ('career_v1', 't'), ('career_outreach_contact_v2', 't');
"""
T0, T1, T2 = '2026-09-01T10:00:00+00:00', '2026-09-03T09:00:00+00:00', '2026-09-10T12:00:00+00:00'
# (application id, status, applied_at, notes)
LEGACY = [
    ('app-saved', 'SAVED', None, 'real-shape'),
    ('app-applied', 'APPLIED', T1, ''),
    ('app-interview', 'INTERVIEW', T1, 'went well'),
    ('app-discovered', 'DISCOVERED', None, ''),
    ('app-outreach-applied', 'OUTREACH_SENT', T1, ''),
    ('app-outreach-saved', 'OUTREACH_PREPARED', None, ''),
    ('app-skipped', 'SKIPPED', None, ''),
    ('app-unknown', 'SHORTLISTED_BY_HAND', None, ''),
]


def _job_json(index):
    return json.dumps({'company': f'ExampleCo {index}', 'title': 'Data Analyst', 'location': 'Pune'})


class _Store(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'agent.sqlite3'
        patcher = patch.object(career_store, 'DB_PATH', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)

    def rows(self, sql, *args):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(sql, args).fetchall()]


class SchemaTests(_Store):
    def test_fresh_database_has_the_event_tables_and_migration(self):
        self.assertEqual(career_store.list_applications(), [])
        tables = {row['name'] for row in self.rows("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertLessEqual({'job_events', 'job_snapshots', 'profile_versions'}, tables)
        versions = {row['version'] for row in self.rows('SELECT version FROM schema_migrations')}
        self.assertEqual(versions, {'career_v1', 'career_outreach_contact_v2', 'career_v3_events'})
        columns = {row['name'] for row in self.rows('PRAGMA table_info(career_applications)')}
        self.assertLessEqual({'applied_via', 'effort_minutes', 'follow_up_at'}, columns)

    def test_the_log_snapshots_and_cv_versions_are_append_only(self):
        job_id = career_store.upsert_job({'company': 'ExampleCo', 'title': 'Data Analyst', 'location': 'Pune'})
        snapshot = career_store.capture_snapshot(job_id, profile={'name': 'Test Student'}, features={'overall_score': 70})
        career_store.record_event(job_id, 'thumbs_up', snapshot_id=snapshot)
        with closing(sqlite3.connect(self.path)) as conn:
            for statement in ("UPDATE job_events SET note='x'", 'DELETE FROM job_events',
                              "UPDATE job_snapshots SET job_json='{}'", 'DELETE FROM job_snapshots',
                              "UPDATE profile_versions SET profile_json='{}'", 'DELETE FROM profile_versions'):
                with self.assertRaisesRegex(sqlite3.DatabaseError, 'append-only', msg=statement):
                    conn.execute(statement)
        self.assertEqual(len(career_store.job_events(job_id)), 1)

    def test_event_types_sources_and_request_ids_are_constrained(self):
        job_id = career_store.upsert_job({'company': 'ExampleCo', 'title': 'Data Analyst', 'location': 'Pune'})
        with self.assertRaises(ValueError):
            career_store.record_event(job_id, 'shortlisted')
        with self.assertRaises(ValueError):
            career_store.record_event(job_id, 'saved', source='robot')
        with self.assertRaises(ValueError):
            career_store.record_event('missing-job', 'saved')
        career_store.record_event(job_id, 'thumbs_down', request_id='req-1')
        with self.assertRaises(sqlite3.IntegrityError):
            career_store.record_event(job_id, 'thumbs_up', request_id='req-1')
        self.assertEqual(set(career_events.EVENT_TYPES), {
            'saved', 'applied', 'online_test', 'interview', 'offer', 'rejected', 'withdrawn', 'skipped',
            'recruiter_reply', 'no_response_confirmed', 'outreach_prepared', 'outreach_sent',
            'thumbs_up', 'thumbs_down', 'thumbs_cleared', 'removed_from_results', 'undone'})

    def test_an_undone_event_must_name_what_it_undoes(self):
        job_id = career_store.upsert_job({'company': 'ExampleCo', 'title': 'Data Analyst', 'location': 'Pune'})
        with self.assertRaises(ValueError):
            career_store.record_event(job_id, 'undone')
        first = career_store.record_event(job_id, 'thumbs_up')
        undo = career_store.record_event(job_id, 'undone', undoes_event_id=first['id'])
        self.assertEqual(undo['undoes_event_id'], first['id'])


class SnapshotTests(_Store):
    def test_an_unchanged_job_reuses_its_snapshot_and_a_change_makes_a_new_one(self):
        job = {'company': 'ExampleCo', 'title': 'Data Analyst', 'location': 'Pune'}
        job_id = career_store.upsert_job(job)
        profile = {'name': 'Test Student', 'skills': ['Python']}
        first = career_store.capture_snapshot(job_id, profile=profile, features={'overall_score': 70})
        self.assertEqual(career_store.capture_snapshot(job_id, profile=profile, features={'overall_score': 70}), first)
        career_store.upsert_job({**job, 'description': 'Now with SQL'})
        changed = career_store.capture_snapshot(job_id, profile=profile, features={'overall_score': 70})
        self.assertNotEqual(changed, first)
        new_cv = career_store.capture_snapshot(job_id, profile={**profile, 'skills': ['Python', 'SQL']},
                                               features={'overall_score': 70})
        self.assertNotEqual(new_cv, changed)
        row = self.rows('SELECT * FROM job_snapshots WHERE id=?', first)[0]
        self.assertEqual(row['job_sha256'], hashlib.sha256(row['job_json'].encode()).hexdigest())
        self.assertEqual(json.loads(row['job_json']), job)
        self.assertEqual(row['captured_late'], 0)
        self.assertEqual(row['feature_schema_version'], career_events.FEATURE_SCHEMA_VERSION)
        self.assertEqual(row['vocabulary_version'], '2')
        self.assertEqual(row['ranker_version'], career_events.ranker_version())
        self.assertIsNone(row['model_version'])

    def test_the_cv_version_is_the_hash_of_the_canonical_profile(self):
        job_id = career_store.upsert_job({'company': 'ExampleCo', 'title': 'Data Analyst', 'location': 'Pune'})
        career_store.capture_snapshot(job_id, profile={'skills': ['Python'], 'name': 'Test Student'}, features={})
        versions = self.rows('SELECT * FROM profile_versions')
        canonical = json.dumps({'name': 'Test Student', 'skills': ['Python']}, sort_keys=True, separators=(',', ':'))
        self.assertEqual([row['cv_version'] for row in versions], [hashlib.sha256(canonical.encode()).hexdigest()])


class RankerVersionTests(unittest.TestCase):
    def setUp(self):
        career_events.ranker_version.cache_clear()
        self.addCleanup(career_events.ranker_version.cache_clear)

    def test_it_is_v1_plus_the_short_sha_when_git_is_available(self):
        done = subprocess.CompletedProcess([], 0, stdout='abc1234\n', stderr='')
        with patch.object(career_events.subprocess, 'run', return_value=done):
            self.assertEqual(career_events.ranker_version(), 'v1+abc1234')

    def test_it_is_plain_v1_without_git(self):
        with patch.object(career_events.subprocess, 'run', side_effect=FileNotFoundError('git')):
            self.assertEqual(career_events.ranker_version(), 'v1')


class StatusFoldTests(unittest.TestCase):
    def event(self, identity, event_type, undoes=None):
        return {'id': identity, 'event_type': event_type, 'undoes_event_id': undoes, 'occurred_at': T0}

    def test_the_latest_funnel_event_that_is_not_undone_sets_the_status(self):
        fold = career_events.status_from_events
        self.assertIsNone(fold([]))
        self.assertIsNone(fold([self.event(1, 'thumbs_up')]))
        self.assertEqual(fold([self.event(1, 'saved'), self.event(2, 'applied')]), 'APPLIED')
        self.assertEqual(fold([self.event(1, 'applied'), self.event(2, 'recruiter_reply'),
                               self.event(3, 'outreach_sent')]), 'APPLIED', 'responses and outreach keep the status')
        self.assertEqual(fold([self.event(1, 'saved'), self.event(2, 'applied'), self.event(3, 'undone', 2)]), 'SAVED')
        self.assertEqual(fold([self.event(1, 'applied'), self.event(2, 'interview'), self.event(3, 'rejected')]),
                         'REJECTED')


class BackfillTests(_Store):
    def setUp(self):
        super().setUp()
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript(OLD_SCHEMA)
            for index, (identity, status, applied_at, notes) in enumerate(LEGACY):
                conn.execute('INSERT INTO career_jobs VALUES (?, ?, ?, ?)', (f'job-{index}', _job_json(index), T0, T0))
                conn.execute('INSERT INTO career_applications VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                             (identity, f'job-{index}', status, notes, T0, T2, applied_at, 'NONE'))
            # An orphan row from an older build: kept, never migrated into events.
            conn.execute('INSERT INTO career_applications VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                         ('app-orphan', 'job-missing', 'SAVED', '', T0, T0, None, 'NONE'))
        self.migrated = career_store.list_applications()

    def events(self, application_id):
        return self.rows('SELECT * FROM job_events WHERE application_id=? ORDER BY id', application_id)

    def test_every_row_is_kept_and_a_backup_is_taken(self):
        self.assertEqual(len(self.migrated), len(LEGACY) + 1)
        self.assertEqual(len(self.rows('SELECT * FROM career_jobs')), len(LEGACY))
        notes = {row['id']: row['notes'] for row in self.migrated}
        self.assertEqual(notes['app-saved'], 'real-shape')
        self.assertEqual(notes['app-interview'], 'went well')
        self.assertEqual(len(list((self.path.parent / 'backups').rglob('agent.sqlite3'))), 1)

    def test_the_real_shape_saved_application_gets_exactly_one_event(self):
        [event] = self.events('app-saved')
        self.assertEqual((event['event_type'], event['occurred_at'], event['occurred_at_exact'], event['source']),
                         ('saved', T0, 0, 'migration'))
        self.assertEqual(event['job_id'], 'job-0')
        self.assertIsNotNone(event['snapshot_id'])

    def test_each_case_is_backfilled_with_estimated_times(self):
        expected = {
            'app-applied': [('saved', T0), ('applied', T1)],
            'app-interview': [('saved', T0), ('applied', T1), ('interview', T2)],
            'app-discovered': [('saved', T0)],
            'app-outreach-applied': [('saved', T0), ('applied', T1)],
            'app-outreach-saved': [('saved', T0)],
            'app-skipped': [('saved', T0), ('skipped', T2)],
            'app-unknown': [('saved', T0)],
        }
        for application_id, sequence in expected.items():
            events = self.events(application_id)
            self.assertEqual([(event['event_type'], event['occurred_at']) for event in events], sequence, application_id)
            self.assertTrue(all(event['occurred_at_exact'] == 0 and event['source'] == 'migration' for event in events))

    def test_statuses_are_remapped_and_unknown_ones_kept_and_reported(self):
        status = {row['id']: row['status'] for row in self.migrated}
        self.assertEqual(status, {
            'app-saved': 'SAVED', 'app-applied': 'APPLIED', 'app-interview': 'INTERVIEW',
            'app-discovered': 'SAVED', 'app-outreach-applied': 'APPLIED', 'app-outreach-saved': 'SAVED',
            'app-skipped': 'SKIPPED', 'app-unknown': 'SHORTLISTED_BY_HAND', 'app-orphan': 'SAVED'})
        for application_id, legacy in (('app-discovered', 'DISCOVERED'), ('app-outreach-applied', 'OUTREACH_SENT'),
                                       ('app-outreach-saved', 'OUTREACH_PREPARED'), ('app-unknown', 'SHORTLISTED_BY_HAND')):
            self.assertIn(legacy, self.events(application_id)[-1]['note'], application_id)
        self.assertEqual(self.events('app-orphan'), [], 'an orphan row cannot reference a job; it is left as it was')

    def test_each_application_gets_one_late_snapshot_of_the_stored_job(self):
        snapshots = self.rows('SELECT * FROM job_snapshots ORDER BY id')
        self.assertEqual(len(snapshots), len(LEGACY))
        for snapshot in snapshots:
            self.assertEqual(snapshot['captured_late'], 1)
            self.assertIsNone(snapshot['cv_version'], 'the CV used at the time is unknown')
            self.assertIsNone(snapshot['ranker_version'])
            self.assertEqual(snapshot['job_json'], _job_json(int(snapshot['job_id'].split('-')[1])))
        for event in self.rows('SELECT * FROM job_events'):
            self.assertIsNotNone(event['snapshot_id'])

    def test_a_second_run_changes_nothing(self):
        before = self.rows('SELECT * FROM job_events ORDER BY id')
        db.reset_cache()
        self.assertEqual(db.migrate(self.path, career_store.MIGRATIONS), [])
        career_store.list_applications()
        self.assertEqual(self.rows('SELECT * FROM job_events ORDER BY id'), before)
        self.assertEqual(len(list((self.path.parent / 'backups').rglob('agent.sqlite3'))), 1)


if __name__ == '__main__':
    unittest.main()
