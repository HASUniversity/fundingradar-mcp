"""MCP wiring: the tools, the resource, and the stdio entry point.

The API client is built on the first tool call rather than at import, so a client can
always start the server and list its tools even when the token is missing or wrong; the
failure then appears as a clear error on the call that needs the API.

Parameter descriptions live in ``Annotated[..., Field(description=...)]`` because the
SDK builds the tool schema from the pydantic model; the docstring becomes the tool
description.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Annotated, Any, Iterator

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .client import ApiError, FundingRadarApi
from .config import ConfigError, load_settings
from .service import CANONICAL_FOCUS_AREAS, SORTS, STATS_GROUPS, FundingRadarService

SERVER_NAME = "fundingradar"
SERVER_TITLE = "FundingRadar (HAS green academy)"
SERVER_DESCRIPTION = "Read-only access to the FundingRadar funding-call database."

INSTRUCTIONS = """\
FundingRadar aggregates Dutch and European funding calls (subsidies, grants, tenders) and
matches them to HAS green academy research groups. This server reads the FundingRadar API
with a personal token; it is read-only and has no write path.

What the data looks like (measured against production on 2026-10-09):
* `funding_calls` (4,452 rows) holds the calls. `status` is one of open (346), upcoming
  (313), closed (3,770) or intake_closed (16). `urgency` is EXPIRED, PLAN, HOT or WATCH.
  `deadline` is a date and is the field to filter on: only 590 calls have a deadline in
  the future, so pass `status="open"` or `deadline_after` when the question is about what
  can still be applied for.
* `focus_areas` is an array. Only three values are canonical and reliably present:
  `onderzoek` (1,778 calls), `zakelijke_dienstverlening` (669) and `onderwijs` (139). The
  extraction pipeline also writes free text there, which is why this server refuses to
  filter on anything else.
* `funding_type` is free text and dominated by `grant` (3,227); treat the other values as
  indicative, not as a controlled vocabulary.
* `research_group` (12 rows, `is_professorship` marks the lectoraten) and their matches
  (5,745) hold the reason a call was matched and whether the group was e-mailed
  (`match_reason`, `email_sent`, `notified_at`). 3,107 calls have no group match at all.
* `funding_sources` (32 rows, 31 active) holds the funders that are scraped.

Two defaults to be aware of when a count looks lower than expected: calls the pipeline
classified as not eligible for HAS, and calls with a deadline before 2020, are hidden —
same as in the web application. Pass `include_ineligible=True` for the first group; an
explicit `deadline_after` replaces the 2020 cut-off.
"""

server = MCPServer(
    name=SERVER_NAME,
    title=SERVER_TITLE,
    description=SERVER_DESCRIPTION,
    instructions=INSTRUCTIONS,
    version=__version__,
)

_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

_api: FundingRadarApi | None = None


def _service() -> FundingRadarService:
    """The service, with its API client created on first use."""
    global _api
    if _api is None:
        settings = load_settings()
        _api = FundingRadarApi(settings.base_url, settings.token, settings.timeout)
    return FundingRadarService(_api)


@contextmanager
def _anticipated_failure() -> Iterator[None]:
    """Report an expected failure as a tool error instead of an opaque crash.

    The SDK withholds the message of any exception that is not a ``ToolError``: the
    client then only sees ``Error executing tool <name>`` and cannot tell a rejected
    filter from an unreachable API. These three are failures we saw coming, so their text
    is meant for the calling agent.
    """
    try:
        yield
    except (ValueError, ConfigError, ApiError) as error:
        raise ToolError(str(error)) from None


@server.tool(name="search_calls", title="Search funding calls", annotations=_READ_ONLY)
def search_calls(
    query: Annotated[
        str | None,
        Field(description="Free text matched against the call title and description."),
    ] = None,
    source: Annotated[
        str | None,
        Field(description="Funder: matches the source short name or full name, e.g. 'ZonMw', 'RVO', 'EU Portal'."),
    ] = None,
    research_group: Annotated[
        str | None,
        Field(description="Research group slug or name, e.g. 'green-health'. Only calls the pipeline matched to that group."),
    ] = None,
    focus_area: Annotated[
        str | None,
        Field(description="One focus area: onderzoek, onderwijs or zakelijke_dienstverlening."),
    ] = None,
    status: Annotated[
        str | None,
        Field(description="Exact status: open, upcoming, closed or intake_closed."),
    ] = None,
    funding_type: Annotated[
        str | None,
        Field(description="Exact funding type, mostly 'grant' (e.g. subsidy, investment, prize, loan)."),
    ] = None,
    language: Annotated[str | None, Field(description="Call language: 'nl' or 'en'.")] = None,
    deadline_after: Annotated[
        str | None,
        Field(description="Only calls with a deadline on or after this ISO date (YYYY-MM-DD). Replaces the default pre-2020 cut-off."),
    ] = None,
    deadline_before: Annotated[
        str | None,
        Field(description="Only calls with a deadline on or before this ISO date (YYYY-MM-DD)."),
    ] = None,
    sort_by: Annotated[
        str | None,
        Field(description="Result order: " + ", ".join(SORTS) + ". Default deadline_asc (nearest deadline first)."),
    ] = None,
    include_closed: Annotated[
        bool,
        Field(description="Also return calls that are closed or past their deadline. Off by default: an agent asking 'is there a call for this?' should get calls it can still act on, not the archive."),
    ] = False,
    include_ineligible: Annotated[
        bool,
        Field(description="Include calls the pipeline classified as not eligible for HAS; off by default."),
    ] = False,
    limit: Annotated[int, Field(description="Page size, 1-100.", ge=1, le=100)] = 20,
    offset: Annotated[int, Field(description="Rows to skip, for paging.", ge=0)] = 0,
) -> dict[str, Any]:
    """Search funding calls with optional filters; returns a page plus the total match count.

    By default only calls that can still be acted on: not a closed round, and not past the
    deadline. Pass include_closed=True for the archive, or name an explicit status.
    """
    with _anticipated_failure():
        return _service().search_calls(
            query=query,
            source=source,
            research_group=research_group,
            focus_area=focus_area,
            status=status,
            funding_type=funding_type,
            language=language,
            deadline_after=deadline_after,
            deadline_before=deadline_before,
            sort_by=sort_by,
            include_closed=include_closed,
            include_ineligible=include_ineligible,
            limit=limit,
            offset=offset,
        )


@server.tool(name="get_call", title="Get one funding call", annotations=_READ_ONLY)
def get_call(
    public_id: Annotated[
        str,
        Field(description="The call's public UUID, or its numeric id (both are accepted)."),
    ],
) -> dict[str, Any]:
    """Full detail of one funding call, plus its matched research groups, themes and tags."""
    with _anticipated_failure():
        return _service().get_call(public_id)


@server.tool(
    name="calls_for_research_group",
    title="Calls matched to a research group",
    annotations=_READ_ONLY,
)
def calls_for_research_group(
    research_group: Annotated[
        str,
        Field(description="Research group slug or name, e.g. 'voedselproductie-circulaire-economie'."),
    ],
    include_closed: Annotated[
        bool,
        Field(description="Also include matches that are closed or past their deadline; off by default, so the answer is what the group can still act on."),
    ] = False,
    limit: Annotated[int, Field(description="Page size, 1-100.", ge=1, le=100)] = 20,
    offset: Annotated[int, Field(description="Rows to skip, for paging.", ge=0)] = 0,
) -> dict[str, Any]:
    """Calls the pipeline matched to one research group, with the match reason and whether the group was notified.

    By default only matches that can still be acted on: not closed, and the deadline not passed.
    """
    with _anticipated_failure():
        return _service().calls_for_research_group(
            research_group=research_group,
            include_closed=include_closed,
            limit=limit,
            offset=offset,
        )


@server.tool(name="list_sources", title="List funding sources", annotations=_READ_ONLY)
def list_sources(
    active_only: Annotated[
        bool,
        Field(description="Only sources with active = true; on by default."),
    ] = True,
) -> dict[str, Any]:
    """The funders that are scraped, with how many calls each has contributed."""
    with _anticipated_failure():
        return _service().list_sources(active_only=active_only)


@server.tool(name="list_research_groups", title="List research groups", annotations=_READ_ONLY)
def list_research_groups() -> dict[str, Any]:
    """All research groups with their theme, match and document counts; use this to find slugs."""
    with _anticipated_failure():
        return _service().list_research_groups()


@server.tool(name="funding_stats", title="Funding call statistics", annotations=_READ_ONLY)
def funding_stats(
    group_by: Annotated[
        str,
        Field(description="How to group the counts: " + ", ".join(STATS_GROUPS) + "."),
    ] = "source",
    months: Annotated[
        int,
        Field(description="Only count calls created in the last N months (1-120).", ge=1, le=120),
    ] = 12,
) -> dict[str, Any]:
    """Call counts per bucket, limited to calls created in the last N months.

    A call matched to several research groups is counted once per group, so the
    research_group buckets do not sum to the number of distinct calls.
    """
    with _anticipated_failure():
        return _service().funding_stats(group_by=group_by, months=months)


@server.resource(
    "fundingradar://call/{public_id}",
    name="funding_call",
    title="Funding call",
    mime_type="application/json",
    description="The full record of one funding call, as JSON.",
)
def funding_call_resource(public_id: str) -> str:
    """Expose one call as a resource, for clients that read resources instead of calling tools."""
    with _anticipated_failure():
        return json.dumps(_service().get_call(public_id), ensure_ascii=False, indent=2)


def main() -> None:
    """Run the server over stdio: what every MCP client spawns."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
