"""HTTP client for the FundingRadar public API (api/v1).

Uses only the standard library: the server used to talk to PostgreSQL directly, and it
does not need a new dependency to talk to HTTP instead.

Two things are worth knowing before reading the rest.

**Error mapping.** The API answers a failure with a status and a human-readable
`{"error": "..."}`; that message is what a calling agent needs, so it is surfaced
verbatim, with one line about what the status implies.

**Transports.** The production host (nginx, in front of the application) turns requests
away with its own 404 *before* PHP runs when they carry a token-shaped value in the
`Authorization` or `X-API-Token` header, and which combinations it lets through moves over
time (measured 2026-10-09). The token is therefore sent in whichever of three transports
works: the standard `Authorization: Bearer`, `Authorization: Basic` with the token as the
username (base64 hides the token shape from anything in front of the app), or the neutral
`X-FundingRadar-Token` header. One request uses exactly one transport; the client tries the
next one when the host rejects it, and remembers what worked.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_TIMEOUT = 30.0

#: Transport names, in the order they are tried. The standard header comes first so the
#: clean form is used wherever the host behaves; the other two are fallbacks.
TRANSPORTS = ("bearer", "basic", "x-token")

#: Status -> what it means for this client, appended to the API's own message.
_STATUS_HINTS = {
    401: "the API token is missing, revoked or expired — create a new one in FundingRadar under Settings → API access",
    403: "this token does not have the scope the endpoint requires",
    404: "nothing matches that identifier; check the slug or UUID",
    422: "the request parameters were rejected",
    429: "too many requests for this token; wait and try again",
}

#: Statuses a host uses when it refuses a request before the application sees it. A real
#: API answer with one of these carries JSON, which is how the two are told apart.
_HOST_REJECTION_STATUSES = frozenset({400, 403, 404, 405, 501})


class ApiError(RuntimeError):
    """A request to the FundingRadar API failed."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class FundingRadarApi:
    """Minimal, read-only client for one FundingRadar installation."""

    def __init__(self, base_url: str, token: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._transport: str | None = None

    @property
    def base_url(self) -> str:
        return self._base

    @property
    def transport(self) -> str | None:
        """The transport that worked last, or None before the first successful call."""
        return self._transport

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET one endpoint and return its parsed body ({"data": ..., "meta": ...})."""
        query = self._encode(params or {})
        return self._send(f"{self._base}/{path.lstrip('/')}{query}", None)

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST arguments as a JSON body and return the parsed body.

        The API takes the same fields as query-string arguments and as a body. The search goes
        this way so the terms stay out of the URL (and out of access logs) and a long query is
        not limited by URL length. An earlier note here blamed the host for 404s on a
        query-string search; that was a fatal error in the application (a missing import), not
        a host rule - see docs/api-v1.md in the app repository.
        """
        clean = {key: value for key, value in payload.items() if value is not None and value != ""}
        return self._send(f"{self._base}/{path.lstrip('/')}", clean)

    def _send(self, url: str, body: dict[str, Any] | None) -> dict[str, Any]:
        """Try the transports in order until one reaches the application."""
        order = self._transport_order()
        host_rejections: list[str] = []
        for transport in order:
            try:
                parsed = self._request(url, transport, body)
            except _HostRejection as rejection:
                host_rejections.append(f"{transport}: {rejection}")
                self._transport = None
                continue
            self._transport = transport
            return parsed

        raise ApiError(
            "no transport reached the FundingRadar API "
            f"({len(host_rejections)} transports tried: {'; '.join(host_rejections)}). The "
            "request never reached the application, so the token was not the problem — "
            "check docs/OPERATIONS.md"
        )

    def _transport_order(self) -> list[str]:
        """TRANSPORTS, with the one that worked last moved to the front."""
        if self._transport is None:
            return list(TRANSPORTS)
        return [self._transport] + [name for name in TRANSPORTS if name != self._transport]

    def _headers(self, transport: str) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if transport == "bearer":
            headers["Authorization"] = f"Bearer {self._token}"
        elif transport == "basic":
            # The token goes in the username field; the password is empty. Whatever sits in
            # front of the application sees base64, not a token-shaped string.
            credentials = base64.b64encode(f"{self._token}:".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {credentials}"
        else:
            headers["X-FundingRadar-Token"] = self._token
        return headers

    def _request(self, url: str, transport: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = self._headers(transport)
        if body is None:
            request = urllib.request.Request(url, headers=headers, method="GET")
        else:
            headers["Content-Type"] = "application/json"
            request = urllib.request.Request(
                url, headers=headers, data=json.dumps(body).encode("utf-8"), method="POST"
            )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            raw = _read(error)
            if error.code in _HOST_REJECTION_STATUSES and not _is_json(raw):
                # The host answered, not the application: no JSON envelope, so this is not
                # the API's own 404. Try the next transport.
                raise _HostRejection(_short(raw) or str(error.reason)) from None
            raise ApiError(_message_of(error.code, raw, error.reason), error.code) from None
        except urllib.error.URLError as error:
            raise ApiError(f"cannot reach the FundingRadar API at {self._base}: {error.reason}") from None
        except TimeoutError:
            raise ApiError(f"the FundingRadar API at {self._base} did not answer within {self._timeout:.0f}s") from None

        try:
            body = json.loads(payload)
        except json.JSONDecodeError:
            raise ApiError(
                "the FundingRadar API answered with something that is not JSON "
                f"(is {self._base} the application root, not a sub-path?)"
            ) from None
        if not isinstance(body, dict):
            raise ApiError("the FundingRadar API answered with an unexpected payload")
        return body

    @staticmethod
    def _encode(params: dict[str, Any]) -> str:
        """Build the query string, dropping unset values and stringifying the rest."""
        clean: dict[str, str] = {}
        for key, value in params.items():
            if value is None or value == "":
                continue
            clean[key] = "true" if value is True else "false" if value is False else str(value)
        return "?" + urllib.parse.urlencode(clean) if clean else ""


class _HostRejection(RuntimeError):
    """A non-JSON rejection from whatever sits in front of the application.

    Kept as a safety net: it was built when the host appeared to answer its own 404 on certain
    headers, a behaviour that did not reproduce on 2026-10-09 (those 404s came from a fatal
    error in the app). Trying the next transport costs one request and still protects against
    a host that really does filter.
    """


def _read(error: urllib.error.HTTPError) -> str:
    try:
        return error.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 — a body we cannot read is a body we do not need
        return ""


def _is_json(raw: str) -> bool:
    try:
        return isinstance(json.loads(raw), (dict, list))
    except (json.JSONDecodeError, ValueError):
        return False


def _short(raw: str) -> str:
    """A one-line summary of a non-JSON body, for the error message."""
    text = " ".join(raw.split())
    return text[:80]

def _message_of(status: int, raw: str, reason: Any) -> str:
    """The API's own error message, with a hint about what the status means."""
    try:
        message = str(json.loads(raw).get("error") or raw)
    except (json.JSONDecodeError, ValueError, AttributeError):
        message = raw.strip() or str(reason)

    hint = _STATUS_HINTS.get(status)
    if hint:
        return f"HTTP {status}: {message} ({hint})"
    return f"HTTP {status}: {message}"
