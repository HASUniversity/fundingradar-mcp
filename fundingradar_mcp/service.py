"""One method per MCP tool: calls the FundingRadar API and shapes the payload.

The tool outputs keep the shape they had when this server queried PostgreSQL directly, so
a client (or a skill, or a saved prompt) that worked before still works. What changed is
where the data comes from: the read-only API, authenticated with a personal token.

The API's `meta` block carries the counts this layer used to compute itself, so there is
one definition of "how many calls match" — the server's.
"""

from __future__ import annotations

from typing import Any

from .client import FundingRadarApi, ApiError
from .page import DEFAULT_MAX_CHARS, fetch_page_text

#: The only focus areas that are reliably present in the data; the API rejects others.
CANONICAL_FOCUS_AREAS = ("onderzoek", "onderwijs", "zakelijke_dienstverlening")

#: Sorts the API accepts (see docs/TOOLS.md).
SORTS = (
    "deadline_asc", "deadline_strict", "deadline_desc", "created_at_desc", "created_at_asc",
    "title_asc", "title_desc", "budget_desc", "budget_asc", "status_priority",
)

#: Groups the statistics endpoint accepts.
STATS_GROUPS = (
    "source", "month", "status", "language", "funding_type", "focus_area", "research_group",
)


class FundingRadarService:
    """Read-only view of FundingRadar, over the public API."""

    def __init__(self, api: FundingRadarApi) -> None:
        self._api = api

    def search_calls(
        self,
        *,
        query: Any = None,
        source: Any = None,
        research_group: Any = None,
        focus_area: Any = None,
        status: Any = None,
        funding_type: Any = None,
        language: Any = None,
        deadline_after: Any = None,
        deadline_before: Any = None,
        sort_by: Any = None,
        include_closed: Any = False,
        include_ineligible: Any = False,
        expand: Any = True,
        limit: Any = 20,
        offset: Any = 0,
    ) -> dict[str, Any]:
        if focus_area is not None and focus_area not in CANONICAL_FOCUS_AREAS:
            raise ValueError(
                "focus_area must be one of: " + ", ".join(CANONICAL_FOCUS_AREAS)
                + " (other values in that column are free text from the extraction pipeline)"
            )
        if sort_by is not None and sort_by not in SORTS:
            raise ValueError("sort_by must be one of: " + ", ".join(SORTS))

        params = {
            "query": query,
            "source": source,
            "research_group": research_group,
            "focus_area": focus_area,
            "status": status,
            "funding_type": funding_type,
            "language": language,
            "deadline_after": deadline_after,
            "deadline_before": deadline_before,
            "sort_by": sort_by,
            "include_closed": include_closed,
            "include_ineligible": include_ineligible,
            # The API expands the question by default (Dutch/English equivalents, and the call's
            # own URLs count as a hit); without it a whole paragraph finds nothing. `literal=1` is
            # the opt-out, and it also passes the host, which 404s a query string that would run
            # the expanded search — see docs/api-v1.md in the app repository.
            "literal": True if (query and not expand) else None,
            "limit": limit,
            "offset": offset,
        }
        # A search goes in a POST body: the host in front of the API answers its own 404 on a
        # request whose query string would run the expanded search, and a body is not part of it.
        if query:
            body = self._api.post_json("/api/v1/calls.php", params)
        else:
            body = self._api.get("/api/v1/calls.php", params)
        meta = body.get("meta", {})
        return {
            "total_matched": meta.get("total", 0),
            "returned": meta.get("count", 0),
            "offset": meta.get("offset", offset),
            "limit": meta.get("limit", limit),
            "has_more": meta.get("has_more", False),
            # How the question was interpreted: the words that were actually searched, so an
            # agent can see why something did or did not come back.
            "searched_terms": meta.get("expanded_terms", []),
            "calls": body.get("data", []),
        }

    def get_call(self, public_id: Any) -> dict[str, Any]:
        body = self._api.get("/api/v1/call.php", {"id": public_id})
        data = body.get("data", {})
        return {
            "call": data.get("call", {}),
            "matched_research_groups": data.get("research_groups", []),
            "matched_research_themes": data.get("themes", []),
            "tags": data.get("tags", []),
        }

    def read_call_page(self, *, public_id: Any, max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
        """The call's own page, as text, so an agent can judge the current status.

        The stored status and deadline come from the daily pipeline; the funder's page is the
        source of truth for "is this still open". A page that cannot be read raises
        PageUnavailable with a message the agent can pass on, rather than pretending.
        """
        detail = self.get_call(public_id)
        call = detail.get("call", {}) or {}
        url = call.get("url") or call.get("apply_url") or call.get("call_document_url")
        if not url:
            raise ApiError(
                "this call has no page to read: url, apply_url and call_document_url are all empty"
            )
        page = fetch_page_text(str(url), max_chars=max_chars)
        return {
            "call": {key: call.get(key) for key in (
                "public_id", "title", "source", "status", "deadline", "url", "apply_url", "tracked_url",
            )},
            "page": page,
            "note": (
                "The stored status and deadline come from the daily pipeline and can lag behind "
                "the funder's page. Judge the current status from the page text; if the page could "
                "not be read, say that instead of guessing."
            ),
        }

    def calls_for_research_group(
        self,
        *,
        research_group: Any,
        include_closed: Any = False,
        limit: Any = 20,
        offset: Any = 0,
    ) -> dict[str, Any]:
        body = self._api.get(
            "/api/v1/research_groups.php",
            {
                "slug": research_group,
                "include_closed": include_closed,
                "limit": limit,
                "offset": offset,
            },
        )
        data = body.get("data", {})
        meta = body.get("meta", {})
        return {
            "research_group": data.get("research_group", {}),
            "include_closed": meta.get("include_closed", include_closed),
            "total_matched": meta.get("total", 0),
            "returned": meta.get("count", 0),
            "offset": meta.get("offset", offset),
            "limit": meta.get("limit", limit),
            "has_more": meta.get("has_more", False),
            "matches": data.get("matches", []),
        }

    def list_sources(self, *, active_only: Any = True) -> dict[str, Any]:
        body = self._api.get("/api/v1/sources.php", {"active_only": active_only})
        sources = body.get("data", [])
        return {
            "active_only": body.get("meta", {}).get("active_only", active_only),
            "source_count": len(sources),
            "total_calls": body.get("meta", {}).get("total_calls", 0),
            "sources": sources,
        }

    def list_research_groups(self) -> dict[str, Any]:
        body = self._api.get("/api/v1/research_groups.php")
        groups = body.get("data", [])
        return {
            "research_group_count": len(groups),
            "total_matched_calls": sum(int(group.get("call_count") or 0) for group in groups),
            "research_groups": groups,
        }

    def funding_stats(self, *, group_by: Any = "source", months: Any = 12) -> dict[str, Any]:
        if group_by not in STATS_GROUPS:
            raise ValueError("group_by must be one of: " + ", ".join(STATS_GROUPS))
        body = self._api.get("/api/v1/stats.php", {"group_by": group_by, "months": months})
        meta = body.get("meta", {})
        return {
            "group_by": meta.get("group_by", group_by),
            "months": meta.get("months", months),
            "bucket_count": meta.get("bucket_count", 0),
            "counted_calls": meta.get("counted_calls", 0),
            "buckets": body.get("data", {}).get("buckets", []),
        }


__all__ = ["FundingRadarService", "ApiError", "CANONICAL_FOCUS_AREAS", "SORTS", "STATS_GROUPS"]
