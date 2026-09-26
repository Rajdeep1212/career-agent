"""Company Radar seed and local config: validated, evidence-backed, never overwritten."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app.sources import registry


def _entry(**overrides):
    entry = {"id": "example", "name": "Example", "tags": ["big_tech"], "source": {"type": "greenhouse", "board": "example"},
             "evidence": {"method": "api_probe", "checked_at": "2026-09-26", "total": 10, "india": 3},
             "enabled": True, "reviewed": True}
    entry.update(overrides)
    return entry


class SeedTests(unittest.TestCase):
    def test_seed_v1_is_valid_and_matches_the_approved_plan(self):
        seed = registry.load_seed()
        companies = seed.companies
        self.assertEqual(len(companies), 89)
        self.assertEqual(sum(c.enabled for c in companies), 78)
        self.assertEqual({c.id for c in companies if c.unofficial}, {"amazon", "capgemini", "dell", "oracle"})
        self.assertEqual(sum(c.source.type == "none" for c in companies), 7)
        self.assertTrue(all(c.reviewed for c in companies))

    def test_every_entry_has_dated_evidence(self):
        for company in registry.load_seed().companies:
            self.assertTrue(company.evidence.method, company.id)
            self.assertTrue(company.evidence.checked_at, company.id)

    def test_opt_in_and_sourceless_entries_ship_disabled(self):
        for company in registry.load_seed().companies:
            if company.unofficial or company.source.type == "none":
                self.assertFalse(company.enabled, company.id)


class SchemaTests(unittest.TestCase):
    def test_source_fields_are_required_per_type(self):
        for source in ({"type": "greenhouse"}, {"type": "lever"}, {"type": "smartrecruiters"},
                       {"type": "workday", "host": "cisco.wd5.myworkdayjobs.com"},
                       {"type": "sitemap_jsonld", "sitemaps": []}, {"type": "undocumented_json"}):
            with self.subTest(source=source), self.assertRaises(ValidationError):
                registry.CompanyEntry.model_validate(_entry(source=source))

    def test_workday_host_must_be_a_workday_tenant(self):
        with self.assertRaises(ValidationError):
            registry.CompanyEntry.model_validate(_entry(source={"type": "workday", "host": "evil.example", "sites": ["x"]}))

    def test_undocumented_sources_are_marked_unofficial(self):
        with self.assertRaises(ValidationError):
            registry.CompanyEntry.model_validate(_entry(source={"type": "undocumented_json", "endpoint": "https://x.example/api"}))

    def test_ids_are_unique(self):
        with self.assertRaises(ValidationError):
            registry.RadarConfig.model_validate({"version": 1, "seed_version": 1, "companies": [_entry(), _entry()]})


class LocalConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "company_radar.json"
        patcher = patch.object(registry, "CONFIG_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_bootstrap_copies_the_seed_once_and_never_overwrites(self):
        config = registry.load_config()
        self.assertTrue(self.path.exists())
        self.assertEqual(len(config.companies), 89)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["companies"][0]["enabled"] = False
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertFalse(registry.load_config().companies[0].enabled)

    def test_only_enabled_reviewed_entries_with_a_source_sync(self):
        registry.save_config(registry.RadarConfig(version=1, seed_version=1, companies=[
            registry.CompanyEntry.model_validate(_entry(id="ok")),
            registry.CompanyEntry.model_validate(_entry(id="unreviewed", reviewed=False)),
            registry.CompanyEntry.model_validate(_entry(id="disabled", enabled=False)),
            registry.CompanyEntry.model_validate(_entry(id="nosource", enabled=False, source={"type": "none"})),
        ]))
        self.assertEqual([c.id for c in registry.syncable(registry.load_config())], ["ok"])

    def test_an_unreadable_local_file_is_an_error_not_a_silent_reset(self):
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(registry.RadarConfigError):
            registry.load_config()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
