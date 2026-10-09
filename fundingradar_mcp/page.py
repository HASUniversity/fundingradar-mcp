"""Fetch a funding call's own page and turn it into text an agent can read.

Why this exists: the database holds what the pipeline extracted, and the funder's page is the
source of truth for the *current* status — is the round still open, did the deadline move, is
there a new round. An agent asked to check that needs the page text, and not every MCP client
has web tools of its own, so the server fetches it here.

Standard library only, and deliberately modest: one GET, no cookies, no JavaScript. Pages that
need a browser, or that block scripts, come back as a clear failure carrying the URL, so the
agent can say "I could not read this page" instead of inventing a status.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

#: Identify honestly: the same application already scrapes these pages, and a funder's
#: administrator should be able to see who asked.
USER_AGENT = "FundingRadar-MCP/0.2 (+https://dilab.has.nl/showcases/fundingradar)"

DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_BYTES = 400_000
DEFAULT_MAX_CHARS = 6000

#: Redirects are followed by hand (see _open_public) so every hop can be checked.
MAX_REDIRECTS = 5
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Leave redirects to _open_public: urllib must not follow a hop we have not checked."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)

#: Elements whose text is markup machinery or navigation, not the call's content.
_SKIP_ELEMENTS = frozenset({
    "script", "style", "noscript", "svg", "head", "nav", "footer", "form", "iframe", "template",
})
#: Elements that end a line, so the extracted text keeps a readable shape.
_BLOCK_ELEMENTS = frozenset({
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
    "table", "thead", "tbody", "ul", "ol", "blockquote", "dt", "dd", "figure", "hr",
})
#: Containers that hold chrome rather than content. Most sites mark this with a class or id
#: instead of a <nav> element, and the menu text would otherwise eat the reader's budget.
_CHROME_PATTERN = re.compile(
    r"(^|[-_\s])(nav|navbar|navigation|menu|breadcrumb|skip|header|footer|sidebar|social|"
    r"cookie|consent|banner|search|language|lang)([-_\s]|$)",
    re.IGNORECASE,
)


class PageUnavailable(RuntimeError):
    """The page could not be read; the message says what to tell the user."""


#: Elements that never have an end tag, so they cannot open a subtree to skip.
_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
    "track", "wbr",
})


def _is_chrome(tag: str, attributes: dict[str, str]) -> bool:
    """True for a container that holds site chrome rather than the page's content."""
    if tag not in ("div", "section", "ul", "ol", "span", "aside", "form", "table"):
        return False
    haystack = " ".join((
        attributes.get("class", ""),
        attributes.get("id", ""),
        attributes.get("role", ""),
        attributes.get("aria-label", ""),
    ))
    return bool(_CHROME_PATTERN.search(haystack))


class _TextExtractor(HTMLParser):
    """Collect visible text, a title, and nothing else."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._parts: list[str] = []
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
            return
        if self._skip_depth > 0:
            if tag not in _VOID_ELEMENTS:
                self._skip_depth += 1
            return
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if tag in _SKIP_ELEMENTS or _is_chrome(tag, attributes):
            self._skip_depth = 1
        elif tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Self-closing tag: it has no end tag, so it can never start a skipped subtree."""
        if self._skip_depth == 0 and tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        """Collapse whitespace, drop empty lines, keep paragraph breaks."""
        raw = "".join(self._parts)
        raw = raw.replace("\u00a0", " ").replace("\r", "\n")
        lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in raw.split("\n")]
        return "\n".join(line for line in lines if line)


def extract_text(html: str) -> tuple[str, str]:
    """Return (title, text) for an HTML document. Never raises on broken markup."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — a malformed page should still give what was parsed
        pass
    return " ".join(parser.title.split()), parser.text()


def _assert_public_host(url: str) -> None:
    """Refuse to fetch loopback, private and link-local addresses.

    The URL comes from a database record: `call_document_url` is extracted from a funder's page
    by a language model, so it is not something we control. Without this check the tool would be
    a way to reach internal services from wherever the server runs (SSRF).
    """
    host = urllib.parse.urlsplit(url).hostname or ""
    if not host:
        raise PageUnavailable(f"not an http(s) URL: {url}")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as error:
        raise PageUnavailable(f"could not resolve {host}: {error}") from None
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified):
            raise PageUnavailable(
                f"{host} resolves to a non-public address ({address}); refusing to fetch it"
            )


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        "Accept-Language": "nl,en;q=0.8",
    })


def _open_public(url: str, *, timeout: float):
    """GET *url*, following redirects one hop at a time, checking every hop's host.

    Redirects are followed by hand because a public URL may redirect to an internal one; letting
    urllib follow them would skip the check.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        _assert_public_host(current)
        try:
            return _OPENER.open(_request(current), timeout=timeout)
        except urllib.error.HTTPError as error:
            if error.code in _REDIRECT_CODES:
                target = urllib.parse.urljoin(current, error.headers.get("Location") or "")
                if not target:
                    raise PageUnavailable(f"{current} redirected without a target") from None
                current = target
                continue
            if error.code in (401, 403, 405, 406, 429):
                raise PageUnavailable(
                    f"the site refused the request (HTTP {error.code}); it likely blocks "
                    f"automated visitors. Open {current} in a browser for the current status"
                ) from None
            raise PageUnavailable(f"the site answered HTTP {error.code} for {current}") from None
        except urllib.error.URLError as error:
            raise PageUnavailable(f"could not reach {current}: {error.reason}") from None
        except TimeoutError:
            raise PageUnavailable(f"{current} did not answer within {timeout:.0f}s") from None
    raise PageUnavailable(f"{url} redirected more than {MAX_REDIRECTS} times")


def fetch_page_text(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """GET *url* and return {"url", "final_url", "http_status", "content_type", "bytes",
    "title", "text", "truncated"}.

    Raises PageUnavailable with an explanation the caller can pass on: a page that blocks
    scripts (403/429), a page that needs JavaScript (empty text), a timeout, a non-HTML
    document (PDF), or a URL that is not http(s).
    """
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise PageUnavailable(f"not an http(s) URL: {url}")

    response = _open_public(url, timeout=timeout)
    with response:
        status = response.status
        final_url = response.geturl()
        header = response.headers.get("Content-Type") or ""
        raw = response.read(max_bytes + 1)

    truncated_bytes = len(raw) > max_bytes
    raw = raw[:max_bytes]

    content_type = header.split(";")[0].strip().lower()
    if content_type and content_type not in ("text/html", "application/xhtml+xml", "text/plain"):
        raise PageUnavailable(
            f"{url} is a {content_type} document, not a web page; open it in a browser"
        )

    charset = "utf-8"
    match = re.search(r"charset=([\w-]+)", header)
    if match:
        charset = match.group(1)
    html = raw.decode(charset, "replace")

    title, text = extract_text(html)
    truncated_chars = len(text) > max_chars
    if truncated_chars:
        text = text[:max_chars].rstrip()

    if not text:
        raise PageUnavailable(
            f"{url} returned no readable text — the page probably builds itself with "
            "JavaScript. Open it in a browser for the current status"
        )

    return {
        "url": url,
        "final_url": final_url,
        "http_status": status,
        "content_type": content_type or "text/html",
        "bytes": len(raw),
        "title": title,
        "text": text,
        "truncated": truncated_bytes or truncated_chars,
    }
