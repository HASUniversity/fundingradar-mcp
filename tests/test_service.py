"""Tests for fundingradar_mcp.service: endpoint mapping and output shaping.

A fake API object records the calls, so these tests pin down exactly which endpoint and
parameters each tool uses — the contract with the FundingRadar API. No HTTP, no network.

Run from the repository root:  python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import unittest
from unittest import mock

from fundingradar_mcp.client import ApiError
from fundingradar_mcp.client import FundingRadarApi as Client
from fundingradar_mcp.page import DEFAULT_MAX_CHARS
from fundingradar_mcp.service import FundingRadarService


class FakeApi:
    """Stand-in for FundingRadarApi: returns canned bodies, records the requests."""

    def __init__(self, responses: dict | None = None) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.responses = responses or {}

    def get(self, path: str, params: dict | None = None) -> dict:
        self.requests.append((path, params or {}))
        return self.responses.get(path, {"data": [], "meta": {}})


class SearchCallsTests(unittest.TestCase):
    def test_maps_filters_to_the_calls_endpoint(self) -> None:
        api = FakeApi({
            "/api/v1/calls.php": {
                "data": [{"public_id": "x", "title": "Test"}],
                "meta": {"total": 7, "count": 1, "limit": 5, "offset": 5, "has_more": True},
            }
        })
        result = FundingRadarService(api).search_calls(
            query="waterstof", source="ZonMw", research_group="green-health",
            focus_area="onderzoek", status="open", limit=5, offset=5,
        )

        path, params = api.requests[0]
        self.assertEqual(path, "/api/v1/calls.php")
        self.assertEqual(params["q"], "waterstof")
        self.assertEqual(params["source"], "ZonMw")
        self.assertEqual(params["research_group"], "green-health")
        self.assertEqual(params["focus_area"], "onderzoek")
        self.assertEqual(params["status"], "open")
        self.assertEqual(params["limit"], 5)

        self.assertEqual(result["total_matched"], 7)
        self.assertEqual(result["returned"], 1)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["calls"][0]["title"], "Test")

    def test_total_comes_from_the_api_not_from_counting_the_page(self) -> None:
        api = FakeApi({"/api/v1/calls.php": {"data": [{"id": 1}], "meta": {"total": 400, "count": 1}}})
        result = FundingRadarService(api).search_calls()
        self.assertEqual(result["total_matched"], 400)
        self.assertEqual(result["returned"], 1)

    def test_non_canonical_focus_area_is_refused_before_the_request(self) -> None:
        api = FakeApi()
        with self.assertRaises(ValueError) as caught:
            FundingRadarService(api).search_calls(focus_area="verzonnen")
        self.assertIn("onderzoek", str(caught.exception))
        self.assertEqual(api.requests, [])

    def test_unknown_sort_is_refused_before_the_request(self) -> None:
        api = FakeApi()
        with self.assertRaises(ValueError):
            FundingRadarService(api).search_calls(sort_by="cheapest")
        self.assertEqual(api.requests, [])

    def test_known_sort_is_passed_through(self) -> None:
        api = FakeApi()
        FundingRadarService(api).search_calls(sort_by="budget_desc")
        self.assertEqual(api.requests[0][1]["sort_by"], "budget_desc")

    def test_only_actionable_calls_are_requested_by_default(self) -> None:
        # The API applies the rule; the client must not accidentally ask for the archive.
        api = FakeApi()
        FundingRadarService(api).search_calls()
        self.assertEqual(api.requests[0][1]["include_closed"], False)

    def test_include_closed_is_passed_through(self) -> None:
        api = FakeApi()
        FundingRadarService(api).search_calls(include_closed=True)
        self.assertEqual(api.requests[0][1]["include_closed"], True)


class GetCallTests(unittest.TestCase):
    def test_maps_to_the_call_endpoint_and_reshapes_the_payload(self) -> None:
        api = FakeApi({
            "/api/v1/call.php": {
                "data": {
                    "call": {"public_id": "abc", "title": "Test"},
                    "research_groups": [{"slug": "green-health", "match_reason": "want relevant"}],
                    "themes": [{"slug": "t"}],
                    "tags": [{"name": "AI"}],
                }
            }
        })
        result = FundingRadarService(api).get_call("abc")

        self.assertEqual(api.requests[0], ("/api/v1/call.php", {"id": "abc"}))
        self.assertEqual(result["call"]["title"], "Test")
        self.assertEqual(result["matched_research_groups"][0]["slug"], "green-health")
        self.assertEqual(result["matched_research_themes"], [{"slug": "t"}])
        self.assertEqual(result["tags"], [{"name": "AI"}])


class CallsForResearchGroupTests(unittest.TestCase):
    def test_maps_to_the_group_endpoint(self) -> None:
        api = FakeApi({
            "/api/v1/research_groups.php": {
                "data": {
                    "research_group": {"slug": "green-health", "name": "Groene Gezondheid"},
                    "matches": [{"public_id": "abc", "match_reason": "want relevant", "email_sent": True}],
                },
                "meta": {"total": 42, "count": 1, "limit": 20, "offset": 0, "has_more": True, "include_closed": False},
            }
        })
        result = FundingRadarService(api).calls_for_research_group(research_group="green-health")

        path, params = api.requests[0]
        self.assertEqual(path, "/api/v1/research_groups.php")
        self.assertEqual(params["slug"], "green-health")
        self.assertFalse(params["include_closed"])
        self.assertEqual(result["total_matched"], 42)
        self.assertTrue(result["has_more"])
        self.assertTrue(result["matches"][0]["email_sent"])


class ListTests(unittest.TestCase):
    def test_sources_counts_are_taken_from_the_api_meta(self) -> None:
        api = FakeApi({
            "/api/v1/sources.php": {
                "data": [{"short_name": "RVO"}, {"short_name": "ZonMw"}],
                "meta": {"active_only": True, "total_calls": 4434},
            }
        })
        result = FundingRadarService(api).list_sources(active_only=True)
        self.assertEqual(api.requests[0][1], {"active_only": True})
        self.assertEqual(result["source_count"], 2)
        self.assertEqual(result["total_calls"], 4434)

    def test_research_groups_sum_their_match_counts(self) -> None:
        api = FakeApi({
            "/api/v1/research_groups.php": {
                "data": [{"slug": "a", "call_count": 5}, {"slug": "b", "call_count": 7}],
            }
        })
        result = FundingRadarService(api).list_research_groups()
        self.assertEqual(result["research_group_count"], 2)
        self.assertEqual(result["total_matched_calls"], 12)

    def test_missing_call_count_does_not_crash(self) -> None:
        api = FakeApi({"/api/v1/research_groups.php": {"data": [{"slug": "a"}]}})
        self.assertEqual(FundingRadarService(api).list_research_groups()["total_matched_calls"], 0)


class FundingStatsTests(unittest.TestCase):
    def test_group_by_is_validated_then_passed_through(self) -> None:
        api = FakeApi({
            "/api/v1/stats.php": {
                "data": {"buckets": [{"group_key": "RVO", "call_count": 12}]},
                "meta": {"group_by": "source", "months": 24, "bucket_count": 1, "counted_calls": 12},
            }
        })
        result = FundingRadarService(api).funding_stats(group_by="source", months=24)
        self.assertEqual(api.requests[0], ("/api/v1/stats.php", {"group_by": "source", "months": 24}))
        self.assertEqual(result["counted_calls"], 12)
        self.assertEqual(result["buckets"][0]["group_key"], "RVO")

    def test_unknown_group_by_is_refused_before_the_request(self) -> None:
        api = FakeApi()
        with self.assertRaises(ValueError) as caught:
            FundingRadarService(api).funding_stats(group_by="colour")
        self.assertIn("research_group", str(caught.exception))
        self.assertEqual(api.requests, [])


class SearchExpansionTests(unittest.TestCase):
    """search_calls asks the API to expand the question, and shows what it searched."""

    def _api(self):
        return FakeApi({
            "/api/v1/calls.php": {
                "data": [{"public_id": "x"}],
                "meta": {"total": 210, "count": 1, "expanded_terms": ["bodemkwaliteit", "soil"]},
            }
        })

    def test_expansion_is_on_by_default_and_reported(self) -> None:
        api = self._api()
        result = FundingRadarService(api).search_calls(query="bodemkwaliteit en water")

        _, params = api.requests[0]
        self.assertIs(params["expand"], True)
        self.assertEqual(result["searched_terms"], ["bodemkwaliteit", "soil"])
        self.assertEqual(result["total_matched"], 210)

    def test_expansion_can_be_turned_off(self) -> None:
        api = self._api()
        FundingRadarService(api).search_calls(query="bodem", expand=False)
        _, params = api.requests[0]
        self.assertIs(params["expand"], False)

    def test_no_query_means_nothing_to_expand(self) -> None:
        api = self._api()
        FundingRadarService(api).search_calls(source="RVO")
        _, params = api.requests[0]
        self.assertIsNone(params["q"])
        self.assertIsNone(params["expand"], "expand is only sent together with a query")
        # The client drops unset values, so neither parameter reaches the API.
        self.assertNotIn("expand=", Client._encode(params))
        self.assertNotIn("q=", Client._encode(params))


class ReadCallPageTests(unittest.TestCase):
    """read_call_page: the call's URL goes in, the page's text comes out."""

    def _service(self, call: dict) -> tuple[FundingRadarService, FakeApi]:
        api = FakeApi({"/api/v1/call.php": {"data": {"call": call}, "meta": {}}})
        return FundingRadarService(api), api

    def test_reads_the_calls_own_page(self) -> None:
        service, api = self._service({
            "public_id": "abc", "title": "Bodem en water", "status": "open",
            "deadline": "2027-03-31", "url": "https://example.org/bodem",
            "tracked_url": "https://dilab.has.nl/showcases/fundingradar/api/click.php?call_id=1",
        })

        with mock.patch("fundingradar_mcp.service.fetch_page_text") as fetch:
            fetch.return_value = {"url": "https://example.org/bodem", "text": "Nog open tot 31 maart 2027"}
            result = service.read_call_page(public_id="abc")

        fetch.assert_called_once_with("https://example.org/bodem", max_chars=DEFAULT_MAX_CHARS)
        self.assertEqual(api.requests[0], ("/api/v1/call.php", {"id": "abc"}))
        self.assertEqual(result["call"]["title"], "Bodem en water")
        self.assertEqual(result["call"]["url"], "https://example.org/bodem")
        self.assertIn("Nog open", result["page"]["text"])
        self.assertIn("daily pipeline", result["note"])

    def test_falls_back_to_the_application_page(self) -> None:
        service, _ = self._service({"public_id": "abc", "url": None, "apply_url": "https://example.org/apply"})

        with mock.patch("fundingradar_mcp.service.fetch_page_text") as fetch:
            fetch.return_value = {"text": "Aanvragen"}
            service.read_call_page(public_id="abc")

        fetch.assert_called_once_with("https://example.org/apply", max_chars=DEFAULT_MAX_CHARS)

    def test_passes_the_requested_length_through(self) -> None:
        service, _ = self._service({"public_id": "abc", "url": "https://example.org/x"})

        with mock.patch("fundingradar_mcp.service.fetch_page_text") as fetch:
            fetch.return_value = {"text": "kort"}
            service.read_call_page(public_id="abc", max_chars=2000)

        fetch.assert_called_once_with("https://example.org/x", max_chars=2000)

    def test_a_call_without_any_url_is_refused_clearly(self) -> None:
        service, _ = self._service({"public_id": "abc", "url": None, "apply_url": None})

        with mock.patch("fundingradar_mcp.service.fetch_page_text") as fetch:
            with self.assertRaises(ApiError) as caught:
                service.read_call_page(public_id="abc")

        self.assertIn("no page to read", str(caught.exception))
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
