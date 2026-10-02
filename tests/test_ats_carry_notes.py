"""Q2c4: a run that answers a row from the cache only keeps the reason an earlier run recorded for it."""
import unittest

from app.sources.ats_detect import Detection, carry_notes

SKIPPED = "not requested in this run and not in the cache"


def not_read(company, url, note) -> Detection:
    return Detection(line=2, company=company, list_type="startup", careers_url=url, declared_ats="unknown", status="not_read", note=note)


class CarryNotesTests(unittest.TestCase):
    def test_an_earlier_reason_comes_back_only_for_the_same_careers_url(self):
        earlier = {"Blocked Co": {"careers_url": "https://www.blocked.example/careers", "note": "careers page returned HTTP 403"},
                   "Moved Co": {"careers_url": "https://old.moved.example/careers", "note": "careers page returned HTTP 404"}}
        results = [not_read("Blocked Co", "https://www.blocked.example/careers", SKIPPED),
                   not_read("Moved Co", "https://new.moved.example/careers", SKIPPED),
                   not_read("Fresh Co", "https://www.fresh.example/careers", "robots.txt disallows https://www.fresh.example/careers")]
        carried, notes = carry_notes(results, earlier)
        self.assertEqual(carried[0].note, "careers page returned HTTP 403 (earlier run; not requested again)")
        self.assertEqual(carried[1].note, SKIPPED)
        self.assertEqual(carried[2], results[2])
        self.assertEqual(notes, {"Blocked Co": earlier["Blocked Co"],
                                 "Fresh Co": {"careers_url": "https://www.fresh.example/careers",
                                              "note": "robots.txt disallows https://www.fresh.example/careers"}})

    def test_rows_that_were_read_are_left_alone(self):
        found = Detection(line=2, company="Read Co", list_type="startup", careers_url="https://x.example", declared_ats="unknown",
                          status="not_detected")
        self.assertEqual(carry_notes([found], {"Read Co": {"careers_url": "https://x.example", "note": "old"}}), ([found], {}))


if __name__ == "__main__":
    unittest.main()
