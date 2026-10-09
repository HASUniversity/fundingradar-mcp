#!/usr/bin/env python3
"""Print one real sample per tool, to document the payload shapes from actual output.

Run with a token in the environment or in .env (same variables as the server):

    python tests/print_samples.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fundingradar_mcp.client import FundingRadarApi  # noqa: E402
from fundingradar_mcp.config import load_settings  # noqa: E402
from fundingradar_mcp.service import FundingRadarService  # noqa: E402


def _dotenv() -> None:
    path = pathlib.Path(__file__).resolve().parents[1] / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def sample(payload: dict) -> str:
    """A compact JSON view: long strings cut off, so the shape stays readable."""
    def shorten(value):
        if isinstance(value, str):
            return value[:60] + ("…" if len(value) > 60 else "")
        if isinstance(value, dict):
            return {key: shorten(item) for key, item in list(value.items())[:14]}
        if isinstance(value, list):
            return [shorten(item) for item in value[:2]]
        return value

    return json.dumps(shorten(payload), ensure_ascii=False, indent=2)


def main() -> int:
    _dotenv()
    settings = load_settings()
    print(f"api: {settings.base_url} (token {settings.masked_token()})\n")
    service = FundingRadarService(FundingRadarApi(settings.base_url, settings.token, settings.timeout))

    print("### search_calls(status='open', limit=1)")
    found = service.search_calls(status="open", limit=1)
    print("top-level keys:", sorted(found))
    print("call keys:", sorted(found["calls"][0]) if found["calls"] else "—")
    print(sample(found))

    print("\n### calls_for_research_group('green-health', limit=1)")
    group = service.calls_for_research_group(research_group="green-health", limit=1)
    print("top-level keys:", sorted(group))
    print("match keys:", sorted(group["matches"][0]) if group["matches"] else "—")
    print(sample(group))

    if found["calls"]:
        print("\n### get_call(<uuid van de gevonden call>)")
        detail = service.get_call(found["calls"][0]["public_id"])
        print("top-level keys:", sorted(detail))
        print("call keys:", len(detail["call"]), "velden")
        print("matched_research_groups keys:",
              sorted(detail["matched_research_groups"][0]) if detail["matched_research_groups"] else "—")

    print("\n### list_sources(active_only=True)")
    sources = service.list_sources(active_only=True)
    print("top-level keys:", sorted(sources))
    print(sample(sources))

    print("\n### list_research_groups()")
    groups = service.list_research_groups()
    print("top-level keys:", sorted(groups))
    print("group keys:", sorted(groups["research_groups"][0]))
    print(sample(groups))

    print("\n### funding_stats(group_by='focus_area', months=24)")
    stats = service.funding_stats(group_by="focus_area", months=24)
    print("top-level keys:", sorted(stats))
    print(sample(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
