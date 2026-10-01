"""M2-8b: a label batch built from a frozen index copy (docs/ROADMAP_QUEUE.md Q2). A small temporary
index stands in for the real one; fictional data only, and no live file is read or written."""
import gc
import hashlib
import importlib.util
import json
import os
import stat
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.models.schemas import CandidateProfile
from app.providers import radar_provider
from app.sources.adapters import posting
from app.storage import career_events, career_store, db, history, preference_store, profile_store, radar_store, search_cache
from radar_helpers import company
from test_radar_provider import FRESHER

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'SENTINEL-PRIVATE-CV-LINE'
SENIOR = 'Requires 10+ years of experience leading teams. Python, PyTorch and SQL.'
SEARCHES = [('A', 'AI Engineer jobs for freshers in India', 'target', 5),
            ('D', 'Sales Executive jobs in India', 'control', 2)]
BLIND_KEYS = {'item_id', 'title', 'company', 'location', 'work_mode', 'employment_type', 'posted_date', 'salary',
              'description', 'application_url'}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


class BuildLabelBatchTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(self._make_writable, Path(directory.name))
        self.addCleanup(gc.collect)
        self.root = Path(directory.name)
        self.live = {'radar': self.root / 'radar.sqlite3', 'agent': self.root / 'agent.sqlite3',
                     'history': self.root / 'history.sqlite3', 'cache': self.root / 'search_cache.sqlite3'}
        for module, name, value in ((radar_store, 'DB_PATH', self.live['radar']), (career_store, 'DB_PATH', self.live['agent']),
                                    (history, 'DB_PATH', self.live['history']), (search_cache, 'DB_PATH', self.live['cache']),
                                    (profile_store, 'PROFILE_PATH', self.root / 'profile.json'),
                                    (preference_store, 'PREFERENCES_PATH', self.root / 'preferences.json')):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        profile_store.save_profile(CandidateProfile(name='Test Student', summary=SECRET, graduation_year=2025, experience_years=0,
                                                    skills=['Python', 'PyTorch'], preferred_roles=['AI Engineer'],
                                                    preferred_locations=['Pune', 'Kolkata']))
        entry = company({'type': 'greenhouse', 'board': 'b'}, id='b', name='Example')
        jobs = []

        def add(title, description, count, posted):
            for _ in range(count):
                index = len(jobs)
                jobs.append(posting(entry, job_id=str(index), title=title, location='Pune, India', description=description,
                                    url=f'https://job-boards.greenhouse.io/b/jobs/{index}', posted=posted))
        # Last listed three days before the snapshot day (2026-10-01), so none is confirmed open and age decides.
        add('AI Engineer', FRESHER, 3, '2026-09-20')
        add('AI Engineer', SENIOR, 8, '2026-09-05')
        add('AI Engineer', FRESHER, 2, '2026-08-15')    # 47 days: "check before applying"
        add('AI Engineer', FRESHER, 2, '2026-06-01')    # stale: hidden, never labelled
        add('AI Engineer', SENIOR, 1, '2026-06-01')     # stale and ineligible: not in the excluded pool
        add('Sales Executive', 'Freshers welcome: 0-1 years of experience. Sell to local shops.', 2, '2026-09-25')
        add('Sales Executive', SENIOR, 1, '2026-09-10')
        radar_store.record_listing(entry.id, jobs, complete=True, today=date(2026, 9, 28))
        db.reset_cache()
        self.frozen = _load('freeze_index').freeze(self.live['radar'], self.root / 'eval' / 'frozen', stamp='20261001T000000Z')
        self.batches = self.root / 'eval' / 'batches'
        self.builder = _load('build_label_batch')
        self.before = {name: _sha(path) for name, path in self.live.items() if path.exists()}
        self.frozen_sha = _sha(self.frozen / 'radar.sqlite3')

    @staticmethod
    def _make_writable(root: Path):
        for path in root.rglob('*'):
            if path.is_file():
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)

    def build(self, batch_id='b1', seed=7, searches=None):
        return self.builder.build(self.frozen, self.batches, seed=seed, batch_id=batch_id, searches=searches or SEARCHES)

    def test_the_real_search_list_is_the_confirmed_one(self):
        self.assertEqual(self.builder.SEARCHES, [
            ('A', 'AI Engineer jobs for freshers in India', 'target', 15),
            ('B', 'Software Engineer fresher jobs in India', 'target', 15),
            ('C', 'Data Analyst jobs for freshers in India', 'target', 15),
            ('D', 'Sales Executive jobs in India', 'control', 10)])
        self.assertEqual(self.builder.RUBRIC_VERSION, 'r1')

    def test_the_batch_is_one_line_per_distinct_job(self):
        folder = self.build()
        self.assertEqual(folder, self.batches / 'b1')
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ['blind.jsonl', 'jobs.jsonl', 'meta.json'])
        jobs = _lines(folder / 'jobs.jsonl')
        self.assertEqual(len({job['job_id'] for job in jobs}), len(jobs))
        self.assertEqual(len({job['item_id'] for job in jobs}), len(jobs))
        shown = [job for job in jobs if any(entry['stage'] == 'post_filter' for entry in job['surfaced_by'])]
        sampled = [job for job in jobs if all(entry['stage'] == 'excluded_sample' for entry in job['surfaced_by'])]
        self.assertEqual((len(shown), len(sampled)), (7, 6), '5 AI + 2 sales shown; 5 of 8 and 1 of 1 excluded sampled')
        self.assertEqual(sorted(job['freshness']['decision'] for job in jobs), ['check'] * 2 + ['show'] * 11,
                         'no stale job is in the batch')
        for job in shown:
            self.assertIn(job['eligibility_status'], ('eligible', 'uncertain'))
            self.assertTrue(all(isinstance(entry['rank'], int) and entry['rank'] >= 1 for entry in job['surfaced_by']))
        for job in sampled:
            self.assertEqual(job['eligibility_status'], 'excluded')
            self.assertTrue(job['eligibility_summary'])
            self.assertEqual([entry['rank'] for entry in job['surfaced_by']], [None])
        for job in jobs:
            self.assertEqual(job['job_sha256'], hashlib.sha256(
                json.dumps(job['job'], sort_keys=True, separators=(',', ':')).encode()).hexdigest())
            self.assertIn('L0', job['score_claim_level'])
        ranks = sorted(entry['rank'] for job in shown for entry in job['surfaced_by'] if entry['search'] == 'A')
        self.assertEqual(ranks, [1, 2, 3, 4, 5])

    def test_excluded_jobs_come_from_the_search_itself(self):
        source = (ROOT / 'scripts' / 'build_label_batch.py').read_text(encoding='utf-8')
        self.assertIn('return_excluded=True', source)
        self.assertNotIn('evaluate_job', source, 'no runtime wrapper around the search internals')

    def test_the_blind_view_hides_everything_the_ranker_said(self):
        folder = self.build()
        blind, jobs = _lines(folder / 'blind.jsonl'), _lines(folder / 'jobs.jsonl')
        self.assertEqual({row['item_id'] for row in blind}, {job['item_id'] for job in jobs})
        for row in blind:
            self.assertEqual(set(row), BLIND_KEYS)
        text = (folder / 'blind.jsonl').read_text(encoding='utf-8')
        for word in ('eligibility', 'score', 'rank', 'surfaced', 'stage', 'job_id', 'excluded_sample'):
            self.assertNotIn(word, text)
        by_id = {job['item_id']: job for job in jobs}
        stages = [by_id[row['item_id']]['surfaced_by'][0]['stage'] for row in blind]
        self.assertNotEqual(stages, sorted(stages), 'shown and excluded jobs are interleaved')
        self.assertNotEqual(stages, sorted(stages, reverse=True))

    def test_the_same_seed_gives_the_same_batch_and_another_seed_does_not(self):
        first, again, other = self.build('b1', 7), self.build('b2', 7), self.build('b3', 8)
        for name in ('blind.jsonl', 'jobs.jsonl'):
            self.assertEqual((first / name).read_bytes(), (again / name).read_bytes(), name)
        self.assertNotEqual((first / 'blind.jsonl').read_bytes(), (other / 'blind.jsonl').read_bytes())

    def test_meta_records_what_the_batch_was_built_from(self):
        folder = self.build()
        meta = json.loads((folder / 'meta.json').read_text(encoding='utf-8'))
        manifest = json.loads((self.frozen / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['batch_id'], 'b1')
        self.assertEqual(meta['frozen'], {'stamp': '20261001T000000Z', 'sha256': manifest['sha256']})
        self.assertEqual((meta['rubric_version'], meta['seed'], meta['label_unit']), ('r1', 7, 'job'))
        self.assertEqual(meta['ranker_version'], career_events.ranker_version())
        self.assertRegex(meta['cv_version'], r'^[0-9a-f]{64}$')
        self.assertRegex(meta['preferences_sha256'], r'^[0-9a-f]{64}$')
        self.assertTrue(meta['vocabulary_version'])
        self.assertEqual(meta['counts'], {'distinct_jobs': 13, 'post_filter_jobs': 7, 'excluded_sample_jobs': 6})
        self.assertEqual((meta['today'], meta['max_age_days'], meta['check_age_days']), ('2026-10-01', 30, 60),
                         'freshness is judged on the day the copy was frozen, in India time')
        self.assertEqual(meta['files'], {name: _sha(folder / name) for name in ('blind.jsonl', 'jobs.jsonl')})
        first, control = meta['searches']
        self.assertEqual({key: first[key] for key in ('id', 'query', 'role', 'locations', 'index_matches', 'match_cap', 'cap_hit',
                                                      'oldest_posted_date', 'stale_hidden', 'excluded_by_eligibility', 'results',
                                                      'check_before_applying', 'excluded_sample_requested', 'excluded_sampled')},
                         {'id': 'A', 'query': 'AI Engineer jobs for freshers in India', 'role': 'target', 'locations': ['India'],
                          'index_matches': 16, 'match_cap': radar_provider.MAX_RESULTS, 'cap_hit': False,
                          'oldest_posted_date': '2026-06-01', 'stale_hidden': 3, 'excluded_by_eligibility': 8, 'results': 5,
                          'check_before_applying': 2, 'excluded_sample_requested': 5, 'excluded_sampled': 5})
        self.assertEqual(first['eligible'] + first['uncertain'], 5)
        self.assertEqual((control['id'], control['results'], control['excluded_sampled'], control['excluded_sample_requested']),
                         ('D', 2, 1, 2), 'a pool smaller than the sample is taken whole')

    def test_the_cap_is_recorded_when_it_is_hit(self):
        with patch.object(radar_provider, 'MAX_RESULTS', 4):
            folder = self.build()
        first = json.loads((folder / 'meta.json').read_text(encoding='utf-8'))['searches'][0]
        self.assertEqual((first['index_matches'], first['match_cap'], first['cap_hit']), (4, 4, True))

    def test_no_cv_text_reaches_the_batch(self):
        folder = self.build()
        for path in folder.iterdir():
            text = path.read_text(encoding='utf-8')
            self.assertNotIn(SECRET, text, path.name)
            self.assertNotIn('Test Student', text, path.name)

    def test_nothing_else_is_written(self):
        folder = self.build()
        self.assertEqual(_sha(self.frozen / 'radar.sqlite3'), self.frozen_sha)
        self.assertEqual(sorted(path.name for path in self.frozen.iterdir()), ['manifest.json', 'radar.sqlite3'])
        self.assertEqual({name: _sha(path) for name, path in self.live.items() if path.exists()}, self.before,
                         'the live index is untouched and no live app database is created')
        self.assertEqual([path.name for path in self.batches.iterdir()], ['b1'], 'the scratch folder is removed')
        for path in folder.iterdir():
            self.assertFalse(os.access(path, os.W_OK), path.name)

    def test_an_existing_batch_is_never_overwritten(self):
        folder = self.build()
        before = {path.name: _sha(path) for path in folder.iterdir()}
        with self.assertRaisesRegex(FileExistsError, 'already exists'):
            self.build(seed=8)
        self.assertEqual({path.name: _sha(path) for path in folder.iterdir()}, before)

    def test_a_frozen_copy_that_does_not_match_its_manifest_is_refused(self):
        manifest = self.frozen / 'manifest.json'
        os.chmod(manifest, stat.S_IWRITE | stat.S_IREAD)
        details = json.loads(manifest.read_text(encoding='utf-8'))
        manifest.write_text(json.dumps({**details, 'sha256': '0' * 64}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'manifest'):
            self.build()
        self.assertFalse(self.batches.exists())

    def test_a_search_without_in_india_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'India'):
            self.build(searches=[('A', 'AI Engineer fresher', 'target', 5)])
        self.assertFalse((self.batches / 'b1').exists())
        self.assertEqual(list(self.batches.iterdir()) if self.batches.exists() else [], [])


if __name__ == '__main__':
    unittest.main()
