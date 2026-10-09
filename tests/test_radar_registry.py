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
    def test_seed_v2_adds_the_twelve_keka_tenants_disabled_and_changes_nothing_else(self):
        old, new = registry.load_seed(1).companies, registry.load_seed().companies
        self.assertEqual(registry.SEED_VERSION, 2)
        self.assertEqual([c.model_dump() for c in new[:len(old)]], [c.model_dump() for c in old])
        keka = new[len(old):]
        self.assertEqual(len(keka), 12)
        self.assertEqual({c.source.type for c in keka}, {"keka"})
        self.assertTrue(all(c.unofficial and c.reviewed and not c.enabled for c in keka))     # undocumented feed: opt-in (decision G)
        self.assertEqual(len({c.source.tenant for c in keka}), 12)
        self.assertEqual(sum((c.evidence.india or 0) > 0 for c in keka), 10)

    def test_seed_v1_is_valid_and_matches_the_approved_plan(self):
        seed = registry.load_seed(1)
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
        self.assertEqual(len(config.companies), 101)
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

    def _old_config(self) -> dict:
        """A file made from seed v1, with one edit by the user and one company they added."""
        data = json.loads(registry.load_seed(1).model_dump_json())
        data["companies"][0]["enabled"] = False
        data["companies"].append(_entry(id="my-own-company"))
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def test_a_newer_seed_adds_its_new_companies_and_keeps_everything_the_user_has(self):
        before = self._old_config()
        config = registry.load_config()
        self.assertEqual(config.seed_version, 2)
        self.assertEqual(len(config.companies), 90 + 12)
        kept = json.loads(config.model_dump_json())["companies"][:90]
        self.assertEqual([entry["id"] for entry in kept], [entry["id"] for entry in before["companies"]])
        self.assertFalse(config.companies[0].enabled)                                   # the edit is kept
        self.assertEqual(config.companies[89].id, "my-own-company")
        added = config.companies[90:]
        self.assertEqual({entry.source.type for entry in added}, {"keka"})
        self.assertFalse(any(entry.enabled for entry in added))                         # shipped disabled: nothing new is polled unasked

    def test_the_upgrade_backs_the_file_up_first_and_runs_once(self):
        self._old_config()
        original = self.path.read_bytes()
        registry.load_config()
        backups = list((self.path.parent / "backups").glob("*/company_radar.json"))
        self.assertEqual([backup.read_bytes() for backup in backups], [original])
        upgraded = self.path.read_bytes()
        registry.load_config()
        self.assertEqual(self.path.read_bytes(), upgraded)                               # idempotent
        self.assertEqual(len(list((self.path.parent / "backups").glob("*/company_radar.json"))), 1)

    def test_an_id_the_user_already_has_is_never_replaced(self):
        data = self._old_config()
        data["companies"].append(_entry(id="gokwik", name="My GoKwik entry"))
        self.path.write_text(json.dumps(data), encoding="utf-8")
        config = registry.load_config()
        mine = [entry for entry in config.companies if entry.id == "gokwik"]
        self.assertEqual([(entry.name, entry.source.type) for entry in mine], [("My GoKwik entry", "greenhouse")])
        self.assertEqual(len(config.companies), 91 + 11)

    def test_a_company_the_user_removed_is_not_brought_back(self):
        data = self._old_config()
        data["companies"] = [entry for entry in data["companies"] if entry["id"] != "accenture"]
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertNotIn("accenture", [entry.id for entry in registry.load_config().companies])

    def test_reading_without_writing_never_upgrades_the_file(self):
        self._old_config()
        original = self.path.read_bytes()
        self.assertEqual(len(registry.read_config().companies), 90 + 12)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse((self.path.parent / "backups").exists())

    def test_an_unreadable_local_file_is_an_error_not_a_silent_reset(self):
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(registry.RadarConfigError):
            registry.load_config()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
