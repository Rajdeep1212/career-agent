"""robots.txt is read as RFC 9309 says: the longest matching rule wins, and Allow wins a tie.

Python's urllib.robotparser takes the first matching line instead, which refuses a path that a later, more specific
Allow permits, and permits a path that a later, more specific Disallow refuses.
"""
import tempfile
import unittest
from pathlib import Path

import httpx

from app.sources.fetcher import PoliteFetcher, RobotsDisallowed, RobotsRules

AGENT = "CareerAgent/0.4 (local personal job search; reads public job boards once a day)"
KEKA = "User-agent: *\nDisallow: /\nAllow: /careers\nAllow: /careers/\n"     # as served by a Keka tenant on 9 Oct 2026


def allowed(text: str, path: str, agent: str = AGENT) -> bool:
    return RobotsRules(text).can_fetch(agent, "https://site.example" + path)


class RobotsRulesTests(unittest.TestCase):
    def test_a_longer_allow_beats_an_earlier_shorter_disallow(self):
        self.assertTrue(allowed(KEKA, "/careers/api/jobs/default/active"))
        self.assertTrue(allowed(KEKA, "/careers"))
        self.assertFalse(allowed(KEKA, "/"))
        self.assertFalse(allowed(KEKA, "/admin/login"))

    def test_a_longer_disallow_beats_an_earlier_shorter_allow(self):
        text = "User-agent: *\nAllow: /\nDisallow: /jobs/private/\n"
        self.assertFalse(allowed(text, "/jobs/private/1"))
        self.assertTrue(allowed(text, "/jobs/1"))

    def test_allow_wins_a_tie_and_no_matching_rule_allows(self):
        self.assertTrue(allowed("User-agent: *\nDisallow: /page\nAllow: /page\n", "/page"))
        self.assertTrue(allowed("User-agent: *\nDisallow: /private/\n", "/public/1"))
        self.assertTrue(allowed("", "/anything"))

    def test_an_empty_disallow_allows_everything(self):
        self.assertTrue(allowed("User-agent: *\nDisallow:\n", "/x"))

    def test_wildcard_and_end_anchor(self):
        text = "User-agent: *\nDisallow: /*.pdf$\nDisallow: /search*sort=\n"
        self.assertFalse(allowed(text, "/files/cv.pdf"))
        self.assertTrue(allowed(text, "/files/cv.pdf.html"))
        self.assertFalse(allowed(text, "/search?q=ai&sort=date"))
        self.assertTrue(allowed(text, "/search?q=ai"))

    def test_the_group_naming_this_app_is_used_instead_of_the_star_group(self):
        text = "User-agent: *\nDisallow: /\n\nUser-agent: CareerAgent\nDisallow: /private/\n"
        self.assertTrue(allowed(text, "/jobs/1"))
        self.assertFalse(allowed(text, "/private/1"))
        self.assertFalse(allowed(text, "/jobs/1", agent="OtherBot/1.0"))

    def test_groups_for_other_agents_are_ignored_and_several_agents_share_rules(self):
        text = "User-agent: BadBot\nDisallow: /\n\nUser-agent: a\nUser-agent: *\nDisallow: /x/\n"
        self.assertTrue(allowed(text, "/jobs/1"))
        self.assertFalse(allowed(text, "/x/1"))

    def test_comments_case_and_the_query_string(self):
        text = "# hello\nUSER-AGENT: *   # everyone\ndisallow: /a?b=1  # one page\n"
        self.assertFalse(allowed(text, "/a?b=1&c=2"))
        self.assertTrue(allowed(text, "/a?c=2"))


class FetcherUsesTheRulesTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_path_under_a_later_allow_is_fetched_and_another_is_refused(self):
        calls = []

        async def get(url, *, timeout, headers, max_bytes):
            calls.append(url)
            body = KEKA if url.endswith("/robots.txt") else "[]"
            return httpx.Response(200, text=body, request=httpx.Request("GET", url))

        async def no_sleep(_seconds):
            return None

        with tempfile.TemporaryDirectory() as directory:
            fetcher = PoliteFetcher(get=get, sleep=no_sleep, cache_dir=Path(directory))
            feed = "https://tenant.keka.example/careers/api/jobs/default/active"
            self.assertEqual((await fetcher.get(feed)).text, "[]")
            with self.assertRaises(RobotsDisallowed):
                await fetcher.get("https://tenant.keka.example/hire/internal")
        self.assertEqual(calls, ["https://tenant.keka.example/robots.txt", feed])


if __name__ == "__main__":
    unittest.main()
