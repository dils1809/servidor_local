"""Generate a clean, numbered MCP session for a Wireshark capture.

The problem with capturing the chatbot directly is that the model decides how
many calls to make and when, so no two captures look alike and nothing lines
up with anything. This script sends a fixed sequence instead, one message at a
time with a pause between each, and prints a manifest saying what every
message was: request or notification, which method, which id, and what HTTP
status it should have come back with.

That manifest is the answer key. Open it beside Wireshark and every packet has
a name.

How to use it:

    1. $env:SSLKEYLOGFILE = "$PWD\\logs\\tls-keys.log"
    2. python tests/capture_session.py
    3. it wakes the server, then waits -- start the Wireshark capture now
    4. press Enter; the sequence runs with pauses
    5. stop the capture

Wireshark then needs the key log to decrypt: Edit -> Preferences ->
Protocols -> TLS -> (Pre)-Master-Secret log filename.
"""

from __future__ import annotations

import os
import socket
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_URL = "https://vibbo-mcp.onrender.com"
LOG_DIR = REPO_ROOT / "logs"

# Long enough that packets for different messages do not interleave in the
# capture, short enough that the whole run stays under a minute.
PAUSE_SECONDS = 2.0

# (label, method, params, expected kind, expected HTTP status)
#
# The sequence is chosen so the capture contains every message type the
# assignment asks about, in an order that tells a story: the handshake, then
# normal work, then a rejection, then teardown.
SEQUENCE: list[tuple[str, str, dict | None, str, int]] = [
    (
        "Lifecycle: version and capability negotiation",
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "capture-client", "version": "1.0.0"},
        },
        "request",
        200,
    ),
    (
        "Lifecycle: client confirms it is ready",
        "notifications/initialized",
        None,
        "notification",
        202,
    ),
    ("Liveness check, empty result", "ping", {}, "request", 200),
    ("Discovery: what tools exist", "tools/list", {}, "request", 200),
    (
        "Work: look up an order",
        "tools/call",
        {
            "name": "get_order_status",
            "arguments": {
                "email": "ana.morales@example.com",
                "order_number": "1009",
            },
        },
        "request",
        200,
    ),
    ("Discovery: what documents exist", "resources/list", {}, "request", 200),
    (
        "Work: read a document",
        "resources/read",
        {"uri": "vibbo://policies/returns"},
        "request",
        200,
    ),
    ("Discovery: what prompts exist", "prompts/list", {}, "request", 200),
    (
        "Rejected: no such tool (JSON-RPC error -32602)",
        "tools/call",
        {"name": "drop_all_orders", "arguments": {}},
        "request",
        200,
    ),
    (
        "Rejected: no such method (JSON-RPC error -32601)",
        "admin/shutdown",
        {},
        "request",
        200,
    ),
]


def main() -> int:
    from chatbot import ui
    from chatbot.http_client import HttpMCPClient
    from chatbot.logbook import Logbook
    from src import jsonrpc

    ui.enable()

    base = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    endpoint = base.rstrip("/") + "/mcp"
    host = urllib.parse.urlparse(base).hostname or ""

    keylog = os.environ.get("SSLKEYLOGFILE")
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    ui.heading("target")
    print(f"  url        {endpoint}")
    print(f"  host       {host}")
    addresses = _resolve(host)
    print(f"  addresses  {', '.join(addresses) or 'could not resolve'}")
    print(f"  key log    {keylog or 'NOT SET -- the capture will stay encrypted'}")
    if not keylog:
        print()
        ui.error("SSLKEYLOGFILE is not set. Set it and run again:")
        ui.note('  $env:SSLKEYLOGFILE = "$PWD\\logs\\tls-keys.log"')
        return 1
    print()

    # Waking the server first keeps the cold start, which can take 50 seconds
    # and hundreds of packets, out of the capture.
    ui.heading("waking the server")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/", timeout=120) as response:
            response.read()
        ui.note(f"  awake after {time.monotonic() - started:.1f}s")
    except OSError as exc:
        ui.error(f"could not reach the server: {exc}")
        return 1
    print()

    ui.heading("start the capture now")
    print("  In Wireshark: capture on your active interface with this filter,")
    print()
    for address in addresses or ["<address>"]:
        print(f"      host {address} and tcp port 443")
    print()
    print("  Then come back here.")
    print()
    try:
        input(ui.paint("  press Enter to send the sequence › ", ui.BOLD, ui.WHITE))
    except (EOFError, KeyboardInterrupt):
        print()
        return 1

    logbook = Logbook(LOG_DIR / "capture.jsonl")
    client = HttpMCPClient("remote", base, logbook=logbook)

    rows: list[tuple[int, str, str, str, object, str]] = []

    ui.heading("sending")
    for index, (label, method, params, kind, expected_status) in enumerate(
        SEQUENCE, start=1
    ):
        sent_at = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        if kind == "notification":
            client.notify(method, params)
            outcome = f"{expected_status}, empty body"
            message_id: object = "-"
        else:
            message_id = client._next_id + 1  # noqa: SLF001 - for the manifest
            try:
                client.request(method, params)
                outcome = "200, result"
            except jsonrpc.RemoteError as exc:
                outcome = f"200, error {exc.code}"
            except Exception as exc:  # noqa: BLE001
                outcome = f"failed: {exc}"

        rows.append((index, sent_at, kind, method, message_id, outcome))
        print(
            f"  {index:>2}. {sent_at}  "
            + ui.paint(f"{kind:<13}", ui.AMBER if kind == "notification" else ui.TEAL)
            + ui.paint(f"{method:<28}", ui.WHITE)
            + ui.paint(outcome, ui.GREY)
        )
        time.sleep(PAUSE_SECONDS)

    ui.heading("ending the session")
    client.stop()
    print("  DELETE /mcp sent")
    print()

    manifest = _write_manifest(endpoint, host, addresses, keylog, rows)
    ui.heading("done")
    ui.note(f"  stop the Wireshark capture now")
    ui.note(f"  manifest:  {manifest}")
    ui.note(f"  raw log:   {logbook.path}")
    ui.note(f"  tls keys:  {keylog}")
    print()
    return 0


def _resolve(host: str) -> list[str]:
    """Every address the host resolves to, for the capture filter."""
    if not host:
        return []
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return []
    return sorted({info[4][0] for info in infos})


def _write_manifest(
    endpoint: str,
    host: str,
    addresses: list[str],
    keylog: str,
    rows: list[tuple[int, str, str, str, object, str]],
) -> Path:
    """Write the answer key for the capture."""
    path = LOG_DIR / "capture-manifest.md"
    requests = sum(1 for row in rows if row[2] == "request")
    notifications = sum(1 for row in rows if row[2] == "notification")
    filters = [f"host {address} and tcp port 443" for address in addresses] or [
        "host <address> and tcp port 443"
    ]

    lines = [
        "# Capture manifest",
        "",
        f"Captured at {datetime.now().isoformat(timespec='seconds')}.",
        "",
        "| | |",
        "| --- | --- |",
        f"| Endpoint | `{endpoint}` |",
        f"| Host | `{host}` |",
        f"| Addresses | {', '.join(f'`{a}`' for a in addresses) or 'unresolved'} |",
        f"| TLS key log | `{keylog}` |",
        f"| Messages sent | {len(rows)} ({requests} requests, {notifications} notifications) |",
        f"| Responses expected | {requests} |",
        "",
        "## Wireshark setup",
        "",
        "Capture filter:",
        "",
        "```",
        *filters,
        "```",
        "",
        "Decryption: **Edit → Preferences → Protocols → TLS → (Pre)-Master-Secret",
        f"log filename** = `{keylog}`",
        "",
        "Display filters, once decryption works:",
        "",
        "```",
        "http                 all decrypted HTTP",
        'http.request.method == "POST"     every MCP message sent',
        "http.response.code == 202        notifications only",
        "http.response.code == 200        requests that got an answer",
        'http contains "jsonrpc"          the JSON-RPC payloads',
        "```",
        "",
        "## The messages, in order",
        "",
        "| # | Time sent | JSON-RPC kind | Method | id | Response |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, sent_at, kind, method, message_id, outcome in rows:
        lines.append(
            f"| {index} | {sent_at} | {kind} | `{method}` | {message_id} | {outcome} |"
        )

    lines += [
        "",
        "## How to classify what you see",
        "",
        "The assignment asks which JSON-RPC messages are synchronisation, which",
        "are requests, and which are responses. In this capture:",
        "",
        "- **Synchronisation** — the lifecycle messages. `initialize` (a request,",
        "  because the client needs the negotiated version back) and",
        "  `notifications/initialized` (a notification, because there is nothing",
        "  to negotiate). Together they are the handshake, and nothing else is",
        "  served until they complete.",
        "- **Requests** — every `POST /mcp` whose body carries an `id`. Each one",
        "  gets exactly one response. Here: everything except message 2.",
        "- **Responses** — the `200 OK` bodies, each echoing the `id` of the",
        "  request that caused it. A response carries either `result` or `error`,",
        "  never both.",
        "- **Notifications** — a body with no `id`. Message 2 is the only one.",
        "  It is answered with `202 Accepted` and an empty body, so it is",
        "  distinguishable from a request in the capture without opening the",
        "  payload at all.",
        "",
        "Two messages come back as `200 OK` carrying a JSON-RPC `error` rather",
        "than a `result` (numbers 9 and 10). That is deliberate: the HTTP",
        "request succeeded, so HTTP reports success; the JSON-RPC layer inside",
        "reports the failure. HTTP status and JSON-RPC status are different",
        "layers and they disagree on purpose.",
        "",
        "## What is not in this capture",
        "",
        "The three local servers (`vibbo`, `filesystem`, `git`) talk over stdio,",
        "which is an operating-system pipe. There are no packets to capture: no",
        "link layer, no network layer, no transport layer. The same MCP messages",
        "flow, framed one per line instead of one per request body, but they",
        "never touch the network stack. That contrast is the point — the",
        "protocol is identical, the layers underneath it are not.",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


if __name__ == "__main__":
    sys.exit(main())
