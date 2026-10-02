"""Q2d: the read-only MCP tools. Hard rule: no tool result may contain CV text or text extracted from the CV.

A small temporary index and tracker stand in for the real ones; fictional data only. The CV-derived
profile is filled with sentinel strings, and every tool result is searched for them.
"""
import gc
import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.mcp import tools
from app.models.schemas import CandidateProfile
from app.sources.adapters import posting
from app.storage import career_store, db, profile_store, radar_store
from radar_helpers import company

TODAY = date(2026, 10, 1)
MARK = "sentinel"       # every CV-derived string below contains it
FRESHER = "Freshers and recent graduates welcome. You will build LLM evaluation tools with Python, PyTorch and SQL."
SENIOR = "Requires 10+ years of experience leading teams. Python, PyTorch and SQL."
TARGET = """# Target profile

- roles: AI Engineer, Machine Learning Engineer
- locations: Pune, Bengaluru
- graduation year: 2025
- experience years: 0

I want an entry-level role working with Python, PyTorch and SQL.
"""
CV_PROFILE = CandidateProfile(
    name="Sentinel Candidate Name", graduation_year=2025, degree="B.Tech in Sentinel Engineering",
    skills=["Python", "PyTorch", "SentinelSkillXYZ"], preferred_roles=["Sentinel Role Title"], preferred_locations=["Sentinelpur"],
    projects=["SENTINEL-PRIVATE-CV-LINE: built a fraud detector with Python, PyTorch and SQL for evaluation tools"],
    research=["Sentinel research paper about LLM evaluation tools"], experience=["Intern at SentinelCorp doing sentinel things"],
    evidence={"Python": ["sentinel evidence line from the CV"]})


class ToolCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(directory.name)
        self.radar, self.agent = self.root / "radar.sqlite3", self.root / "agent.sqlite3"
        self.target = self.root / "eval" / "target_profile.md"
        for module, name, value in ((radar_store, "DB_PATH", self.radar), (career_store, "DB_PATH", self.agent),
                                    (profile_store, "PROFILE_PATH", self.root / "current_profile.json"),
                                    (tools, "TARGET_PROFILE", self.target)):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        profile_store.save_profile(CV_PROFILE)
        entry = company({"type": "greenhouse", "board": "b"}, id="b", name="Example Corp")

        def job(index, title, description, posted, location="Pune, India"):
            return posting(entry, job_id=str(index), title=title, location=location, description=description,
                           url=f"https://job-boards.greenhouse.io/b/jobs/{index}", posted=posted)
        self.jobs = [job(1, "Graduate AI Engineer", FRESHER, "2026-09-20"), job(2, "Senior AI Engineer", SENIOR, "2026-09-20"),
                     job(3, "AI Engineer", FRESHER, "2026-06-01"), job(4, "Sales Executive", "Sell things.", "2026-09-20"),
                     job(5, "Junior AI Engineer", FRESHER, "2026-09-25", location="Kolkata, India")]
        radar_store.record_listing("b", self.jobs, complete=True, today=date(2026, 9, 28))
        # A tracker row whose stored job, notes and outreach all carry CV-derived text.
        saved = {**self.jobs[0].model_dump(mode="json"),
                 "match": {"explanation": "Relevant candidate evidence: SENTINEL-PRIVATE-CV-LINE", "matched_skills": ["SentinelSkillXYZ"]}}
        self.job_id = career_store.upsert_job(saved)
        self.application = career_store.save_application(self.job_id, "SAVED", "sentinel note copied from my CV")
        career_store.link_outreach(self.application["id"], 7, "Hi, I built a sentinel fraud detector (from my CV).")

    def write_target(self, text=TARGET):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_text(text, encoding="utf-8")

    def assert_no_cv_text(self, result):
        text = json.dumps(result).casefold()
        self.assertNotIn(MARK, text)
        return text


class SearchJobsTests(ToolCase):
    def test_fresh_eligible_jobs_come_back_with_fit_freshness_and_link(self):
        self.write_target()
        result = tools.search_jobs("AI Engineer jobs for freshers in India", today=TODAY)
        titles = [job["title"] for job in result["jobs"]]
        self.assertEqual(sorted(titles), ["Graduate AI Engineer", "Junior AI Engineer"])      # not senior, stale or sales
        first = next(job for job in result["jobs"] if job["title"] == "Graduate AI Engineer")
        self.assertEqual(first["job_id"], "b:1")
        self.assertEqual(first["link"], "https://job-boards.greenhouse.io/b/jobs/1")
        self.assertEqual(first["fit"]["claim_level"], "L0")
        self.assertEqual(first["fit"]["label"], f"Heuristic fit {first['fit']['score']}/100")
        self.assertGreater(first["fit"]["score"], 0)
        self.assertEqual((first["freshness"]["decision"], first["freshness"]["age_days"]), ("show", 11))
        self.assertIn(first["eligibility"]["status"], ("eligible", "uncertain"))
        self.assertEqual(result["profile_source"], "data/eval/target_profile.md")
        self.assert_no_cv_text(result)

    def test_city_age_and_limit_narrow_the_result(self):
        self.write_target()
        pune = tools.search_jobs("AI Engineer", city="Pune", today=TODAY)
        self.assertEqual([job["title"] for job in pune["jobs"]], ["Graduate AI Engineer"])
        recent = tools.search_jobs("AI Engineer jobs in India", max_age_days=7, today=TODAY)
        self.assertEqual([job["title"] for job in recent["jobs"]], ["Junior AI Engineer"])
        one = tools.search_jobs("AI Engineer jobs in India", limit=1, today=TODAY)
        self.assertEqual((len(one["jobs"]), one["matched"]), (1, 2))

    def test_without_a_target_profile_there_is_no_fit_score_and_the_cv_is_not_used_instead(self):
        result = tools.search_jobs("AI Engineer jobs for freshers in India", today=TODAY)
        self.assertEqual(len(result["jobs"]), 2)
        self.assertTrue(all(job["fit"] is None for job in result["jobs"]))
        self.assertIsNone(result["profile_source"])
        self.assertIn("data/eval/target_profile.md", " ".join(result["notes"]))
        self.assert_no_cv_text(result)

    def test_the_result_does_not_depend_on_the_cv_profile_at_all(self):
        self.write_target()
        with_cv = tools.search_jobs("AI Engineer jobs for freshers in India", today=TODAY)
        profile_store.PROFILE_PATH.unlink()
        without_cv = tools.search_jobs("AI Engineer jobs for freshers in India", today=TODAY)
        self.assertEqual(with_cv, without_cv)


class ExplainFitTests(ToolCase):
    def test_the_explanation_quotes_only_the_job_description(self):
        self.write_target()
        result = tools.explain_fit("b:1", today=TODAY)
        self.assertEqual(result["title"], "Graduate AI Engineer")
        self.assertEqual(result["fit"]["label"], f"Heuristic fit {result['fit']['score']}/100")
        quotes = [item["quote"] for item in result["eligibility"]["evidence"] if item["quote"]] + \
                 [item["quote"] for item in result["skills_in_listing"]]
        self.assertTrue(quotes)
        listing = f"{self.jobs[0].title}. {self.jobs[0].description}"
        for quote in quotes:
            self.assertIn(quote, listing)
        self.assertEqual({item["skill"] for item in result["skills_in_listing"] if item["in_target_profile"]}, {"Python", "PyTorch", "SQL"})
        self.assert_no_cv_text(result)

    def test_an_excluded_job_says_why_with_the_listing_text(self):
        self.write_target()
        result = tools.explain_fit("b:2", today=TODAY)
        self.assertEqual(result["eligibility"]["status"], "excluded")
        self.assertIn("10+ years", json.dumps(result["eligibility"]["evidence"]))
        self.assert_no_cv_text(result)

    def test_an_unknown_job_and_a_missing_target_profile(self):
        self.assertIn("error", tools.explain_fit("b:999", today=TODAY))
        result = tools.explain_fit("b:1", today=TODAY)
        self.assertIsNone(result["fit"])
        self.assert_no_cv_text(result)


class ListApplicationsTests(ToolCase):
    def test_rows_hold_tracker_and_job_facts_only(self):
        result = tools.list_applications()
        self.assertEqual(result["count"], 1)
        row = result["applications"][0]
        self.assertEqual(set(row), {"application_id", "status", "created_at", "updated_at", "applied_at", "response_state",
                                    "shortlisted", "last_event", "job"})
        self.assertEqual(set(row["job"]), {"job_id", "title", "company", "location", "link"})
        self.assertEqual((row["status"], row["job"]["title"], row["job"]["company"]), ("SAVED", "Graduate AI Engineer", "Example Corp"))
        self.assert_no_cv_text(result)      # notes, outreach text and the stored match are all left out

    def test_the_status_filter(self):
        self.assertEqual(tools.list_applications("saved")["count"], 1)
        self.assertEqual(tools.list_applications("APPLIED")["count"], 0)
        self.assertIn("error", tools.list_applications("dreaming"))


class HardRuleTests(ToolCase):
    """Each tool, called the way the server calls it, with a CV full of sentinel strings."""

    def calls(self):
        return [("search_jobs", {"query": "AI Engineer jobs for freshers in India"}), ("explain_fit", {"job_id": "b:1"}),
                ("explain_fit", {"job_id": "b:2"}), ("list_applications", {}), ("list_applications", {"status": "SAVED"})]

    def test_job_data_that_shares_a_role_title_or_degree_with_the_profile_is_not_blocked(self):
        # Found on the live index: a preferred role in the stored profile was also a job title, and every search was blocked.
        # Role titles, places, skills, degrees and certificate names are ordinary job vocabulary; the check covers the
        # CV's own prose (projects, research, experience, internships, evidence) and the name.
        profile_store.save_profile(CV_PROFILE.model_copy(update={
            "preferred_roles": ["Graduate AI Engineer"], "preferred_locations": ["Pune, India and nearby"],
            "degree": "Freshers and recent graduates welcome", "certifications": ["Python, PyTorch and SQL certificate"],
            "education": ["LLM evaluation tools with Python"], "domain_knowledge": ["LLM evaluation tools with Python"]}))
        self.write_target()
        with patch.object(tools, "today_ist", return_value=TODAY):
            for name, arguments in self.calls():
                with self.subTest(tool=name):
                    self.assertNotIn("error", tools.call(name, arguments))
            titles = [job["title"] for job in tools.call("search_jobs", {"query": "AI Engineer jobs in India"})["jobs"]]
        self.assertIn("Graduate AI Engineer", titles)

    def test_no_tool_result_contains_cv_text_with_or_without_a_target_profile(self):
        for target in (False, True):
            if target:
                self.write_target()
            for name, arguments in self.calls():
                with self.subTest(tool=name, arguments=arguments, target_profile=target):
                    with patch.object(tools, "today_ist", return_value=TODAY):
                        result = tools.call(name, arguments)
                    self.assertNotIn("error", result)
                    self.assert_no_cv_text(result)

    def test_a_cv_string_that_reaches_a_result_is_blocked(self):
        leak = CV_PROFILE.projects[0]
        for name in ("search_jobs", "explain_fit", "list_applications"):
            with self.subTest(tool=name), patch.dict(tools.HANDLERS, {name: lambda **arguments: {"jobs": [{"title": f"x {leak} y"}]}}):
                result = tools.call(name, {})
                self.assertEqual(set(result), {"error"})
                self.assertIn("blocked", result["error"])
                self.assert_no_cv_text(result)
        with patch.dict(tools.HANDLERS, {"search_jobs": lambda **arguments: {"note": "by sentinel candidate name"}}):
            self.assertIn("error", tools.call("search_jobs", {}))      # the name, in any letter case

    def test_no_tool_writes_anything(self):
        self.write_target()
        gc.collect()

        def state():
            return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(self.root.rglob("*")) if path.is_file()
                    and not path.name.endswith(("-shm", "-wal"))}
        before = state()
        with patch.object(tools, "today_ist", return_value=TODAY):
            for name, arguments in self.calls():
                tools.call(name, arguments)
        self.assertEqual(state(), before)

    def test_unknown_tools_and_bad_arguments_are_errors_not_crashes(self):
        self.assertIn("error", tools.call("delete_everything", {}))
        self.assertIn("error", tools.call("search_jobs", {}))
        self.assertIn("error", tools.call("search_jobs", {"query": "AI Engineer", "limit": "many"}))
        self.assertIn("error", tools.call("explain_fit", {"job_id": "b:1", "cv": "please"}))


class TargetProfileTests(ToolCase):
    def test_the_target_profile_gives_roles_places_skills_and_year_and_nothing_personal(self):
        from app.mcp.target_profile import load
        self.assertIsNone(load(self.target))
        self.write_target()
        profile = load(self.target)
        self.assertEqual(profile.preferred_roles, ["AI Engineer", "Machine Learning Engineer"])
        self.assertEqual(profile.preferred_locations, ["Pune", "Bengaluru"])
        self.assertEqual((profile.graduation_year, profile.experience_years), (2025, 0))
        self.assertEqual(sorted(profile.skills), ["PyTorch", "Python", "SQL"])
        self.assertEqual((profile.name, profile.projects, profile.experience, profile.evidence), (None, [], [], {}))

    def test_a_skills_line_is_the_whole_skill_list(self):
        # Found on the real file: "Never: sales" and a "Touched but would not claim: AWS, Kubernetes" section were read as skills.
        from app.mcp.target_profile import load
        self.write_target("""roles: AI Engineer
skills: Python, FastAPI, SQL

Never: sales, BPO.

## Touched but would not claim
GCP, AWS, Kubernetes, MongoDB, React.
""")
        self.assertEqual(load(self.target).skills, ["Python", "FastAPI", "SQL"])


if __name__ == "__main__":
    unittest.main()
