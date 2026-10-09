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

import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Any

#: Identify honestly: the same application already scrapes these pages, and a funder's
#: administrator should be able to see who asked.
USER_AGENT = "FundingRadar-MCP/0.2 (+https://dilab.has.nl/showcases/fundingradar)"

DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_BYTES = 400_000
DEFAULT_MAX_CHARS = 6000

#: Elements whose text is markup machinery or navigation, not the call's content.
_SKIP_ELEMENTS = frozenset({
    "script", "style", "noscript", "svg", "head", "nav", "footer", "form", "iframe", "template",
})
#: Elements that end a line, so the extracted text keeps a readable shape.
_BLOCK_ELEMENTS = frozenset({
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
    "table", "thead", "tbody", "ul", "ol", "blockquote", "dt", "dd", "figure", "hr",
})


class PageUnavailable(RuntimeError):
    """The page could not be read; the message says what to tell the user."""


class _TextExtractor(HTMLParser):
    """Collect visible text, a title, and nothing else."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._parts: list[str] = []
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_ELEMENTS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_ELEMENTS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
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

    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        "Accept-Language": "nl,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            final_url = response.geturl()
            header = response.headers.get("Content-Type") or ""
            raw = response.read(max_bytes + 1)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403, 405, 406, 429):
            raise PageUnavailable(
                f"the site refused the request (HTTP {error.code}); it likely blocks automated "
                f"visitors. Open {url} in a browser for the current status"
            ) from None
        raise PageUnavailable(f"the site answered HTTP {error.code} for {url}") from None
    except urllib.error.URLError as error:
        raise PageUnavailable(f"could not reach {url}: {error.reason}") from None
    except TimeoutError:
        raise PageUnavailable(f"{url} did not answer within {timeout:.0f}s") from None

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
