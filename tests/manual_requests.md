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
call to each tool, both resource methods, both prompt methods, and every
error path.

```powershell
Get-Content tests\demo_session.jsonl | python -m src
```

```bash
# macOS / Linux
python -m src < tests/demo_session.jsonl
```

The file contains **19 messages but you should count 17 responses**. The two
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

## Resources

Resources are documents the model reads; it cannot write them and cannot
choose an arbitrary one. Discovery first:

```json
{"jsonrpc":"2.0","id":8,"method":"resources/list"}
```

Reading one returns the markdown as plain text:

```json
{"jsonrpc":"2.0","id":9,"method":"resources/read","params":{"uri":"vibbo://policies/returns"}}
```

The four URIs are `vibbo://policies/shipping`, `vibbo://policies/returns`,
`vibbo://policies/support-faq` and `vibbo://catalog/brewing`.

A URI is never turned into a file path. The server holds a fixed table of
URI to filename, so a traversal attempt is not a path that gets normalised
badly; it is simply a key that is not in the table:

```json
{"jsonrpc":"2.0","id":15,"method":"resources/read","params":{"uri":"vibbo://policies/../../secrets"}}
```

## Prompts

A prompt is a template the user invokes, not something the model calls on its
own. The host fills the arguments and gets back the messages to open the
conversation with.

```json
{"jsonrpc":"2.0","id":10,"method":"prompts/list"}
```

```json
{"jsonrpc":"2.0","id":11,"method":"prompts/get","params":{"name":"support_triage","arguments":{"order_number":"#1009","purchase_channel":"online store","issue_description":"The box arrived crushed and I want a replacement.","evidence":"photo of the box"}}}
```

The reply is two messages: an `assistant` turn with the triage procedure, and
a `user` turn holding the case report. The customer's own words are placed
between `-----BEGIN CUSTOMER TEXT-----` and `-----END CUSTOMER TEXT-----`, and
the assistant turn states that everything inside the fence is quoted material
rather than instructions. Try it with an injection attempt as the
`issue_description` and read the output: the text is passed through
unmodified, but its status as data is unambiguous.

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
| `{"jsonrpc":"2.0","id":12,"method":"tools/call","params":{"name":"drop_all_orders","arguments":{}}}` | -32602 | Unknown tool |
| `{"jsonrpc":"2.0","id":13,"method":"tools/call","params":{"name":"get_order_status","arguments":{"order_number":"1009"}}}` | -32602 | Required argument missing |
| `{"jsonrpc":"2.0","id":14,"method":"tools/call","params":{"name":"get_order_status","arguments":{"email":"ana.morales@example.com","order_no":"1009"}}}` | -32602 | Undeclared argument |
| `{"jsonrpc":"2.0","id":15,"method":"resources/read","params":{"uri":"vibbo://policies/../../secrets"}}` | -32602 | Unknown resource URI |
| `{"jsonrpc":"2.0","id":16,"method":"admin/shutdown"}` | -32601 | No such method |
| `{"jsonrpc":"2.0","id":17,"method":` | -32700 | Malformed JSON |
| `{"jsonrpc":"1.0","id":18,"method":"ping"}` | -32600 | Wrong protocol version |
| `{"jsonrpc":"2.0","id":19,"method":"ping","params":[1,2]}` | -32602 | Positional parameters |

Note the difference between rows two and three. A missing `email` and a
misspelled `order_no` are both caught, and neither is allowed to reach the
database as a lookup that quietly returns nothing. Without the second check
a client that sends `order_no` would get "no order matches", which points the
user at the wrong problem.

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
| Examples | `initialize`, `ping`, `tools/call`, `resources/read`, `prompts/get` | `notifications/initialized` |

Every response echoes the `id` of the request that caused it, which is what
lets a client match replies to calls when several are in flight. The one
exception is a parse error: the id cannot be recovered from text that is not
valid JSON, so the response carries `"id": null`.
