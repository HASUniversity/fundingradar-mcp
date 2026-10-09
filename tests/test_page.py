"""Tests for fundingradar_mcp.page: HTML → text, and the ways a page can refuse to be read.

No network and no DNS: the opener and the resolver are stubbed, so these tests pin down the
parsing, the error mapping and the SSRF guard instead of a funder's live site.

Run from the repository root:  python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import io
import socket
import unittest
import urllib.error
from unittest import mock

from fundingradar_mcp import page

PUBLIC_IP = "93.184.216.34"


def public_dns(host: str, port: int | None = None, *args: object, **kwargs: object) -> list:
    """Every name resolves to one public address."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port or 443))]


class FakeResponse(io.BytesIO):
    """Minimal stand-in for the object the opener returns (context manager included)."""

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


def http_error(code: int, *, location: str | None = None, url: str = "https://example.org/call"):
    headers = {"Location": location} if location else {}
    return urllib.error.HTTPError(url, code, "error", headers, None)


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

    def test_skips_chrome_marked_with_a_class_or_id(self) -> None:
        html = """
        <div class="main-menu"><a href="/">Home</a><a href="/calls">Calls</a></div>
        <div id="breadcrumb">Home > Subsidies</div>
        <div role="navigation">Zoeken</div>
        <div class="site-header">HAS green academy</div>
        <div class="cookie-banner">Wij gebruiken cookies</div>
        <article class="content">
          <h1>Bijdrageregeling water en bodem</h1>
          <p>Deadline 31 maart 2027.</p>
        </article>
        """
        _, text = page.extract_text(html)

        self.assertIn("Bijdrageregeling water en bodem", text)
        self.assertIn("Deadline 31 maart 2027.", text)
        for chrome in ("Home", "Calls", "breadcrumb", "Zoeken", "green academy", "cookies"):
            self.assertNotIn(chrome, text)

    def test_keeps_content_after_a_skipped_subtree(self) -> None:
        html = ('<div class="menu"><div><span>Menu</span></div></div>'
                '<p>Echte inhoud</p>')
        _, text = page.extract_text(html)
        self.assertEqual(text, "Echte inhoud")

    def test_a_content_class_is_not_chrome(self) -> None:
        _, text = page.extract_text('<div class="subsidie-beschrijving">Deadline: 1 mei 2027</div>')
        self.assertIn("Deadline: 1 mei 2027", text)


class FetchPageTextTests(unittest.TestCase):
    def setUp(self) -> None:
        dns = mock.patch.object(page.socket, "getaddrinfo", side_effect=public_dns)
        dns.start()
        self.addCleanup(dns.stop)

    def _fetch(self, response: FakeResponse | None = None, *, side_effect=None, **kwargs):
        opener = mock.patch.object(page._OPENER, "open")
        opened = opener.start()
        self.addCleanup(opener.stop)
        if side_effect is not None:
            opened.side_effect = side_effect
        else:
            opened.return_value = response
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
        with self.assertRaises(page.PageUnavailable) as caught:
            self._fetch(side_effect=http_error(403))

        message = str(caught.exception)
        self.assertIn("403", message)
        self.assertIn("browser", message)
        self.assertIn("https://example.org/call", message)

    def test_other_http_errors_are_reported_with_the_code(self) -> None:
        with self.assertRaises(page.PageUnavailable) as caught:
            self._fetch(side_effect=http_error(500))
        self.assertIn("500", str(caught.exception))

    def test_unreachable_host_is_reported(self) -> None:
        with self.assertRaises(page.PageUnavailable) as caught:
            self._fetch(side_effect=urllib.error.URLError("getaddrinfo failed"))
        self.assertIn("could not reach", str(caught.exception))

    def test_timeout_is_reported(self) -> None:
        with self.assertRaises(page.PageUnavailable) as caught:
            self._fetch(side_effect=TimeoutError(), timeout=5)
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

        def open_(request, timeout=None):  # noqa: ANN001, ANN202
            captured["ua"] = request.get_header("User-agent") or ""
            return FakeResponse(b"<p>ok</p>")

        self._fetch(side_effect=open_)
        self.assertIn("FundingRadar", captured["ua"])


class PublicAddressGuardTests(unittest.TestCase):
    """The URL comes from a database record, so the fetcher must not become an SSRF tool."""

    def _dns(self, address: str):
        return mock.patch.object(
            page.socket, "getaddrinfo",
            side_effect=lambda host, port=None, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 80))],
        )

    def test_refuses_a_link_local_address(self) -> None:
        with self._dns("169.254.169.254"):
            with self.assertRaises(page.PageUnavailable) as caught:
                page.fetch_page_text("http://169.254.169.254/latest/meta-data/")
        self.assertIn("non-public address", str(caught.exception))

    def test_refuses_a_private_address(self) -> None:
        with self._dns("10.0.0.5"):
            with self.assertRaises(page.PageUnavailable) as caught:
                page.fetch_page_text("http://intranet.example.org/admin")
        self.assertIn("non-public address", str(caught.exception))

    def test_refuses_a_redirect_to_a_private_address(self) -> None:
        def dns(host, port=None, *a, **k):
            address = "10.0.0.5" if host == "intern.example.org" else PUBLIC_IP
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 80))]

        opener = mock.patch.object(page._OPENER, "open",
                                   side_effect=http_error(302, location="http://intern.example.org/admin"))
        with opener, mock.patch.object(page.socket, "getaddrinfo", side_effect=dns):
            with self.assertRaises(page.PageUnavailable) as caught:
                page.fetch_page_text("https://example.org/call")
        self.assertIn("non-public address", str(caught.exception))

    def test_follows_a_public_redirect_hop_by_hop(self) -> None:
        requested: list[str] = []

        def open_(request, timeout=None):  # noqa: ANN001, ANN202
            requested.append(request.full_url)
            if len(requested) == 1:
                raise http_error(301, location="https://example.org/nieuw")
            return FakeResponse(b"<p>Verplaatst</p>", url="https://example.org/nieuw")

        with mock.patch.object(page._OPENER, "open", side_effect=open_), \
                mock.patch.object(page.socket, "getaddrinfo", side_effect=public_dns):
            result = page.fetch_page_text("https://example.org/call")

        self.assertEqual(requested, ["https://example.org/call", "https://example.org/nieuw"])
        self.assertEqual(result["final_url"], "https://example.org/nieuw")

    def test_gives_up_after_too_many_redirects(self) -> None:
        def open_(request, timeout=None):  # noqa: ANN001, ANN202
            raise http_error(302, location=request.full_url)

        with mock.patch.object(page._OPENER, "open", side_effect=open_), \
                mock.patch.object(page.socket, "getaddrinfo", side_effect=public_dns):
            with self.assertRaises(page.PageUnavailable) as caught:
                page.fetch_page_text("https://example.org/lus")

        self.assertIn("redirected more than", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
