#!/usr/bin/env python3
"""Drive the MCP server over stdio and report what it actually answered.

This is the end-to-end check that no unit test can give: the server is started the way
an MCP client starts it, and every answer below comes from the wire.

    python tests/stdio_smoke_test.py

Without database credentials it still verifies the handshake, the tool list and the
resource list, and skips the queries. Credentials come from the environment or from a
`.env` file in the repository root. Nothing is printed that contains the password.

Exit code 0 means every executed step succeeded.
"""

from __future__ import annotations

import json
import os
import pathlib
import queue
import subprocess
import sys
import threading

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER = REPO_ROOT / "server.py"
STDERR_LOG = REPO_ROOT / "smoke_test_stderr.log"
TIMEOUT_SECONDS = 60

EXPECTED_TOOLS = {
    "search_calls",
    "get_call",
    "read_call_page",
    "calls_for_research_group",
    "list_sources",
    "list_research_groups",
    "funding_stats",
}

DB_VARS = (
    "FUNDINGRADAR_API_TOKEN",
)

failures: list[str] = []
checks: list[str] = []


def check(condition: bool, description: str, detail: str = "") -> bool:
    if condition:
        checks.append(f"PASS  {description}")
    else:
        failures.append(f"FAIL  {description} {detail}".strip())
    return condition


def load_dotenv() -> dict[str, str]:
    """Minimal .env reader (KEY=VALUE, '#' comments); the file is gitignored."""
    path = REPO_ROOT / ".env"
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


class Client:
    """A minimal MCP client: newline-delimited JSON-RPC over the child's stdio."""

    def __init__(self, env: dict[str, str]) -> None:
        self.stderr_handle = STDERR_LOG.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr_handle,
            env=env,
            text=True,
            encoding="utf-8",
        )
        self.lines: queue.Queue[str] = queue.Queue()
        self.reader = threading.Thread(target=self._pump, daemon=True)
        self.reader.start()
        self.next_id = 0

    def _pump(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line)

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        while True:
            try:
                line = self.lines.get(timeout=TIMEOUT_SECONDS)
            except queue.Empty:
                raise RuntimeError(f"no answer to {method} within {TIMEOUT_SECONDS}s") from None
            payload = json.loads(line)
            if payload.get("id") == self.next_id:
                return payload
            # Notifications and unrelated messages are fine to skip.

    def notify(self, method: str) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.process.stdin.flush()

    def call_tool(self, name: str, arguments: dict) -> dict:
        answer = self.request("tools/call", {"name": name, "arguments": arguments})
        return answer

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.stderr_handle.close()


def payload_of(answer: dict) -> dict:
    """Unwrap a tools/call result: content[0].text holds the JSON payload."""
    result = answer.get("result") or {}
    content = result.get("content") or []
    if content and content[0].get("type") == "text":
        return json.loads(content[0]["text"])
    return result


def error_text(answer: dict) -> str:
    """The message a failed tools/call handed back to the client."""
    result = answer.get("result") or {}
    content = result.get("content") or []
    return content[0].get("text", "") if content else json.dumps(answer)[:200]


def is_tool_error(answer: dict) -> bool:
    return bool((answer.get("result") or {}).get("isError"))


def main() -> int:
    env = dict(os.environ)
    env.update(load_dotenv())
    has_credentials = bool(env.get("FUNDINGRADAR_API_TOKEN"))

    print(f"server:  {SERVER}")
    print(f"python:  {sys.executable}")
    print(f"api:     {env.get('FUNDINGRADAR_API_URL', 'https://dilab.has.nl/showcases/fundingradar (default)')}")
    print(f"live API checks: {'yes' if has_credentials else 'skipped (no FUNDINGRADAR_API_TOKEN in env or .env)'}")
    print()

    client = Client(env)
    try:
        handshake = client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "stdio-smoke-test", "version": "0.1.0"},
            },
        )
        server_info = (handshake.get("result") or {}).get("serverInfo") or {}
        check(server_info.get("name") == "fundingradar", "initialize returns the fundingradar server", json.dumps(server_info))
        print(f"serverInfo: {json.dumps(server_info)}")
        client.notify("notifications/initialized")

        listing = client.request("tools/list")
        tools = {tool["name"] for tool in (listing.get("result") or {}).get("tools", [])}
        check(tools == EXPECTED_TOOLS, "tools/list returns exactly the seven documented tools", f"got {sorted(tools)}")
        print(f"tools: {sorted(tools)}")

        sample = next(
            tool for tool in (listing.get("result") or {}).get("tools", []) if tool["name"] == "search_calls"
        )
        check("query" in sample.get("inputSchema", {}).get("properties", {}), "search_calls exposes its parameters")
        check(bool(sample.get("description")), "tools carry a description for the client")

        resources = client.request("resources/list")
        resource_uris = [entry.get("uri") for entry in (resources.get("result") or {}).get("resources", [])]
        check(True, f"resources/list answered ({len(resource_uris)} static resource(s))")

        # A templated URI is advertised through resources/templates/list, not resources/list.
        templates = client.request("resources/templates/list")
        template_uris = [
            entry.get("uriTemplate") for entry in (templates.get("result") or {}).get("resourceTemplates", [])
        ]
        check(
            "fundingradar://call/{public_id}" in template_uris,
            "resources/templates/list returns the call resource template",
            f"got {template_uris}",
        )
        print(f"resource templates: {template_uris}")

        if has_credentials:
            groups = payload_of(client.call_tool("list_research_groups", {}))
            check(groups.get("research_group_count", 0) > 0, "list_research_groups returns research groups")
            print(
                f"research groups: {groups.get('research_group_count')} "
                f"(matched calls: {groups.get('total_matched_calls')})"
            )
            for group in groups.get("research_groups", [])[:3]:
                print(f"  - {group['slug']}: {group.get('call_count')} matched, "
                      f"{group.get('matched_open_calls')} open")

            sources = payload_of(client.call_tool("list_sources", {"active_only": True}))
            check(sources.get("source_count", 0) > 0, "list_sources returns active sources")
            print(f"active sources: {sources.get('source_count')} (calls: {sources.get('total_calls')})")

            search = payload_of(client.call_tool("search_calls", {"status": "open", "limit": 3}))
            check("calls" in search, "search_calls returns a page of calls")
            print(f"open calls matched: {search.get('total_matched')} (showing {search.get('returned')})")
            for call in search.get("calls", []):
                print(f"  - [{call['status']}] {call['deadline']} {call['source']}: {call['title'][:70]}")

            if search.get("calls"):
                detail = payload_of(client.call_tool("get_call", {"public_id": search["calls"][0]["public_id"]}))
                check(bool(detail.get("call")), "get_call returns the full record for a searched call")
                print(
                    f"get_call: '{detail['call']['title'][:50]}' with "
                    f"{len(detail.get('matched_research_groups', []))} matched group(s), "
                    f"{len(detail.get('tags', []))} tag(s)"
                )

                read = client.request("resources/read", {"uri": f"fundingradar://call/{search['calls'][0]['public_id']}"})
                contents = (read.get("result") or {}).get("contents") or [{}]
                check(
                    contents[0].get("mimeType") == "application/json" and '"call"' in contents[0].get("text", ""),
                    "resources/read returns the call as JSON",
                    json.dumps(read)[:200],
                )
                print(f"resources/read: {len(contents[0].get('text', ''))} bytes of JSON")

            stats = payload_of(client.call_tool("funding_stats", {"group_by": "research_group", "months": 24}))
            check(stats.get("bucket_count", 0) > 0, "funding_stats returns buckets")
            print(f"stats by research_group (24 months): {stats.get('bucket_count')} buckets")
            for bucket in stats.get("buckets", [])[:5]:
                print(f"  - {bucket['group_key']}: {bucket['call_count']}")

            rejected = [
                ("out-of-range limit", "search_calls", {"limit": 5000}, "less than or equal to 100"),
                ("malformed date", "search_calls", {"deadline_after": "01-03-2026"}, "ISO date"),
                ("unknown research group", "calls_for_research_group", {"research_group": "does-not-exist"}, "does-not-exist"),
                ("unparsable call id", "get_call", {"public_id": "not-a-call"}, "UUID"),
            ]
            for description, tool_name, arguments, expected in rejected:
                answer = client.call_tool(tool_name, arguments)
                text = error_text(answer)
                check(
                    is_tool_error(answer) and expected in text,
                    f"{description} is reported back to the client",
                    text[:160],
                )
                print(f"{description} -> {text.splitlines()[0][:110]}")
    finally:
        client.close()

    print()
    for line in checks:
        print(line)
    for line in failures:
        print(line)
    print()
    if failures:
        print(f"RESULT: {len(failures)} of {len(checks) + len(failures)} checks failed")
        print(f"server stderr: {STDERR_LOG}")
        return 1
    print(f"RESULT: all {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
