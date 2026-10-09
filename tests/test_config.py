"""Tests for fundingradar_mcp.config and fundingradar_mcp.client. No network needed.

Run from the repository root:  python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import base64
import io
import json
import unittest
import urllib.error
from unittest import mock

from fundingradar_mcp.client import ApiError, FundingRadarApi
from fundingradar_mcp.config import ConfigError, Settings, load_settings

BASE = {
    "FUNDINGRADAR_API_URL": "https://example.org/showcases/fundingradar",
    "FUNDINGRADAR_API_TOKEN": "fdr_" + "a" * 28,
}


class LoadSettingsTests(unittest.TestCase):
    def test_base_url_and_token_are_used(self) -> None:
        settings = load_settings(dict(BASE))
        self.assertEqual(settings.base_url, "https://example.org/showcases/fundingradar")
        self.assertEqual(settings.token, "fdr_" + "a" * 28)
        self.assertEqual(settings.timeout, 30.0)

    def test_the_old_prefix_is_still_accepted(self) -> None:
        # Tokens minted before the prefix changed must keep working.
        settings = load_settings(dict(BASE, FUNDINGRADAR_API_TOKEN="fr_" + "a" * 28))
        self.assertEqual(settings.token, "fr_" + "a" * 28)

    def test_trailing_slash_is_dropped(self) -> None:
        env = dict(BASE, FUNDINGRADAR_API_URL="https://example.org/app/")
        self.assertEqual(load_settings(env).base_url, "https://example.org/app")

    def test_default_base_url_is_used_when_unset(self) -> None:
        env = {"FUNDINGRADAR_API_TOKEN": BASE["FUNDINGRADAR_API_TOKEN"]}
        self.assertEqual(load_settings(env).base_url, "https://dilab.has.nl/showcases/fundingradar")

    def test_missing_token_is_rejected_with_a_pointer_to_the_settings_page(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            load_settings({"FUNDINGRADAR_API_URL": BASE["FUNDINGRADAR_API_URL"]})
        self.assertIn("Settings → API access", str(caught.exception))

    def test_token_without_the_prefix_is_rejected(self) -> None:
        env = dict(BASE, FUNDINGRADAR_API_TOKEN="d3adb33f")
        with self.assertRaises(ConfigError) as caught:
            load_settings(env)
        self.assertIn("fr_", str(caught.exception))

    def test_url_pointing_at_the_api_directory_is_explained(self) -> None:
        env = dict(BASE, FUNDINGRADAR_API_URL="https://example.org/app/api/v1")
        with self.assertRaises(ConfigError) as caught:
            load_settings(env)
        self.assertIn("application root", str(caught.exception))

    def test_url_must_be_http_or_https(self) -> None:
        for bad in ("example.org/app", "ftp://example.org", "https://"):
            with self.subTest(url=bad):
                with self.assertRaises(ConfigError):
                    load_settings(dict(BASE, FUNDINGRADAR_API_URL=bad))

    def test_timeout_bounds(self) -> None:
        self.assertEqual(load_settings(dict(BASE, FUNDINGRADAR_API_TIMEOUT="5")).timeout, 5.0)
        for bad in ("0", "301", "lang"):
            with self.subTest(timeout=bad):
                with self.assertRaises(ConfigError):
                    load_settings(dict(BASE, FUNDINGRADAR_API_TIMEOUT=bad))

    def test_blank_values_count_as_missing(self) -> None:
        with self.assertRaises(ConfigError):
            load_settings(dict(BASE, FUNDINGRADAR_API_TOKEN="   "))


class MaskedTokenTests(unittest.TestCase):
    def test_only_the_prefix_is_visible(self) -> None:
        settings = Settings(base_url="https://example.org", token="fdr_" + "abcdefgh" + "i" * 20, timeout=30)
        masked = settings.masked_token()
        self.assertTrue(masked.startswith("fdr_abcdefgh"))
        self.assertIn("***", masked)
        self.assertNotIn("i" * 20, masked)

    def test_short_token_is_hidden_entirely(self) -> None:
        self.assertEqual(Settings("https://example.org", "fdr_short", 30).masked_token(), "***")


def _response(payload: str, status: int = 200) -> mock.MagicMock:
    """A urlopen() return value usable as a context manager."""
    handle = mock.MagicMock()
    handle.__enter__ = mock.MagicMock(return_value=handle)
    handle.__exit__ = mock.MagicMock(return_value=False)
    handle.read.return_value = payload.encode("utf-8")
    handle.status = status
    return handle


def _http_error(status: int, payload: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://example.org/api/v1/me.php",
        code=status,
        msg="error",
        hdrs=None,
        fp=io.BytesIO(payload.encode("utf-8")),
    )


class GetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = FundingRadarApi("https://example.org/app", "fdr_" + "a" * 28)

    def test_successful_call_returns_the_parsed_body(self) -> None:
        payload = json.dumps({"data": {"ok": True}, "meta": {"count": 1}})
        with mock.patch("urllib.request.urlopen", return_value=_response(payload)) as opener:
            body = self.api.get("/api/v1/me.php")
        self.assertEqual(body["meta"]["count"], 1)
        request = opener.call_args[0][0]
        self.assertEqual(request.full_url, "https://example.org/app/api/v1/me.php")
        self.assertEqual(request.headers["Authorization"], "Bearer fdr_" + "a" * 28)

    def test_falls_back_to_basic_when_the_host_rejects_the_standard_header(self) -> None:
        # The production host answers its own 404 (HTML, no JSON) before PHP runs.
        html = "<html><head><title>404 Not Found</title></head><body>nginx</body></html>"
        with mock.patch("urllib.request.urlopen",
                        side_effect=[_http_error(404, html), _response("{}")]) as opener:
            self.api.get("/api/v1/me.php")
        self.assertEqual(self.api.transport, "basic")
        second = opener.call_args_list[1][0][0]
        expected = base64.b64encode(("fdr_" + "a" * 28 + ":").encode("utf-8")).decode("ascii")
        self.assertEqual(second.headers["Authorization"], "Basic " + expected)
        self.assertNotIn("X-fundingradar-token", second.headers)

    def test_falls_back_to_the_neutral_header(self) -> None:
        html = "<html><title>404</title></html>"
        with mock.patch("urllib.request.urlopen",
                        side_effect=[_http_error(404, html), _http_error(404, html), _response("{}")]) as opener:
            self.api.get("/api/v1/me.php")
        self.assertEqual(self.api.transport, "x-token")
        third = opener.call_args_list[2][0][0]
        self.assertEqual(third.headers["X-fundingradar-token"], "fdr_" + "a" * 28)

    def test_a_real_api_404_is_not_treated_as_a_host_rejection(self) -> None:
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(404, '{"error": "no such call"}')) as opener:
            with self.assertRaises(ApiError) as caught:
                self.api.get("/api/v1/call.php", {"id": "nope"})
        self.assertIn("no such call", str(caught.exception))
        self.assertEqual(opener.call_count, 1)  # the application answered: no retry

    def test_every_transport_rejected_names_the_host_as_the_cause(self) -> None:
        html = "<html><title>404</title></html>"
        with mock.patch("urllib.request.urlopen", side_effect=[_http_error(404, html)] * 3):
            with self.assertRaises(ApiError) as caught:
                self.api.get("/api/v1/me.php")
        message = str(caught.exception)
        self.assertIn("host", message)
        self.assertIn("bearer", message)

    def test_the_transport_that_worked_is_tried_first_next_time(self) -> None:
        html = "<html><title>404</title></html>"
        with mock.patch("urllib.request.urlopen",
                        side_effect=[_http_error(404, html), _response("{}")]):
            self.api.get("/api/v1/me.php")
        with mock.patch("urllib.request.urlopen", return_value=_response("{}")) as opener:
            self.api.get("/api/v1/me.php")
        request = opener.call_args[0][0]
        self.assertIsNotNone(request.headers.get("Authorization"))
        self.assertTrue(request.headers["Authorization"].startswith("Basic "))

    def test_query_string_is_built_and_empties_are_dropped(self) -> None:
        with mock.patch("urllib.request.urlopen", return_value=_response("{}")) as opener:
            self.api.get("/api/v1/calls.php", {"q": None, "status": "open", "limit": 2, "include_closed": False})
        url = opener.call_args[0][0].full_url
        self.assertNotIn("q=", url)
        self.assertIn("status=open", url)
        self.assertIn("limit=2", url)
        self.assertIn("include_closed=false", url)

    def test_booleans_are_lowercase_for_the_api(self) -> None:
        with mock.patch("urllib.request.urlopen", return_value=_response("{}")) as opener:
            self.api.get("/api/v1/sources.php", {"active_only": True})
        self.assertIn("active_only=true", opener.call_args[0][0].full_url)

    def test_api_error_message_is_surfaced_with_a_hint(self) -> None:
        error = _http_error(401, json.dumps({"error": "Invalid or expired API token"}))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ApiError) as caught:
                self.api.get("/api/v1/me.php")
        message = str(caught.exception)
        self.assertIn("Invalid or expired API token", message)
        self.assertIn("Settings → API access", message)
        self.assertEqual(caught.exception.status, 401)

    def test_unreachable_host_explains_itself(self) -> None:
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("name resolution failed")):
            with self.assertRaises(ApiError) as caught:
                self.api.get("/api/v1/me.php")
        self.assertIn("cannot reach", str(caught.exception))
        self.assertIn("https://example.org/app", str(caught.exception))

    def test_non_json_answer_mentions_the_likely_mistake(self) -> None:
        with mock.patch("urllib.request.urlopen", return_value=_response("<html>404</html>")):
            with self.assertRaises(ApiError) as caught:
                self.api.get("/api/v1/me.php")
        self.assertIn("application root", str(caught.exception))

    def test_timeout_is_reported_as_such(self) -> None:
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError):
            with self.assertRaises(ApiError) as caught:
                self.api.get("/api/v1/me.php")
        self.assertIn("did not answer", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
