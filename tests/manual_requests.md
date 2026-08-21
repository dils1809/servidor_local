# Manual JSON-RPC requests

The server speaks JSON-RPC 2.0 over stdio, one message per line, so it can be
driven from a terminal with nothing but a keyboard. No MCP client is needed.

Everything below assumes you are in the repository root and that the database
exists:

```powershell
python data/seed.py
```

## Running a whole session at once

`demo_session.jsonl` holds a complete session: the lifecycle handshake, one
call to each tool, and every error path.

```powershell
Get-Content tests\demo_session.jsonl | python -m src
```

```bash
# macOS / Linux
python -m src < tests/demo_session.jsonl
```

The file contains **13 messages but you should count 11 responses**. The two
that go unanswered are notifications, which by definition are never replied to.

Add `--log-level DEBUG` to watch both directions on stderr while protocol
traffic flows on stdout:

```powershell
Get-Content tests\demo_session.jsonl | python -m src --log-level DEBUG
```

## Running one request at a time

Start the server and type or paste lines into it. Press `Ctrl+C` to stop.

```powershell
python -m src
```

A single request without an interactive session:

```powershell
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}' | python -m src
```

## The lifecycle

The handshake must come first. Only `initialize` and `ping` are accepted before
it completes; anything else is refused with -32600.

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"manual-demo","version":"1.0"}}}
```

The server replies with the negotiated protocol version, the capabilities it
actually implements, and its identity. The client then sends:

```json
{"jsonrpc":"2.0","method":"notifications/initialized"}
```

That message has no `id`, so **no response is produced**. The connection is now
in the ready state.

```json
{"jsonrpc":"2.0","id":2,"method":"ping"}
```

Ping carries no data in either direction; an empty result is the whole
contract.

## Tools

Discovery:

```json
{"jsonrpc":"2.0","id":3,"method":"tools/list"}
```

An order the caller is entitled to see:

```json
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_order_status","arguments":{"email":"ana.morales@example.com","order_number":"1009"}}}
```

The same order number with a different customer's email. Order `#1010` does
exist, but the answer is indistinguishable from one for an order that does not:

```json
{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"get_order_status","arguments":{"email":"ana.morales@example.com","order_number":"1010"}}}
```

Catalog search, including by ingredient:

```json
{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"search_products","arguments":{"query":"lavender"}}}
```

The only tool that writes:

```json
{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"create_support_ticket","arguments":{"email":"ana.morales@example.com","subject":"Package arrived damaged","description":"Order #1001 arrived with the box crushed."}}}
```

## Error paths

Two kinds of failure are answered differently, and the difference is
deliberate.

**Execution failures** come back as successful responses carrying
`isError: true`, because the model has to read them in order to help the
customer. Request id 5 above is one.

**Protocol errors** come back as JSON-RPC errors. The client never shows them
to the model; they mean the message itself was wrong.

| Request | Code | Meaning |
|---|---|---|
| `{"jsonrpc":"2.0","id":8,"method":"tools/call","params":{"name":"drop_all_orders","arguments":{}}}` | -32602 | Unknown tool |
| `{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"get_order_status","arguments":{"order_number":"1009"}}}` | -32602 | Required argument missing |
| `{"jsonrpc":"2.0","id":10,"method":"resources/list"}` | -32601 | Method not implemented yet |
| `{"jsonrpc":"2.0","id":11,"method":` | -32700 | Malformed JSON |
| `{"jsonrpc":"1.0","id":12,"method":"ping"}` | -32600 | Wrong protocol version |
| `{"jsonrpc":"2.0","id":13,"method":"ping","params":[1,2]}` | -32602 | Positional parameters |

A malformed message that carries no `id` is a notification, and no error is
returned for it either:

```json
{"jsonrpc":"2.0","method":"notifications/unknown","params":{"note":"never answered"}}
```

## Requests versus notifications

This distinction is the one to watch when capturing traffic.

| | Request | Notification |
|---|---|---|
| Has `id` | yes | no |
| Expects a response | yes, exactly one | never |
| On error | error response | silently dropped |
| Examples | `initialize`, `ping`, `tools/call` | `notifications/initialized` |

Every response echoes the `id` of the request that caused it, which is what
lets a client match replies to calls when several are in flight. The one
exception is a parse error: the id cannot be recovered from text that is not
valid JSON, so the response carries `"id": null`.
