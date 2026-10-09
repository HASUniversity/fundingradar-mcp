"""End-to-end verification of the FundingRadar MCP server against a running installation.

Creates a throwaway user + API token in the database you point it at, runs the MCP server
over stdio with that token, prints what the tools returned, then deletes the user (the
token goes with it via ON DELETE CASCADE). The token secret never leaves this process.

!!  This script WRITES to the database it is pointed at: it inserts one user and one
!!  token, and deletes both afterwards. Point it at production only when that is
!!  intended, and never against a database whose `users` table you do not own.

Run:  python tests/prod_smoke_test.py
Env:  DB_HOST / DB_NAME / DB_USER / DB_PASS / DB_SSLMODE   (the target database)
      FUNDINGRADAR_API_URL                                  optional, defaults to production
      MCP_PYTHON                                            optional, python for server.py
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import secrets
import subprocess
import sys

import psycopg2

# The checkout this file lives in, so the script works on any machine.
MCP_DIR = str(pathlib.Path(__file__).resolve().parents[1])
PROD_URL = "https://dilab.has.nl/showcases/fundingradar"
TEST_EMAIL = "mcp-verificatie@example.invalid"


def dsn() -> str:
    return (
        f"host={os.environ['DB_HOST']} port={os.environ.get('DB_PORT', '5432')} "
        f"dbname={os.environ['DB_NAME']} user={os.environ['DB_USER']} "
        f"password={os.environ['DB_PASS']} sslmode={os.environ.get('DB_SSLMODE', 'require')}"
    )


def send(process: subprocess.Popen, payload: dict) -> None:
    process.stdin.write(json.dumps(payload) + "\n")
    process.stdin.flush()


def read_until(process: subprocess.Popen, wanted_id: int, limit: int = 20) -> dict:
    for _ in range(limit):
        line = process.stdout.readline()
        if not line:
            raise RuntimeError("the MCP server closed its output")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            print(f"   (non-JSON line from the server: {line.strip()[:120]})")
            continue
        if message.get("id") == wanted_id:
            return message
    raise RuntimeError(f"no answer for request id {wanted_id}")


def main() -> int:
    missing = [v for v in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASS") if not os.environ.get(v)]
    if missing:
        print("SKIPPED: this is a database-backed cross-check; set " + ", ".join(missing) + ".")
        print("The API-only checks live in stdio_smoke_test.py and need no database.")
        return 0
    secret = "fdr_" + "".join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_") for _ in range(28))
    token_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()

    with psycopg2.connect(dsn()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (email, password_hash, role, approved, active, email_verified_at, onboarding_done)
                VALUES (%s, %s, 'researcher', true, true, now(), true)
                RETURNING id
                """,
                (TEST_EMAIL, "verification-only-no-login"),
            )
            user_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO api_tokens (user_id, name, token_prefix, token_hash, scopes, expires_at)
                VALUES (%s, %s, %s, %s, ARRAY['read']::text[], now() + interval '1 hour')
                RETURNING id
                """,
                (user_id, "MCP end-to-end verificatie (tijdelijk)", secret[:12], token_hash),
            )
            token_id = cursor.fetchone()[0]
        connection.commit()
    print(f"tijdelijke testgebruiker id={user_id} en token id={token_id} aangemaakt (token niet geprint)\n")

    env = dict(os.environ)
    env["FUNDINGRADAR_API_URL"] = os.environ.get("FUNDINGRADAR_API_URL", PROD_URL)
    env["FUNDINGRADAR_API_TOKEN"] = secret
    env["PYTHONIOENCODING"] = "utf-8"

    process = subprocess.Popen(
        [os.environ.get("MCP_PYTHON", sys.executable), os.path.join(MCP_DIR, "server.py")],
        cwd=MCP_DIR, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
    )
    try:
        send(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "clientInfo": {"name": "prod-verify", "version": "1"}}})
        info = read_until(process, 1)["result"]
        print(f"1. handshake ok — server {info['serverInfo']['name']} {info['serverInfo']['version']}, "
              f"protocol {info['protocolVersion']}")
        send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = [tool["name"] for tool in read_until(process, 2)["result"]["tools"]]
        print(f"2. tools: {', '.join(tools)}")

        calls = [
            ("funding_stats", {}),
            ("list_sources", {}),
            ("search_calls", {"query": "bodem", "limit": 3}),
            ("calls_for_research_group", {"research_group": "levende-bodem", "limit": 2}),
        ]
        for index, (name, arguments) in enumerate(calls, start=3):
            send(process, {"jsonrpc": "2.0", "id": index, "method": "tools/call",
                           "params": {"name": name, "arguments": arguments}})
            answer = read_until(process, index)
            if "error" in answer:
                print(f"{index}. {name} -> FOUT: {json.dumps(answer['error'])[:300]}")
                continue
            content = answer["result"]["content"][0]["text"]
            print(f"{index}. {name}({json.dumps(arguments, ensure_ascii=False)}) ->")
            print("   " + content.replace("\n", "\n   ")[:900])
            print()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        stderr = process.stderr.read()
        if stderr.strip():
            print("server-stderr:", stderr.strip()[-500:])

    with psycopg2.connect(dsn()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
            deleted = cursor.rowcount
            cursor.execute("SELECT count(*) FROM api_tokens WHERE id = %s", (token_id,))
            leftovers = cursor.fetchone()[0]
        connection.commit()
    print(f"\nopgeruimd: gebruiker verwijderd={bool(deleted)}, resterende tokendrijen={leftovers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
