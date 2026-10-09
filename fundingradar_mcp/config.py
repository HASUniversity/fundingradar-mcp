"""Connection settings, read strictly from the environment.

The server talks to the FundingRadar API with a personal token, not to PostgreSQL. That
is deliberate: a token belongs to one account, can be revoked per person in the web
application, and its blast radius is the read-only API surface instead of the whole
database (which also holds account hashes and sessions).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

API_URL_VAR = "FUNDINGRADAR_API_URL"
API_TOKEN_VAR = "FUNDINGRADAR_API_TOKEN"
TIMEOUT_VAR = "FUNDINGRADAR_API_TIMEOUT"

DEFAULT_TIMEOUT = 30.0
DEFAULT_BASE_URL = "https://dilab.has.nl/showcases/fundingradar"

#: Shape of a personal token as the settings page issues it. Two prefixes are accepted:
#: the current one, and `fr_` for tokens minted before it changed. The prefix moved during the
#: build, when the host appeared to reject a `fr_` value in a token header - that turned out to
#: be a fatal error in the application, not a host rule (see docs/api-v1.md in the app repo).
#: `fr_` works fine, so both stay accepted.
TOKEN_PREFIXES = ("fdr_", "fr_")


class ConfigError(RuntimeError):
    """Raised when the environment does not describe a usable API connection."""


@dataclass(frozen=True)
class Settings:
    """Validated API settings."""

    base_url: str
    token: str
    timeout: float

    def masked_token(self) -> str:
        """The token with only its prefix visible; safe for logs and error messages."""
        visible = 8  # characters of the body shown next to the prefix
        if len(self.token) <= visible + 4:
            return "***"
        return f"{self.token[: visible + 4]}…***"


def _value(env: Mapping[str, Any], name: str) -> str | None:
    raw = env.get(name)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _base_url(raw: str) -> str:
    """Validate and normalise the application root.

    This is the prefix the api/v1 paths hang off, e.g.
    https://dilab.has.nl/showcases/fundingradar — a trailing slash is dropped, and the
    common mistake of pointing at the API directory itself is rejected with an
    explanation instead of a confusing 404 later.
    """
    candidate = raw.rstrip("/")
    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConfigError(f"{API_URL_VAR} must be a full http(s) URL, got {raw!r}")
    if parts.path.rstrip("/").endswith("/api/v1"):
        raise ConfigError(
            f"{API_URL_VAR} must point at the application root, not at the API directory: "
            f"drop the trailing '/api/v1' (got {raw!r})"
        )
    return candidate


def _timeout(raw: str) -> float:
    try:
        timeout = float(raw)
    except ValueError:
        raise ConfigError(f"{TIMEOUT_VAR} must be a number of seconds, got {raw!r}") from None
    if not 1 <= timeout <= 300:
        raise ConfigError(f"{TIMEOUT_VAR} must be between 1 and 300 seconds, got {timeout}")
    return timeout


def load_settings(env: Mapping[str, Any] | None = None) -> Settings:
    """Build validated settings from ``env`` (defaults to ``os.environ``)."""
    source: Mapping[str, Any] = os.environ if env is None else env

    base = _value(source, API_URL_VAR)
    token = _value(source, API_TOKEN_VAR)

    if base is None:
        # A default is offered so a colleague only has to set the token, but the error
        # message names the variable so a non-default installation is obvious.
        base = DEFAULT_BASE_URL
    if token is None:
        raise ConfigError(
            f"missing required environment variable {API_TOKEN_VAR} — create a token in "
            "FundingRadar under Settings → API access"
        )
    if not token.startswith(TOKEN_PREFIXES):
        raise ConfigError(
            f"{API_TOKEN_VAR} does not look like a FundingRadar API token (expected it to "
            f"start with one of {', '.join(repr(p) for p in TOKEN_PREFIXES)})"
        )

    timeout = _timeout(_value(source, TIMEOUT_VAR) or str(DEFAULT_TIMEOUT))
    return Settings(base_url=_base_url(base), token=token, timeout=timeout)
