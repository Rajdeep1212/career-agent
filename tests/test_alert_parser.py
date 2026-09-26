"""Job-alert emails (LinkedIn, Naukri, Indeed) parsed into jobs; links are stored, never fetched."""
import re
import unittest
from pathlib import Path

from app.sources.alerts.parser import parse_alert_email

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "alerts"


def parse(name):
    return parse_alert_email((FIXTURES / name).read_bytes())


class LinkedInAlertTests(unittest.TestCase):
    def test_jobs_titles_companies_locations_and_clean_links(self):
        alert = parse("linkedin_alert.eml")
        self.assertEqual(alert.platform, "linkedin")
        self.assertEqual(alert.message_id, "<synthetic-linkedin-0001@example.com>")
        self.assertEqual(alert.received_at, "2026-09-25T07:32:10+00:00")
        self.assertEqual([(j.title, j.company, j.location) for j in alert.jobs], [
            ("Machine Learning Engineer", "Example Analytics", "Bengaluru, Karnataka, India"),
            ("Graduate Engineer Trainee - AI/ML", "Example Retrieval Co", "Hyderabad, Telangana, India (Hybrid)"),
            ("Data Scientist", "Example Health AI", "India (Remote)"),
        ])
        first = alert.jobs[0]
        self.assertEqual(first.url, "https://www.linkedin.com/jobs/view/4012345678/")   # tracking removed
        self.assertEqual(first.job_id, "linkedin:4012345678")

    def test_postings_carry_an_honest_source_and_no_posted_date(self):
        job = parse("linkedin_alert.eml").postings()[0]
        self.assertEqual(job.source, "LinkedIn alert")
        self.assertEqual(str(job.application_url), "https://www.linkedin.com/jobs/view/4012345678/")
        self.assertIsNone(job.posted_date)   # the alert date is when it was emailed, not when it was posted
        self.assertIn("LinkedIn job alert received 25 Sep 2026", job.description)
        self.assertEqual(parse("linkedin_alert.eml").postings()[1].work_mode, "hybrid")
        self.assertEqual(parse("linkedin_alert.eml").postings()[2].work_mode, "remote")


class NaukriAlertTests(unittest.TestCase):
    def test_jobs_with_experience_and_skills(self):
        alert = parse("naukri_alert.eml")
        self.assertEqual(alert.platform, "naukri")
        self.assertEqual([(j.title, j.company, j.location) for j in alert.jobs], [
            ("Data Analyst", "Example Retail Pvt. Ltd.", "Pune"),
            ("Python Developer - Fresher", "Example Software", "Bengaluru"),
        ])
        self.assertEqual(alert.jobs[0].url,
                         "https://www.naukri.com/job-listings-data-analyst-example-retail-pvt-ltd-pune-0-to-2-years-250926000123")
        job = alert.postings()[0]
        self.assertEqual((job.experience_min, job.experience_max), (0, 2))
        self.assertIn("Power BI", job.skills)


class IndeedAlertTests(unittest.TestCase):
    def test_company_and_location_on_one_line_or_two(self):
        alert = parse("indeed_alert.eml")
        self.assertEqual(alert.platform, "indeed")
        self.assertEqual([(j.title, j.company, j.location) for j in alert.jobs], [
            ("Python Developer", "Example Soft", "Hyderabad, Telangana"),
            ("Junior Backend Engineer", "Example Fintech", "Hyderabad, Telangana"),
        ])
        self.assertEqual(alert.jobs[0].url, "https://in.indeed.com/viewjob?jk=0123456789abcdef")
        self.assertTrue(alert.postings()[0].fresher_allowed)


class RobustnessTests(unittest.TestCase):
    def test_plain_text_only_alert(self):
        raw = (b"From: LinkedIn <jobs-noreply@linkedin.com>\r\nSubject: jobs\r\nDate: Fri, 25 Sep 2026 07:00:00 +0000\r\n"
               b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
               b"Backend Engineer\r\nExample Commerce\r\nPune, Maharashtra, India\r\n"
               b"View job: https://www.linkedin.com/comm/jobs/view/4099999999/?trackingId=x\r\n")
        alert = parse_alert_email(raw)
        self.assertEqual([(j.title, j.company, j.location) for j in alert.jobs],
                         [("Backend Engineer", "Example Commerce", "Pune, Maharashtra, India")])

    def test_unrelated_email_yields_no_jobs(self):
        raw = (b"From: Friend <friend@example.com>\r\nSubject: hi\r\nContent-Type: text/html\r\n\r\n"
               b"<p>See <a href='https://example.com/careers'>this</a> and "
               b"<a href='https://www.linkedin.com/in/someone'>my profile</a></p>")
        alert = parse_alert_email(raw)
        self.assertIsNone(alert.platform)
        self.assertEqual(alert.jobs, [])

    def test_manually_forwarded_alert_is_recognised_by_its_links(self):
        # Headers end at the first blank line (LF or CRLF, depending on the Git checkout).
        body = re.split(rb"\r?\n\r?\n", (FIXTURES / "naukri_alert.eml").read_bytes(), maxsplit=1)[1]
        raw = (b"From: Me <me@example.com>\r\nSubject: Fwd: jobs\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
               b"---------- Forwarded message ---------\r\n" + body)
        self.assertEqual(len(parse_alert_email(raw).jobs), 2)


if __name__ == "__main__":
    unittest.main()
