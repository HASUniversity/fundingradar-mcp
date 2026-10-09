"""HTTP client for the FundingRadar public API (api/v1).

Uses only the standard library: the server used to talk to PostgreSQL directly, and it
does not need a new dependency to talk to HTTP instead.

Error mapping is the point of this module. The API answers a failure with a status and a
human-readable `{"error": "..."}`; that message is what a calling agent needs, so it is
surfaced verbatim, with one line about what the status implies.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_TIMEOUT = 30.0

#: Status -> what it means for this client, appended to the API's own message.
_STATUS_HINTS = {
    401: "the API token is missing, revoked or expired — create a new one in FundingRadar under Settings → API access",
    403: "this token does not have the scope the endpoint requires",
    404: "nothing matches that identifier; check the slug or UUID",
    422: "the request parameters were rejected",
    429: "too many requests for this token; wait and try again",
}


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

    @property
    def base_url(self) -> str:
        return self._base

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET one endpoint and return its parsed body ({"data": ..., "meta": ...})."""
        query = self._encode(params or {})
        url = f"{self._base}/{path.lstrip('/')}{query}"
        request = urllib.request.Request(
            url,
            headers={
                # The token travels in both accepted headers. `Authorization: Bearer` is
                # the standard, but the production host answers its own 404 on any
                # request that carries it (nginx, before PHP runs), while X-API-Token
                # does arrive. Sending both means one client works everywhere; the
                # server prefers the standard header when both are present.
                "Authorization": f"Bearer {self._token}",
                "X-API-Token": self._token,
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            raise ApiError(_message_of(error), error.code) from None
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


def _message_of(error: urllib.error.HTTPError) -> str:
    """The API's own error message, with a hint about what the status means."""
    raw = ""
    try:
        raw = error.read().decode("utf-8", "replace")
        body = json.loads(raw)
        message = str(body.get("error") or raw)
    except (json.JSONDecodeError, AttributeError):
        message = raw.strip() or error.reason

    hint = _STATUS_HINTS.get(error.code)
    if hint:
        return f"HTTP {error.code}: {message} ({hint})"
    return f"HTTP {error.code}: {message}"
