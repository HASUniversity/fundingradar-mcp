"""Tests for fundingradar_mcp.page: HTML → text, and the ways a page can refuse to be read.

No network: urllib.request.urlopen is replaced by a stub, so the tests pin down the parsing and
the error mapping instead of a funder's live site.

Run from the repository root:  python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import io
import unittest
import urllib.error
from unittest import mock

from fundingradar_mcp import page


class FakeResponse(io.BytesIO):
    """Minimal stand-in for the object urlopen returns (context manager included)."""

    def __init__(self, body: bytes, *, status: int = 200, content_type: str = "text/html; charset=utf-8",
                 url: str = "https://example.org/call") -> None:
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Type": content_type}
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        self.close()
        return False


class ExtractTextTests(unittest.TestCase):
    def test_keeps_content_and_drops_markup(self) -> None:
        html = """
        <html><head><title>Openstelling 2027</title>
        <style>body { color: red }</style></head>
        <body><nav>Menu</nav><h1>Subsidie voor bodemonderzoek</h1>
        <p>Deadline: 31 maart 2027.</p><script>var x = 1;</script>
        <ul><li>Doelgroep: hbo</li><li>Maximaal &euro; 250.000</li></ul>
        <footer>Privacy</footer></body></html>
        """
        title, text = page.extract_text(html)

        self.assertEqual(title, "Openstelling 2027")
        self.assertIn("Subsidie voor bodemonderzoek", text)
        self.assertIn("Deadline: 31 maart 2027.", text)
        self.assertIn("Maximaal € 250.000", text)  # entities decoded
        for noise in ("color: red", "var x", "Menu", "Privacy"):
            self.assertNotIn(noise, text)

    def test_block_elements_become_line_breaks(self) -> None:
        _, text = page.extract_text("<p>Een</p><p>Twee</p>")
        self.assertEqual(text, "Een\nTwee")

    def test_collapses_repeated_whitespace_and_nbsp(self) -> None:
        _, text = page.extract_text("<p>a &nbsp;  b</p>\n\n\n<p>c</p>")
        self.assertEqual(text, "a b\nc")

    def test_survives_broken_markup(self) -> None:
        _, text = page.extract_text("<p>Open<div><span>Nog open")
        self.assertIn("Open", text)
        self.assertIn("Nog open", text)

    def test_returns_empty_for_a_script_only_page(self) -> None:
        _, text = page.extract_text('<html><body><div id="app"></div><script>render()</script></body></html>')
        self.assertEqual(text, "")


class FetchPageTextTests(unittest.TestCase):
    def _fetch(self, response, **kwargs):
        with mock.patch.object(page.urllib.request, "urlopen", return_value=response):
            return page.fetch_page_text("https://example.org/call", **kwargs)

    def test_returns_text_with_provenance(self) -> None:
        result = self._fetch(FakeResponse(b"<html><title>T</title><body><p>Nog open tot 1 mei</p></body></html>"))

        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["title"], "T")
        self.assertIn("Nog open tot 1 mei", result["text"])
        self.assertEqual(result["url"], "https://example.org/call")
        self.assertFalse(result["truncated"])

    def test_reports_the_url_it_actually_landed_on(self) -> None:
        response = FakeResponse(b"<p>Verplaatst</p>", url="https://example.org/nieuw")
        self.assertEqual(self._fetch(response)["final_url"], "https://example.org/nieuw")

    def test_truncates_long_text_and_says_so(self) -> None:
        body = b"<p>" + b"woord " * 400 + b"</p>"
        result = self._fetch(FakeResponse(body), max_chars=100)

        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["text"]), 100)

    def test_honours_a_non_utf8_charset(self) -> None:
        body = "<p>caf\u00e9 subsidie</p>".encode("latin-1")
        response = FakeResponse(body, content_type="text/html; charset=iso-8859-1")
        self.assertIn("café subsidie", self._fetch(response)["text"])

    def test_blocked_site_explains_itself_and_offers_the_url(self) -> None:
        error = urllib.error.HTTPError("https://example.org/call", 403, "Forbidden", {}, None)
        with mock.patch.object(page.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(page.PageUnavailable) as caught:
                page.fetch_page_text("https://example.org/call")

        message = str(caught.exception)
        self.assertIn("403", message)
        self.assertIn("browser", message)
        self.assertIn("https://example.org/call", message)

    def test_other_http_errors_are_reported_with_the_code(self) -> None:
        error = urllib.error.HTTPError("https://example.org/call", 500, "Server Error", {}, None)
        with mock.patch.object(page.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(page.PageUnavailable) as caught:
                page.fetch_page_text("https://example.org/call")
        self.assertIn("500", str(caught.exception))

    def test_unreachable_host_is_reported(self) -> None:
        error = urllib.error.URLError("getaddrinfo failed")
        with mock.patch.object(page.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(page.PageUnavailable) as caught:
                page.fetch_page_text("https://bestaat-niet.example")
        self.assertIn("could not reach", str(caught.exception))

    def test_timeout_is_reported(self) -> None:
        with mock.patch.object(page.urllib.request, "urlopen", side_effect=TimeoutError()):
            with self.assertRaises(page.PageUnavailable) as caught:
                page.fetch_page_text("https://example.org/traag", timeout=5)
        self.assertIn("5s", str(caught.exception))

    def test_pdf_is_not_pretended_to_be_a_page(self) -> None:
        response = FakeResponse(b"%PDF-1.4", content_type="application/pdf")
        with self.assertRaises(page.PageUnavailable) as caught:
            self._fetch(response)
        self.assertIn("application/pdf", str(caught.exception))

    def test_javascript_only_page_is_reported_as_such(self) -> None:
        response = FakeResponse(b'<html><body><script>app()</script></body></html>')
        with self.assertRaises(page.PageUnavailable) as caught:
            self._fetch(response)
        self.assertIn("JavaScript", str(caught.exception))

    def test_refuses_a_non_http_url(self) -> None:
        with self.assertRaises(page.PageUnavailable) as caught:
            page.fetch_page_text("file:///etc/passwd")
        self.assertIn("not an http(s) URL", str(caught.exception))

    def test_sends_an_identifying_user_agent(self) -> None:
        captured: dict[str, str] = {}

        def fake_urlopen(request, timeout=None):  # noqa: ANN001, ANN202
            captured["ua"] = request.get_header("User-agent") or ""
            return FakeResponse(b"<p>ok</p>")

        with mock.patch.object(page.urllib.request, "urlopen", side_effect=fake_urlopen):
            page.fetch_page_text("https://example.org/call")
        self.assertIn("FundingRadar", captured["ua"])


if __name__ == "__main__":
    unittest.main()
