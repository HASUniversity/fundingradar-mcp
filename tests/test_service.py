"""Tests for fundingradar_mcp.service: endpoint mapping and output shaping.

A fake API object records the calls, so these tests pin down exactly which endpoint and
parameters each tool uses — the contract with the FundingRadar API. No HTTP, no network.

Run from the repository root:  python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
