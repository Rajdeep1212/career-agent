"""TRK2: the tracker API under /api/v1 (docs/TRACKER_PLAN.md sections 3, 4 and 7). Status transitions, undo,
idempotent retries, the job-post snapshot, owner scoping and quick-add matching. Temporary databases, fictional
applications and recorded URL shapes only; nothing is fetched."""
import json
import socket
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.api import tracker as tracker_api
from app.main import app
from app.models.schemas import JobPosting
from app.storage import radar_store
from app.tracker import matching, models, store
from test_security_regressions import LOCAL, _IsolatedApp

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "tracker" / "quick_add_urls.json").read_text(encoding="utf-8"))
V1 = "/api/v1"
# docs/TRACKER_PLAN.md section 3, copied by hand so the test does not read the table it checks.
ALLOWED = {
    "SAVED": ["APPLIED", "SKIPPED"],
    "APPLIED": ["ONLINE_TEST", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "NO_RESPONSE"],
    "ONLINE_TEST": ["INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "NO_RESPONSE"],
    "INTERVIEW": ["INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "NO_RESPONSE"],
    "OFFER": ["WITHDRAWN", "REJECTED"],
    "NO_RESPONSE": ["ONLINE_TEST", "INTERVIEW", "OFFER", "REJECTED"],
    "REJECTED": [], "WITHDRAWN": [], "SKIPPED": [],
}
EVENT = {"SAVED": "saved", "APPLIED": "applied", "ONLINE_TEST": "online_test", "INTERVIEW": "interview", "OFFER": "offer",
         "REJECTED": "rejected", "WITHDRAWN": "withdrawn", "NO_RESPONSE": "no_response_confirmed", "SKIPPED": "skipped"}
PATH_TO = {"SAVED": [], "APPLIED": ["applied"], "ONLINE_TEST": ["applied", "online_test"], "INTERVIEW": ["applied", "interview"],
           "OFFER": ["applied", "offer"], "REJECTED": ["applied", "rejected"], "WITHDRAWN": ["applied", "withdrawn"],
           "NO_RESPONSE": ["applied", "no_response_confirmed"], "SKIPPED": ["skipped"]}


class _Tracker(_IsolatedApp):
    def setUp(self):
        super().setUp()
        for module, value in ((store, self.directory / "tracker.sqlite3"), (radar_store, self.directory / "radar.sqlite3")):
            patcher = patch.object(module, "DB_PATH", value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(store.dispose_all)
        store.upgrade()
        self.keys = 0

    def key(self) -> str:
        self.keys += 1
        return f"key-{self.keys:04d}"

    def post(self, path, body=None, key=None, headers=LOCAL):
        return self.client.post(V1 + path, json=body or {}, headers={**headers, "Idempotency-Key": key or self.key()})

    def get(self, path, **params):
        return self.client.get(V1 + path, params=params)

    def patch(self, path, body):
        return self.client.patch(V1 + path, json=body, headers=LOCAL)

    def new(self, n=1, **fields) -> dict:
        body = {"title": f"AI Engineer {n}", "company": "ExampleCo", "url": f"https://jobs.example.com/{n}", **fields}
        response = self.post("/applications", body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["application"]

    def event(self, application_id, event_type, key=None, headers=LOCAL, **fields):
        return self.post(f"/applications/{application_id}/events", {"event_type": event_type, **fields}, key=key, headers=headers)

    def at(self, status, n) -> dict:
        application = self.new(n)
        for event_type in PATH_TO[status]:
            self.assertEqual(self.event(application["id"], event_type).status_code, 201)
        return application

    def detail(self, application_id) -> dict:
        response = self.get(f"/applications/{application_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def count(self, table) -> int:
        with closing(sqlite3.connect(store.DB_PATH)) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def index(self) -> None:
        by_company: dict[str, list[JobPosting]] = {}
        for row in FIXTURE["index"]:
            by_company.setdefault(row["company_id"], []).append(JobPosting(
                company=row["company"], title=row["title"], location=row["location"], description=row["description"],
                application_url=row["url"], source="Company Radar", source_job_id=row["source_job_id"]))
        for company_id, jobs in by_company.items():
            radar_store.record_listing(company_id, jobs, complete=True, today=date(2026, 10, 3))

    def other_user(self) -> str:
        with store.session() as session:
            session.add(models.User(id="other", email="other@example.com", role="student", created_at="2026-10-01T00:00:00+00:00"))
            session.flush()
            session.add(models.Company(id="c-other", owner_id="other", name="OtherCo", company_key="otherco", created_at="2026-10-01"))
            session.add(models.CvVersion(id="cv-other", owner_id="other", label="other-v1", created_at="2026-10-01"))
            session.add(models.Contact(id="p-other", owner_id="other", name="Someone Else", created_at="2026-10-01"))
            session.flush()
            session.add(models.Application(id="a-other", owner_id="other", identity="x:1", title="Secret role", company_name="OtherCo",
                                           status="APPLIED", created_at="2026-10-01", updated_at="2026-10-01"))
            session.flush()
            session.add(models.ApplicationEvent(application_id="a-other", owner_id="other", event_type="applied",
                                                occurred_at="2026-10-01", recorded_at="2026-10-01", source="user"))
            session.commit()
        with closing(sqlite3.connect(store.DB_PATH)) as conn:
            return str(conn.execute("SELECT id FROM application_events WHERE application_id='a-other'").fetchone()[0])


class TransitionTests(_Tracker):
    def test_every_transition_in_section_3_is_allowed_or_refused(self):
        n = 0
        for status, allowed in ALLOWED.items():
            for target, event_type in EVENT.items():
                n += 1
                with self.subTest(status=status, target=target):
                    application = self.at(status, n)
                    events = self.count("application_events")
                    response = self.event(application["id"], event_type)
                    if target in allowed:
                        self.assertEqual(response.status_code, 201, response.text)
                        self.assertEqual(response.json()["application"]["status"], target)
                        self.assertEqual(self.count("application_events"), events + 1)
                    else:
                        self.assertEqual(response.status_code, 409, response.text)
                        self.assertEqual(response.json()["detail"]["allowed"], allowed)
                        self.assertEqual(response.json()["detail"]["status"], status)
                        self.assertEqual(self.count("application_events"), events)
                        self.assertEqual(self.detail(application["id"])["status"], status)

    def test_the_detail_lists_what_is_allowed_next(self):
        for n, (status, allowed) in enumerate(ALLOWED.items()):
            self.assertEqual(self.detail(self.at(status, n)["id"])["allowed_next"], allowed)

    def test_another_interview_round_is_an_event_and_not_a_status_change(self):
        application = self.at("INTERVIEW", 1)
        response = self.event(application["id"], "interview", note="round 2")
        self.assertEqual(response.status_code, 201)
        detail = self.detail(application["id"])
        self.assertEqual(detail["status"], "INTERVIEW")
        self.assertEqual([event["event_type"] for event in detail["timeline"]], ["saved", "applied", "interview", "interview"])
        self.assertEqual(detail["timeline"][-1]["note"], "round 2")

    def test_notes_and_recruiter_replies_never_change_the_status(self):
        for n, status in enumerate(ALLOWED):
            application = self.at(status, n)
            for event_type in ("note", "recruiter_reply"):
                self.assertEqual(self.event(application["id"], event_type, note="x").status_code, 201)
            self.assertEqual(self.detail(application["id"])["status"], status)

    def test_event_types_the_api_does_not_take_are_refused(self):
        application = self.new()
        for event_type in ("undone", "thumbs_up", "outreach_sent", "promoted", ""):
            self.assertEqual(self.event(application["id"], event_type).status_code, 422, event_type)
        self.assertEqual(self.count("application_events"), 1)

    def test_applying_records_the_date_and_later_events_cannot_come_before_it(self):
        application = self.new()
        applied = self.event(application["id"], "applied", occurred_at="2026-09-20")
        self.assertEqual(applied.status_code, 201)
        self.assertEqual(applied.json()["application"]["applied_at"], "2026-09-20")
        self.assertFalse(applied.json()["event"]["occurred_at_exact"])
        self.assertEqual(self.event(application["id"], "interview", occurred_at="2026-09-19").status_code, 422)
        self.assertEqual(self.event(application["id"], "interview", occurred_at="not a date").status_code, 422)
        self.assertEqual(self.event(application["id"], "interview", occurred_at="2099-01-01").status_code, 422)
        back_dated = self.event(application["id"], "interview", occurred_at="2026-09-25T10:00:00+05:30")
        self.assertEqual(back_dated.status_code, 201)
        self.assertTrue(back_dated.json()["event"]["occurred_at_exact"])

    def test_mutations_need_the_local_origin_and_an_idempotency_key(self):
        application = self.new()
        for headers in ({}, {"Origin": "https://attacker.example"}):
            self.assertEqual(self.event(application["id"], "applied", headers=headers).status_code, 403)
            self.assertEqual(self.post("/applications", {"title": "T", "company": "C"}, headers=headers).status_code, 403)
            self.assertEqual(self.client.patch(f"{V1}/applications/{application['id']}", json={"notes": "x"}, headers=headers).status_code, 403)
        missing = self.client.post(f"{V1}/applications/{application['id']}/events", json={"event_type": "applied"}, headers=LOCAL)
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(self.detail(application["id"])["status"], "SAVED")
        self.assertEqual(self.event("no-such-application", "applied").status_code, 404)


class UndoTests(_Tracker):
    def undo(self, application_id, event_id, key=None):
        return self.post(f"/applications/{application_id}/events/{event_id}/undo", key=key)

    def test_undoing_the_latest_event_restores_the_status_before_it(self):
        application = self.new()
        applied = self.event(application["id"], "applied", occurred_at="2026-09-20").json()["event"]
        test = self.event(application["id"], "online_test").json()["event"]
        response = self.undo(application["id"], test["id"])
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["application"]["status"], "APPLIED")
        self.assertEqual(response.json()["event"]["event_type"], "undone")
        self.assertEqual(response.json()["event"]["undoes_event_id"], test["id"])
        timeline = self.detail(application["id"])["timeline"]
        self.assertEqual([(event["event_type"], event["undone"]) for event in timeline],
                         [("saved", False), ("applied", False), ("online_test", True), ("undone", False)])
        again = self.undo(application["id"], applied["id"])                 # undoing walks backwards one event at a time
        self.assertEqual(again.json()["application"]["status"], "SAVED")
        self.assertIsNone(again.json()["application"]["applied_at"])
        self.assertEqual(self.count("application_events"), 5)               # nothing was edited or deleted

    def test_only_the_latest_event_can_be_undone(self):
        application = self.new()
        saved = self.detail(application["id"])["timeline"][0]
        applied = self.event(application["id"], "applied").json()["event"]
        test = self.event(application["id"], "online_test").json()["event"]
        refused = self.undo(application["id"], applied["id"])
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["detail"]["latest_event_id"], test["id"])
        undone = self.undo(application["id"], test["id"]).json()["event"]
        self.assertEqual(self.undo(application["id"], test["id"]).status_code, 409)      # already undone
        self.assertEqual(self.undo(application["id"], undone["id"]).status_code, 409)    # an undo is not undone
        self.assertEqual(self.undo(application["id"], 987654).status_code, 404)
        self.assertEqual(self.undo(application["id"], applied["id"]).status_code, 201)
        self.assertEqual(self.undo(application["id"], saved["id"]).status_code, 409)     # the first event stays
        self.assertEqual(self.detail(application["id"])["status"], "SAVED")

    def test_a_note_can_be_undone_without_touching_the_status(self):
        application = self.at("APPLIED", 1)
        note = self.event(application["id"], "note", note="typo").json()["event"]
        self.assertEqual(self.undo(application["id"], note["id"]).json()["application"]["status"], "APPLIED")


class IdempotencyTests(_Tracker):
    def test_a_retried_event_is_recorded_once(self):
        application = self.new()
        first = self.event(application["id"], "applied", key="retry-1")
        second = self.event(application["id"], "applied", key="retry-1")
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(second.json()["event"]["id"], first.json()["event"]["id"])
        self.assertEqual(second.json()["event"]["request_id"], "retry-1")
        self.assertEqual(self.count("application_events"), 2)

    def test_a_retried_create_makes_one_application(self):
        body = {"title": "AI Engineer", "company": "ExampleCo", "url": "https://jobs.example.com/9"}
        first, second = self.post("/applications", body, key="create-1"), self.post("/applications", body, key="create-1")
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertFalse(second.json()["created"])
        self.assertEqual(second.json()["application"]["id"], first.json()["application"]["id"])
        self.assertEqual((self.count("applications"), self.count("application_events"), self.count("companies")), (1, 1, 1))

    def test_a_retried_undo_is_recorded_once(self):
        application = self.new()
        applied = self.event(application["id"], "applied").json()["event"]
        path = f"/applications/{application['id']}/events/{applied['id']}/undo"
        first, second = self.post(path, key="undo-1"), self.post(path, key="undo-1")
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(second.json()["event"]["id"], first.json()["event"]["id"])
        self.assertEqual(self.count("application_events"), 3)

    def test_a_key_cannot_be_reused_for_another_request(self):
        one, two = self.new(1), self.new(2)
        self.assertEqual(self.event(one["id"], "applied", key="shared").status_code, 201)
        self.assertEqual(self.event(two["id"], "applied", key="shared").status_code, 409)
        self.assertEqual(self.event(one["id"], "note", key="shared").status_code, 409)
        self.assertEqual(self.detail(two["id"])["status"], "SAVED")


class ApplicationTests(_Tracker):
    def test_create_from_an_index_job_keeps_a_snapshot_that_survives_the_job_being_removed(self):
        self.index()
        response = self.post("/applications", {"job_id": "acme:4012345"})
        self.assertEqual(response.status_code, 201, response.text)
        application = response.json()["application"]
        self.assertEqual((application["job_id"], application["title"], application["company_name"], application["status"]),
                         ("acme:4012345", "Machine Learning Engineer", "Acme Robotics", "SAVED"))
        self.assertEqual(application["snapshot"]["description"], "Freshers welcome. Python and PyTorch.")
        self.assertEqual(application["url"], "https://boards.greenhouse.io/acmerobotics/jobs/4012345")
        with closing(sqlite3.connect(radar_store.DB_PATH)) as conn:
            conn.execute("DELETE FROM radar_jobs")
            conn.commit()
        self.assertEqual(self.post("/applications", {"job_id": "acme:4012345"}, ).json()["application"]["id"], application["id"])
        after = self.detail(application["id"])
        self.assertEqual(after["snapshot"], application["snapshot"])
        self.assertEqual(after["snapshot_sha256"], application["snapshot_sha256"])
        self.assertEqual(len(after["snapshot_sha256"]), 64)
        self.assertEqual(self.post("/applications", {"job_id": "acme:4012399"}).status_code, 404)   # gone from the index

    def test_create_needs_an_index_job_or_a_title_and_company(self):
        self.assertEqual(self.post("/applications", {"job_id": "nope:1"}).status_code, 404)
        for body in ({}, {"title": "Only a title"}, {"company": "Only a company"}, {"title": " ", "company": " "},
                     {"title": "T", "company": "C", "status": "OFFER"}, {"title": "T", "company": "C", "surprise": 1}):
            self.assertEqual(self.post("/applications", body).status_code, 422, body)
        self.assertEqual(self.count("applications"), 0)

    def test_create_from_pasted_fields_stores_what_was_given_and_the_same_url_is_one_application(self):
        first = self.new(1, location="Pune", description="Pasted text.", source="Referral", channel="email", notes="via Asha",
                         status="APPLIED", occurred_at="2026-09-28")
        self.assertEqual((first["status"], first["applied_at"], first["source"], first["channel"], first["notes"]),
                         ("APPLIED", "2026-09-28", "Referral", "email", "via Asha"))
        self.assertEqual(first["snapshot"]["description"], "Pasted text.")
        self.assertIsNone(first["job_id"])
        self.assertEqual([event["event_type"] for event in first["timeline"]], ["applied"])
        again = self.post("/applications", {"title": "Renamed", "company": "ExampleCo", "url": "https://JOBS.example.com/1/?utm_source=x#top"})
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()["created"])
        self.assertEqual(again.json()["application"]["id"], first["id"])
        self.assertEqual(self.count("applications"), 1)

    def test_list_filters_by_status_company_and_text(self):
        self.new(1, title="NLP Engineer", company="Acme Robotics Pvt. Ltd.")
        applied = self.new(2, title="Data Analyst", company="Globex", notes="referred by a senior")
        self.event(applied["id"], "applied")
        listed = self.get("/applications").json()
        self.assertEqual(listed["count"], 2)
        self.assertNotIn("snapshot", listed["applications"][0])
        self.assertEqual([row["title"] for row in self.get("/applications", status="APPLIED").json()["applications"]], ["Data Analyst"])
        self.assertEqual([row["title"] for row in self.get("/applications", company="acme robotics").json()["applications"]], ["NLP Engineer"])
        self.assertEqual([row["title"] for row in self.get("/applications", q="SENIOR").json()["applications"]], ["Data Analyst"])
        self.assertEqual(self.get("/applications", q="100%").json()["count"], 0)
        self.assertEqual(self.get("/applications", status="DREAMING").status_code, 422)

    def test_patch_edits_notes_follow_up_and_cv_version_only(self):
        application = self.new()
        version = self.post("/cv-versions", {"label": "v3-ml"}).json()["cv_version"]
        response = self.patch(f"/applications/{application['id']}",
                              {"notes": "call on Monday", "next_follow_up_at": "2026-10-10", "cv_version_id": version["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        detail = self.detail(application["id"])
        self.assertEqual((detail["notes"], detail["next_follow_up_at"], detail["cv_version_id"]), ("call on Monday", "2026-10-10", version["id"]))
        self.assertEqual(self.patch(f"/applications/{application['id']}", {"next_follow_up_at": None}).json()["application"]["next_follow_up_at"], None)
        self.assertEqual(self.detail(application["id"])["notes"], "call on Monday")
        for body in ({"status": "OFFER"}, {"title": "x"}, {"next_follow_up_at": "soon"}, {"cv_version_id": "no-such-version"}):
            self.assertEqual(self.patch(f"/applications/{application['id']}", body).status_code, 422, body)
        self.assertEqual(self.detail(application["id"])["status"], "SAVED")
        self.assertEqual(self.count("application_events"), 1)


class OwnerScopingTests(_Tracker):
    def test_another_users_records_answer_404_and_are_never_listed(self):
        event_id = self.other_user()
        mine = self.new()
        self.assertEqual(self.get("/applications/a-other").status_code, 404)
        self.assertEqual(self.patch("/applications/a-other", {"notes": "x"}).status_code, 404)
        self.assertEqual(self.event("a-other", "interview").status_code, 404)
        self.assertEqual(self.post(f"/applications/a-other/events/{event_id}/undo").status_code, 404)
        self.assertEqual(self.post(f"/applications/{mine['id']}/events/{event_id}/undo").status_code, 404)
        self.assertEqual(self.patch("/companies/c-other", {"notes": "x"}).status_code, 404)
        self.assertEqual(self.patch("/contacts/p-other", {"notes": "x"}).status_code, 404)
        self.assertEqual(self.patch(f"/applications/{mine['id']}", {"cv_version_id": "cv-other"}).status_code, 422)
        self.assertEqual(self.post("/contacts", {"name": "A", "company_id": "c-other"}).status_code, 422)
        self.assertEqual([row["id"] for row in self.get("/applications").json()["applications"]], [mine["id"]])
        self.assertEqual(self.get("/applications", q="secret").json()["count"], 0)
        self.assertEqual([row["name"] for row in self.get("/companies").json()["companies"]], ["ExampleCo"])
        self.assertEqual(self.get("/cv-versions").json()["cv_versions"], [])
        self.assertEqual(self.get("/contacts").json()["contacts"], [])
        with closing(sqlite3.connect(store.DB_PATH)) as conn:
            self.assertEqual(conn.execute("SELECT status, notes FROM applications WHERE id='a-other'").fetchone(), ("APPLIED", ""))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM application_events WHERE application_id='a-other'").fetchone()[0], 1)

    def test_each_user_sees_only_their_own_and_keys_and_urls_do_not_cross(self):
        self.other_user()
        mine = self.new(1)
        self.event(mine["id"], "applied", key="same-key")
        app.dependency_overrides[tracker_api.current_owner] = lambda: "other"
        self.addCleanup(app.dependency_overrides.clear)
        self.assertEqual([row["id"] for row in self.get("/applications").json()["applications"]], ["a-other"])
        self.assertEqual(self.get(f"/applications/{mine['id']}").status_code, 404)
        theirs = self.post("/applications", {"title": "AI Engineer 1", "company": "ExampleCo", "url": "https://jobs.example.com/1"})
        self.assertEqual(theirs.status_code, 201)                              # the same URL is a new application for them
        self.assertNotEqual(theirs.json()["application"]["id"], mine["id"])
        self.assertEqual(self.event("a-other", "interview", key="same-key").status_code, 201)   # keys are per owner
        self.assertEqual({row["name"] for row in self.get("/companies").json()["companies"]}, {"OtherCo", "ExampleCo"})


class RecordTests(_Tracker):
    def test_cv_versions_hold_a_label_and_a_file_hash(self):
        digest = "ab" * 32
        first = self.post("/cv-versions", {"label": "v3-ml", "file_sha256": digest, "notes": "ML projects first"})
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(set(first.json()["cv_version"]), {"id", "label", "created_at", "file_sha256", "notes"})
        again = self.post("/cv-versions", {"label": "v3-ml", "file_sha256": digest})
        self.assertEqual((again.status_code, again.json()["created"]), (200, False))
        self.assertEqual(self.post("/cv-versions", {"label": "v3-ml", "file_sha256": "cd" * 32}).status_code, 409)
        for body in ({"label": ""}, {"label": "v4", "file_sha256": "short"}, {"label": "v4", "skills": ["python"]}, {"label": "v4", "text": "cv"}):
            self.assertEqual(self.post("/cv-versions", body).status_code, 422, body)
        self.assertEqual([row["label"] for row in self.get("/cv-versions").json()["cv_versions"]], ["v3-ml"])

    def test_companies_are_one_per_name_and_can_be_edited(self):
        first = self.post("/companies", {"name": "Acme Robotics Pvt. Ltd.", "careers_url": "https://acme.example/careers"})
        self.assertEqual(first.status_code, 201, first.text)
        again = self.post("/companies", {"name": "acme robotics"})
        self.assertEqual((again.status_code, again.json()["company"]["id"]), (200, first.json()["company"]["id"]))
        company_id = first.json()["company"]["id"]
        edited = self.patch(f"/companies/{company_id}", {"notes": "reapply in March", "reapply_after": "2027-03-01"})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual((edited.json()["company"]["notes"], edited.json()["company"]["reapply_after"]), ("reapply in March", "2027-03-01"))
        self.assertEqual(edited.json()["company"]["careers_url"], "https://acme.example/careers")
        for body in ({"name": "Other"}, {"reapply_after": "later"}, {"owner_id": "other"}):
            self.assertEqual(self.patch(f"/companies/{company_id}", body).status_code, 422, body)
        self.assertEqual(self.post("/companies", {"name": "  "}).status_code, 422)
        self.assertEqual(self.get("/companies").json()["companies"][0]["name"], "Acme Robotics Pvt. Ltd.")
        self.assertEqual(self.client.post(f"{V1}/companies", json={"name": "X"}).status_code, 403)

    def test_contacts_belong_to_a_company_and_can_be_edited(self):
        company_id = self.post("/companies", {"name": "Acme Robotics"}).json()["company"]["id"]
        first = self.post("/contacts", {"name": "Asha Rao", "company_id": company_id, "role": "Engineering Manager", "channel": "email"})
        self.assertEqual(first.status_code, 201, first.text)
        again = self.post("/contacts", {"name": "asha rao", "company_id": company_id})
        self.assertEqual((again.status_code, again.json()["contact"]["id"]), (200, first.json()["contact"]["id"]))
        contact_id = first.json()["contact"]["id"]
        edited = self.patch(f"/contacts/{contact_id}", {"referral_status": "asked", "notes": "met at a meetup"})
        self.assertEqual((edited.status_code, edited.json()["contact"]["referral_status"], edited.json()["contact"]["role"]),
                         (200, "asked", "Engineering Manager"))
        self.assertEqual(self.post("/contacts", {"name": "B", "company_id": "no-such-company"}).status_code, 422)
        self.assertEqual(self.patch(f"/contacts/{contact_id}", {"owner_id": "other"}).status_code, 422)
        self.assertEqual(len(self.get("/contacts", company_id=company_id).json()["contacts"]), 1)
        self.assertEqual(self.get("/contacts", company_id="elsewhere").json()["contacts"], [])


class QuickAddTests(_Tracker):
    def setUp(self):
        super().setUp()
        self.index()
        for target in ("create_connection", "getaddrinfo"):                    # quick-add never opens the page it was given
            patcher = patch.object(socket, target, side_effect=AssertionError("network used"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_recorded_urls_match_as_expected(self):
        index = matching.index_jobs()
        self.assertEqual(len(index), len(FIXTURE["index"]))
        for case in FIXTURE["cases"]:
            with self.subTest(case["name"]):
                found = matching.match(case["url"], title=case.get("title", ""), company=case.get("company", ""),
                                       location=case.get("location", ""), index=index)
                self.assertEqual(found["status"], case["expect"], found)
                self.assertTrue(found["reason"])
                self.assertEqual(found["job_id"], case.get("job_id"))
                self.assertEqual(sorted(candidate["job_id"] for candidate in found["candidates"]), sorted(case.get("candidates", [])))

    def test_urls_are_normalised_before_they_are_compared(self):
        same = matching.normalise_url("https://boards.greenhouse.io/acmerobotics/jobs/4012345")
        for url in ("HTTPS://Boards.Greenhouse.IO/acmerobotics/jobs/4012345/", "https://boards.greenhouse.io/acmerobotics/jobs/4012345#app",
                    "https://boards.greenhouse.io/acmerobotics/jobs/4012345?utm_source=a&gh_src=b&fbclid=c&ref=d"):
            self.assertEqual(matching.normalise_url(url), same, url)
        self.assertEqual(matching.normalise_url("https://x.example/jobs?b=2&a=1"), matching.normalise_url("https://x.example/jobs?a=1&b=2"))
        self.assertNotEqual(matching.normalise_url("https://x.example/jobs?id=1"), matching.normalise_url("https://x.example/jobs?id=2"))
        self.assertNotEqual(matching.normalise_url("https://x.example/Jobs/A"), matching.normalise_url("https://x.example/jobs/a"))
        for bad in ("", "not a url", "ftp://x.example/1", "javascript:alert(1)", "https://user:pw@x.example/1", "https://x.example:port/"):
            self.assertEqual(matching.normalise_url(bad), "", bad)

    def test_a_matched_url_creates_the_application_from_the_index_job(self):
        response = self.post("/applications/quick-add", {"url": "https://boards.greenhouse.io/acmerobotics/jobs/4012345?gh_src=x"})
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual((body["match"]["status"], body["match"]["job_id"]), ("matched", "acme:4012345"))
        self.assertIn("boards.greenhouse.io/acmerobotics/jobs/4012345", body["match"]["reason"])
        self.assertEqual((body["application"]["job_id"], body["application"]["title"]), ("acme:4012345", "Machine Learning Engineer"))
        self.assertEqual(body["application"]["snapshot"]["description"], "Freshers welcome. Python and PyTorch.")
        again = self.post("/applications/quick-add", {"url": "https://boards.greenhouse.io/acmerobotics/jobs/4012345/#app"})
        self.assertEqual((again.status_code, again.json()["created"]), (200, False))
        self.assertEqual(again.json()["application"]["id"], body["application"]["id"])

    def test_a_probable_match_is_reported_and_never_linked_without_a_confirmation(self):
        given = {"url": "https://northwind.example/jobs/graduate-swe", "title": "Graduate Software Engineer", "company": "Northwind Labs"}
        response = self.post("/applications/quick-add", given)
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["match"]["status"], "probable")
        self.assertEqual(len(body["match"]["candidates"]), 2)
        self.assertTrue(all(candidate["reason"] for candidate in body["match"]["candidates"]))
        self.assertIsNone(body["application"]["job_id"])
        self.assertEqual(body["application"]["url"], given["url"])
        self.assertNotIn("description", body["application"]["snapshot"])          # only what was given is stored
        chosen = "northwind:b2c3d4e5-1111-2222-3333-444455556666"
        confirmed = self.post("/applications/quick-add", {"url": "https://northwind.example/jobs/graduate-swe-chennai", "title": "Graduate Software Engineer",
                                                         "company": "Northwind Labs", "confirm_job_id": chosen})
        self.assertEqual(confirmed.status_code, 201, confirmed.text)
        self.assertEqual(confirmed.json()["application"]["job_id"], chosen)
        self.assertEqual(confirmed.json()["application"]["snapshot"]["location"], "Chennai, India")
        wrong = self.post("/applications/quick-add", {"url": "https://northwind.example/jobs/other", "title": "Graduate Software Engineer",
                                                     "company": "Northwind Labs", "confirm_job_id": "acme:4012345"})
        self.assertEqual(wrong.status_code, 422)                                  # not one of the candidates

    def test_no_match_stores_what_was_given_or_asks_for_it(self):
        stored = self.post("/applications/quick-add", {"url": "https://initech.example/jobs/12", "title": "Backend Engineer", "company": "Initech"})
        self.assertEqual(stored.status_code, 201, stored.text)
        self.assertEqual(stored.json()["match"]["status"], "none")
        self.assertTrue(stored.json()["match"]["reason"])
        self.assertIsNone(stored.json()["application"]["job_id"])
        before = self.count("applications")
        bare = self.post("/applications/quick-add", {"url": "https://boards.greenhouse.io/acmerobotics/jobs/5550000"})
        self.assertEqual(bare.status_code, 422)
        self.assertEqual(bare.json()["detail"]["match"]["status"], "none")
        self.assertEqual(self.post("/applications/quick-add", {"url": "not a url", "title": "T", "company": "C"}).status_code, 422)
        self.assertEqual(self.count("applications"), before)

    def test_quick_add_works_when_there_is_no_index(self):
        with patch.object(radar_store, "DB_PATH", self.directory / "absent.sqlite3"):
            response = self.post("/applications/quick-add", {"url": "https://initech.example/jobs/77", "title": "QA Engineer", "company": "Initech"})
            self.assertEqual((response.status_code, response.json()["match"]["status"]), (201, "none"))
            self.assertFalse((self.directory / "absent.sqlite3").exists())


class OldRoutesTests(_Tracker):
    def test_the_m2_routes_still_answer_and_do_not_touch_the_tracker_database(self):
        self.assertEqual(self.client.get("/applications").status_code, 200)
        self.assertEqual(self.client.get("/tracker/funnel").status_code, 200)
        self.assertEqual(self.count("applications"), 0)


class WebOriginTests(_Tracker):
    """TRK3: the web app's exact origin is allowed on /api/v1 mutations, and nowhere else."""
    WEB = {"Origin": "http://localhost:3010"}

    def test_the_configured_web_origin_may_change_the_tracker_and_nothing_else(self):
        from app.core.config import settings
        self.assertEqual(settings.web_origin, "http://localhost:3010")
        created = self.post("/applications", {"title": "T", "company": "C"}, headers=self.WEB)
        self.assertEqual(created.status_code, 201, created.text)
        application_id = created.json()["application"]["id"]
        self.assertEqual(self.event(application_id, "applied", headers=self.WEB).status_code, 201)
        self.assertEqual(self.client.patch(f"{V1}/applications/{application_id}", json={"notes": "x"}, headers=self.WEB).status_code, 200)
        for origin in ("http://localhost:3011", "http://127.0.0.1:3010", "https://localhost:3010", "http://localhost:3010.evil.example", "null", "*"):
            self.assertEqual(self.event(application_id, "note", headers={"Origin": origin}).status_code, 403, origin)
        self.assertEqual(self.client.put("/profile/current", json={}, headers=self.WEB).status_code, 403)       # an old route
        self.assertEqual(self.client.post("/applications/applied", json={"job_id": "j1", "request_id": "req-0001"}, headers=self.WEB).status_code, 403)

    def test_another_web_origin_comes_from_config_and_no_cors_header_is_ever_sent(self):
        from app.core.config import settings
        with patch.object(settings, "web_origin", "http://localhost:3999"):
            self.assertEqual(self.post("/applications", {"title": "T", "company": "C"}, headers=self.WEB).status_code, 403)
            allowed = self.post("/applications", {"title": "T", "company": "C"}, headers={"Origin": "http://localhost:3999"})
            self.assertEqual(allowed.status_code, 201)
        listed = self.client.get(f"{V1}/applications", headers=self.WEB)
        preflight = self.client.options(f"{V1}/applications", headers={**self.WEB, "Access-Control-Request-Method": "POST"})
        for response in (allowed, listed, preflight):
            self.assertFalse([name for name in response.headers if name.lower().startswith("access-control-")])

    def test_web_origin_must_be_one_exact_origin(self):
        from app.core.config import Settings
        for bad in ("*", "http://*", "http://localhost:3010/app", "localhost:3010", "http://localhost:3010,http://x.example", ""):
            with self.assertRaises(ValueError, msg=bad):
                Settings(web_origin=bad)
        self.assertEqual(Settings(web_origin="http://localhost:3010/").web_origin, "http://localhost:3010")


class QuickAddPreviewTests(_Tracker):
    """TRK3: the page shows the match first; nothing is stored until the user decides."""

    def test_a_preview_reports_the_match_and_stores_nothing(self):
        self.index()
        given = {"url": "https://northwind.example/jobs/graduate-swe", "title": "Graduate Software Engineer", "company": "Northwind Labs"}
        for body, status in (({"url": "https://boards.greenhouse.io/acmerobotics/jobs/4012345?gh_src=x"}, "matched"), (given, "probable"),
                             ({"url": "https://initech.example/jobs/12"}, "none")):
            response = self.post("/applications/quick-add", {**body, "preview": True}, key="preview-key")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual((response.json()["match"]["status"], response.json()["created"], response.json()["application"]), (status, False, None))
        self.assertEqual((self.count("applications"), self.count("application_events")), (0, 0))
        self.assertEqual(self.post("/applications/quick-add", {"url": "nope", "preview": True}).status_code, 422)
        chosen = "northwind:b2c3d4e5-1111-2222-3333-444455556666"
        confirmed = self.post("/applications/quick-add", {**given, "confirm_job_id": chosen}, key="preview-key")   # the key was not used up
        self.assertEqual((confirmed.status_code, confirmed.json()["application"]["job_id"]), (201, chosen))


class DashboardCutoverTests(_Tracker):
    """TRK3b: what the vanilla dashboard needs from /api/v1 once the M2 routes are gone."""
    STORED = "ab" * 32

    def test_a_job_card_sends_its_fields_and_its_stored_job_id_and_is_one_application(self):
        body = {"title": "NLP Engineer", "company": "ExampleCo", "url": "https://jobs.example.com/77", "location": "Pune",
                "description": "Python and NLP.", "source": "JSearch", "stored_job_id": self.STORED}
        first = self.post("/applications", body)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()["application"]["job_id"], self.STORED)
        again = self.post("/applications", {"title": "NLP Engineer (reposted)", "company": "ExampleCo", "stored_job_id": self.STORED})
        self.assertEqual((again.status_code, again.json()["application"]["id"]), (200, first.json()["application"]["id"]))
        self.assertEqual(self.count("applications"), 1)
        self.assertEqual(self.post("/applications", {"stored_job_id": self.STORED}).status_code, 200)       # known: fields not needed
        self.assertEqual(self.post("/applications", {"stored_job_id": "cd" * 32}).status_code, 422)         # unknown: fields needed
        self.assertEqual(self.post("/applications", {"title": "T", "company": "C", "stored_job_id": "x" * 300}).status_code, 422)

    def test_the_list_names_the_latest_event_that_can_be_undone(self):
        application = self.new()
        self.assertIsNone(self.get("/applications").json()["applications"][0]["latest_event"])       # the first event stays
        applied = self.event(application["id"], "applied").json()["event"]
        self.assertEqual(self.get("/applications").json()["applications"][0]["latest_event"], {"id": applied["id"], "event_type": "applied"})
        self.post(f"/applications/{application['id']}/events/{applied['id']}/undo")
        self.assertIsNone(self.get("/applications").json()["applications"][0]["latest_event"])
        self.other_user()
        self.assertEqual(len(self.get("/applications").json()["applications"]), 1)
