"""Deterministic verification and SSRF regression tests; never uses live DNS/HTTP."""
import socket
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx

from app.models.schemas import JobPosting
from app.services import application_verifier as verifier


class VerificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Old implementations must also stay offline while the tests are red.
        original = httpx.AsyncClient
        patcher = patch("httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(lambda request: httpx.Response(503)), **kwargs))
        patcher.start()
        self.addCleanup(patcher.stop)

    def job(self, url="https://example.com/jobs/12345", **kwargs):
        return JobPosting(company="Example", title="Python Developer", location="India", application_url=url, **kwargs)

    async def check(self, html="", status=200, url="https://example.com/jobs/12345", **kwargs):
        response = httpx.Response(status, text=html, request=httpx.Request("GET", url))
        with patch.object(verifier, "safe_get", AsyncMock(return_value=response), create=True):
            return await verifier.verify_application(self.job(url, **kwargs))

    async def test_real_apply_control_and_exact_role_are_verified(self):
        job = await self.check('<main><h1>Python Developer</h1><a href="/jobs/12345/apply">Apply now</a></main>')
        self.assertEqual(job.verification_state, "ACTIVE_VERIFIED")
        self.assertEqual(job.application_status, "active")
        self.assertIsNotNone(job.verification_checked_at)
        self.assertTrue(job.verification_reason)

    async def test_company_alone_and_generic_apply_prose_are_not_verified(self):
        for html in ['<h1>Example careers</h1><a href="/apply">Apply now</a>', '<h1>Python Developer</h1><p>Learn how to apply now for jobs.</p>', '<h1>Python Developer</h1><nav><a href="/apply">Apply now</a></nav>', '<h1>Python Developer</h1><button disabled>Apply now</button>', '<h1>Python Developer</h1><button style="display:none">Apply now</button>', '<h1>Python Developer</h1><fieldset disabled><button>Apply now</button></fieldset>']:
            with self.subTest(html=html):
                self.assertEqual((await self.check(html)).verification_state, "UNVERIFIED")

    async def test_http_errors_are_not_all_closed(self):
        for status, state in [(404, "CLOSED"), (410, "CLOSED"), (403, "UNVERIFIED"), (429, "UNVERIFIED"), (500, "UNVERIFIED")]:
            with self.subTest(status=status):
                job = await self.check(status=status)
                self.assertEqual(job.verification_state, state)
                self.assertEqual(job.application_status, "closed" if state == "CLOSED" else "unverified")

    async def test_visible_closed_text_wins_over_apply(self):
        job = await self.check('<h1>Python Developer</h1><p>This job is no longer available</p><button>Apply now</button>')
        self.assertEqual(job.verification_state, "CLOSED")

    async def test_conditional_faq_and_other_job_closure_do_not_close_current_job(self):
        for extra in ['<section><h2>FAQ</h2><p>If the position has been filled, try another vacancy.</p></section>', '<article><h2>Java Developer</h2><p>Applications are closed.</p></article>', '<footer>Job not found? Search our careers page.</footer>']:
            with self.subTest(extra=extra):
                job = await self.check('<main><h1>Python Developer</h1><button>Apply now</button></main>' + extra)
                self.assertEqual(job.verification_state, 'ACTIVE_VERIFIED')

    async def test_explicit_closure_within_role_scope_is_closed(self):
        job = await self.check('<main><h1>Python Developer</h1><p>This position has been filled.</p><button>Apply now</button></main>')
        self.assertEqual(job.verification_state, 'CLOSED')

    async def test_script_text_is_not_evidence(self):
        job = await self.check('<script>Python Developer; apply now; applications are closed</script><div id="app"></div>')
        self.assertEqual(job.verification_state, "UNVERIFIED")

    async def test_generic_careers_listing_is_not_verified(self):
        job = await self.check('<h1>Search jobs</h1><h2>Python Developer</h2><button>Apply now</button>', url="https://example.com/careers")
        self.assertEqual(job.verification_state, "UNVERIFIED")

    async def test_recent_identified_ats_listing_is_likely_only(self):
        job = await self.check('<h1>Python Developer</h1><p>Example Job description: Python services.</p>', url="https://jobs.lever.co/example/abc-123", posted_date=datetime.now(timezone.utc).isoformat())
        self.assertEqual(job.verification_state, "LIKELY_ACTIVE")
        self.assertEqual(job.application_status, "unverified")

    async def test_ats_without_recent_evidence_is_unverified(self):
        job = await self.check('<h1>Python Developer</h1>', url="https://jobs.lever.co/example/abc-123")
        self.assertEqual(job.verification_state, "UNVERIFIED")

    async def test_company_substring_never_marks_official(self):
        job = await self.check('<h1>Python Developer</h1><button>Apply now</button>', url="https://example-malicious.com/jobs/12345")
        self.assertFalse(job.official_application)

    async def test_network_failure_and_missing_url_have_reasons(self):
        with patch.object(verifier, "safe_get", AsyncMock(side_effect=ValueError("blocked")), create=True):
            for job in [self.job(), self.job(None)]:
                result = await verifier.verify_application(job)
                self.assertEqual(result.verification_state, "UNVERIFIED")
                self.assertIsNotNone(result.verification_checked_at)
                self.assertTrue(result.verification_reason)


class SafeHTTPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from app.services import safe_http
        self.safe = safe_http
        self.dns = patch.object(safe_http, "_resolve_host", AsyncMock(return_value=["93.184.216.34"]))
        self.resolver = self.dns.start()
        self.addCleanup(self.dns.stop)
        self.original_client = httpx.AsyncClient

    def transport(self, handler):
        def factory(**kwargs):
            self.assertFalse(kwargs["trust_env"])
            self.assertTrue(kwargs["verify"])
            self.assertFalse(kwargs["follow_redirects"])
            return self.original_client(transport=httpx.MockTransport(handler), **kwargs)
        return patch.object(self.safe.httpx, "AsyncClient", side_effect=factory)

    async def test_unsafe_urls_are_blocked_before_http(self):
        urls = ["file:///etc/passwd", "https://user:pass@example.com/a", "http://localhost/a", "http://a.localhost/a", "http://127.0.0.1", "http://10.0.0.1", "http://169.254.169.254", "http://[::1]", "http://[fc00::1]", "http://[fe80::1]", "http://[::ffff:127.0.0.1]", "http://224.0.0.1", "https://example.com:8080/a", "https://example.com:0/a", "http://[2002:7f00:1::]"]
        with self.transport(lambda request: self.fail("Unsafe URL reached HTTP")):
            for url in urls:
                with self.subTest(url=url), self.assertRaises(self.safe.UnsafeURLError):
                    await self.safe.safe_get(url)

    async def test_mixed_dns_answers_are_blocked(self):
        self.resolver.return_value = ["93.184.216.34", "192.168.1.5"]
        with self.transport(lambda request: self.fail("Private DNS answer reached HTTP")), self.assertRaises(self.safe.UnsafeURLError):
            await self.safe.safe_get("https://example.com/job")

    async def test_validated_ip_is_used_for_connection_with_original_tls_name(self):
        def handler(request):
            self.assertEqual(request.url.host, "93.184.216.34")
            self.assertEqual(request.headers["host"], "example.com")
            self.assertEqual(request.extensions["sni_hostname"], "example.com")
            return httpx.Response(200, text="hello")
        with self.transport(handler):
            response = await self.safe.safe_get("https://example.com/jobs/1")
        self.assertEqual(str(response.url), "https://example.com/jobs/1")
        self.assertEqual(response.text, "hello")
        self.assertEqual(self.resolver.await_count, 1)

    async def test_redirect_to_private_host_is_blocked(self):
        with self.transport(lambda request: httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})), self.assertRaises(self.safe.UnsafeURLError):
            await self.safe.safe_get("https://example.com/jobs/1")

    async def test_same_host_redirect_revalidates_dns(self):
        self.resolver.side_effect = [["93.184.216.34"], ["127.0.0.1"]]
        with self.transport(lambda request: httpx.Response(302, headers={"location": "/new"})), self.assertRaises(self.safe.UnsafeURLError):
            await self.safe.safe_get("https://example.com/old")

    async def test_redirect_loop_and_oversized_body_are_bounded(self):
        with self.transport(lambda request: httpx.Response(302, headers={"location": "/loop"})), self.assertRaises(self.safe.UnsafeURLError):
            await self.safe.safe_get("https://example.com/loop", max_redirects=2)
        with self.transport(lambda request: httpx.Response(200, content=b"x" * 101)), self.assertRaises(self.safe.UnsafeURLError):
            await self.safe.safe_get("https://example.com/job", max_bytes=100)

    async def test_dns_and_response_timeout_is_bounded(self):
        import asyncio
        async def slow_dns(*args):
            await asyncio.sleep(1)
            return ["93.184.216.34"]
        self.resolver.side_effect = slow_dns
        with self.assertRaises(TimeoutError):
            await self.safe.safe_get("https://example.com/job", timeout=0.1)

    async def test_stream_body_without_length_header_is_bounded(self):
        class Chunks(httpx.AsyncByteStream):
            async def __aiter__(self):
                for _ in range(20):
                    yield b"a" * 20
        with self.transport(lambda request: httpx.Response(200, stream=Chunks())), self.assertRaises(self.safe.UnsafeURLError):
            await self.safe.safe_get("https://example.com/job", max_bytes=100)

    async def test_compressed_responses_are_not_expanded(self):
        import gzip
        with self.transport(lambda request: httpx.Response(200, headers={"content-encoding": "gzip"}, content=gzip.compress(b"a" * 1000))), self.assertRaises(self.safe.UnsafeURLError):
            await self.safe.safe_get("https://example.com/job")

    async def test_real_httpcore_uses_pinned_socket_and_verified_original_tls_name(self):
        import ssl
        from httpcore._backends.auto import AutoBackend
        observed = {}
        class Stream:
            async def read(self, max_bytes, timeout=None):
                return b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
            async def write(self, buffer, timeout=None):
                observed.setdefault("writes", []).append(buffer)
            async def aclose(self):
                pass
            async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
                observed["tls_name"] = server_hostname
                observed["verify_mode"] = ssl_context.verify_mode
                observed["check_hostname"] = ssl_context.check_hostname
                return self
            def get_extra_info(self, info):
                return None
        async def connect(backend, host, port, **kwargs):
            observed["socket_host"] = host
            observed["socket_port"] = port
            return Stream()
        with patch.object(AutoBackend, "connect_tcp", connect):
            response = await self.safe.safe_get("https://example.com/jobs/1")
        self.assertEqual(response.text, "ok")
        self.assertEqual(observed["socket_host"], "93.184.216.34")
        self.assertEqual(observed["socket_port"], 443)
        self.assertEqual(observed["tls_name"], "example.com")
        self.assertEqual(observed["verify_mode"], ssl.CERT_REQUIRED)
        self.assertTrue(observed["check_hostname"])
        self.assertIn(b"Host: example.com", b"".join(observed["writes"]))

    async def test_ipv6_connection_preserves_host(self):
        self.resolver.return_value = ["2606:4700:4700::1111"]
        def handler(request):
            self.assertEqual(request.url.host, "2606:4700:4700::1111")
            self.assertEqual(request.headers["host"], "example.com")
            return httpx.Response(200, text="ok")
        with self.transport(handler):
            self.assertEqual((await self.safe.safe_get("https://example.com/job")).status_code, 200)


if __name__ == "__main__":
    unittest.main()
