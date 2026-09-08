# VIBBO MCP Chatbot

A terminal chatbot that hosts several [Model Context
Protocol](https://modelcontextprotocol.io) servers and talks to Claude through
the Anthropic API.

The project has two halves:

- **A custom MCP server** (`src/`) for [VIBBO](https://drinkvibbo.com), a real
  online functional tea shop. It answers customer support questions: order
  status, catalog search, support tickets, and the shop's policies.
- **The chatbot that consumes it** (`chatbot/`), which is the *host* in MCP
  terms. It runs three servers at once — the VIBBO one plus Anthropic's
  official Filesystem and Git servers — and lets the model use all of their
  tools in one conversation.

Both halves implement JSON-RPC 2.0 and the MCP protocol **by hand**. No MCP
SDK, no FastMCP, no protocol library: framing, parsing, validation, the
lifecycle state machine and the session handling are written out in this
repository, for the server *and* the client.

```
        ┌─────────────────────────────────────────┐
        │  chatbot/  (host)                       │
        │                                         │
        │   Claude  ←→  session  ←→  logbook      │
        │                  │                      │
        │      ┌───────────┼───────────┐          │
        │   client 1    client 2    client 3      │
        └──────┼────────────┼────────────┼────────┘
               │ stdio      │ stdio      │ HTTP
          ┌────▼────┐  ┌────▼────┐  ┌────▼─────────┐
          │  vibbo  │  │ fs, git │  │ vibbo remote │
          │ (src/)  │  │official │  │  Cloud Run   │
          └─────────┘  └─────────┘  └──────────────┘
```

Built for CC3067 Redes, Universidad del Valle de Guatemala.

---

## Table of contents

- [The use case](#the-use-case)
- [Features](#features)
- [What the server exposes](#what-the-server-exposes)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running the chatbot](#running-the-chatbot)
- [Running the server on its own](#running-the-server-on-its-own)
- [Using the server from another host](#using-the-server-from-another-host)
- [Running the server remotely](#running-the-server-remotely)
- [Tool reference](#tool-reference)
- [Resource reference](#resource-reference)
- [Prompt reference](#prompt-reference)
- [Protocol details](#protocol-details)
- [Architecture](#architecture)
- [Security design](#security-design)
- [About the data](#about-the-data)
- [Repository layout](#repository-layout)

---

## The use case

VIBBO sells loose-leaf functional teas online through Shopify. Its support
inbox gets the same three questions over and over: *where is my order*, *what
is in this blend*, and *can I return it*. Each one currently costs a human
agent a few minutes of copying an order number into an admin panel.

This server lets an AI assistant answer those questions directly, and hand
over to a human when the question actually needs one. It exposes three tools,
four reference documents and one escalation template.

The interesting part is not that a chatbot can look up an order. It is doing
that **without handing the model data it has no business seeing**. The design
notes for that are in [Security design](#security-design).

---

## Features

What is implemented, and where to look for it.

| Feature | Where |
| --- | --- |
| Chat with Claude through the Anthropic API | `chatbot/session.py` |
| Conversation context kept across turns | `chatbot/session.py`, the `_messages` list |
| Log of every MCP request, response and notification | `chatbot/logbook.py`, shown by `/log` |
| Hand-written MCP client over stdio | `chatbot/mcp_client.py` |
| Hand-written MCP client over HTTP | `chatbot/http_client.py` |
| Several servers hosted at once, tools namespaced | `chatbot/host.py` |
| Anthropic's official Filesystem and Git servers | `chatbot/servers.json` |
| A custom MCP server for a real business case | `src/` |
| The same server over stdio and over HTTP | `src/transport.py`, `src/http_transport.py` |
| Colour-coded terminal UI with slash commands | `chatbot/ui.py` |

---

## What the server exposes

**Tools** — actions the model can invoke.

| Tool | Writes? | Purpose |
| --- | --- | --- |
| `get_order_status` | no | Delivery status of one order, given email **and** order number |
| `search_products` | no | Search the catalog by name, type, description or ingredient |
| `create_support_ticket` | **yes** | Open a ticket for a human agent |

**Resources** — documents the model can read.

| URI | Contents |
| --- | --- |
| `vibbo://policies/shipping` | Processing and delivery times, carriers, customs, lost packages |
| `vibbo://policies/returns` | 30-day window, condition rules, refund timing |
| `vibbo://policies/support-faq` | Approved answers, and what the assistant must never say |
| `vibbo://catalog/brewing` | Steeping guide, ingredients, allergens, storage |

**Prompts** — templates the user invokes.

| Prompt | Purpose |
| --- | --- |
| `support_triage` | Collect order, purchase channel, problem and evidence before escalating |

---

## Requirements

What you need depends on which half you want to run.

**The MCP server on its own — nothing but Python.**

- **Python 3.11 or newer.** Developed on 3.13.2.
- **Zero third-party packages.** The protocol is implemented by hand, so
  `python -m src` works on a bare Python install. Standard library only:
  `json`, `sqlite3`, `logging`, `argparse`, `pathlib`, `threading`,
  `http.server`.

**The chatbot — two packages and an API key.**

- `anthropic` — the LLM client. Not an MCP library; the MCP protocol is still
  hand-written in `chatbot/mcp_client.py` and `chatbot/http_client.py`.
- `mcp-server-git` — Anthropic's official Git MCP server, which the chatbot
  consumes as one of its three servers.
- **An Anthropic API key.** Free credit is available at
  [console.anthropic.com](https://console.anthropic.com).
- **Node.js 18 or newer**, for Anthropic's official Filesystem server. `npx`
  fetches it on first use, so there is nothing to install by hand. Developed
  against Node v22.

Any OS. The transport layer handles Windows line endings and BOM injection
explicitly, so PowerShell works the same as bash.

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/dils1809/servidor_local.git
cd servidor_local
```

### 2. Create a virtual environment

Not strictly required since there are no dependencies, but recommended.

```powershell
# Windows / PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install the chatbot's dependencies

Skip this if you only want the MCP server; it needs nothing.

```bash
pip install -r requirements.txt
```

### 4. Set the API key

Get one at [console.anthropic.com](https://console.anthropic.com). It is read
from the `ANTHROPIC_API_KEY` environment variable.

```powershell
# Windows / PowerShell -- persists across reboots
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")
```

```bash
# macOS / Linux
export ANTHROPIC_API_KEY="sk-ant-..."
```

A program only sees variables that existed when it started, so open a **new**
terminal after setting it. On Windows the chatbot also reads the persisted
user variable directly, so a stale terminal still works.

A `.env` file in the repository root is read as a fallback:

```
ANTHROPIC_API_KEY=sk-ant-...
```

`.env` is in `.gitignore`. Never commit a key: GitHub detects them and
Anthropic revokes them automatically.

### 5. Build the database

The database is **not** committed. It is generated from a seed script, so
everyone who clones the repo gets byte-identical data.

```bash
python data/seed.py
```

This creates `data/vibbo.db` with 6 products, 8 customers, 25 orders and 5
support tickets. The script is deterministic: a fixed random seed and a fixed
reference date mean re-running it always produces the same rows. Any existing
database at the target path is dropped and rebuilt.

Useful flags:

```bash
python data/seed.py --help                      # all options
python data/seed.py --database other.db         # write somewhere else
python data/seed.py --reference-date 2026-08-20 # anchor day for all timestamps
```

To point the server at a database in another location, set the `VIBBO_DB_PATH`
environment variable.

### 6. Verify the install

```bash
python -m src < tests/demo_session.jsonl
```

You should see 17 JSON responses. If you see them, the install is complete.

---

## Running the chatbot

```bash
python -m chatbot
```

It starts the three MCP servers, runs the handshake with each, and prints
what connected:

```
  VIBBO  MCP chatbot
  Model Context Protocol host · CC3067 Redes

  ● vibbo        vibbo-mcp-server 0.1.0        MCP 2025-11-25  ·  prompts, resources, tools
  ● filesystem   secure-filesystem-server 0.2.0 MCP 2025-11-25  ·  tools
  ● git          mcp-git 1.29.1                MCP 2025-11-25  ·  experimental, tools

  30 tools available.  /help for commands, /quit to exit.
```

Then you talk to it in plain language, in any language. The model decides on
its own when a tool is needed.

```
you › I'm ana.morales@example.com, where is my order 1009?
  ⚙ vibbo · get_order_status(email=ana.morales@example.com, order_number=1009)
    ✓ {"status": "In transit", "carrier": "USPS", ...

  claude Order #1009 is in transit with USPS.
         Tracking: 9400366674684573766792
         Estimated delivery: 2026-08-25
```

Questions that need no tool are answered from the model's own knowledge, and
the conversation keeps its context: ask *"who was Alan Turing?"* and then
*"what year was he born?"* and the second question resolves against the first.

### Commands

Lines starting with `/` are handled by the chatbot itself and never reach the
model.

| Command | What it does |
| --- | --- |
| `/help` | list the commands |
| `/servers` | connected servers, their versions and capabilities |
| `/tools` | every tool the model can call, by server |
| `/resources` | documents the servers publish |
| `/prompts` | templates the servers publish |
| `/use <server> <prompt>` | fill a prompt's arguments and load it into the conversation |
| `/read <server> <uri>` | read a resource into the conversation |
| `/log [n]` | the last n MCP messages, with their kind and id |
| `/save` | where the session log is being written |
| `/clear` | forget the conversation, keep the servers connected |
| `/cost` | tokens and dollars used this session |
| `/quit` | exit |

### Options

```
python -m chatbot --model claude-opus-5 --log-level WARNING --no-color
```

### The MCP log

Every message to and from every server is recorded, in both directions, with
its kind and its id. `/log` shows it live:

```
   1. --> vibbo        id=1 initialize
   2. <-- vibbo        id=1 initialized, capabilities: prompts, resources, tools
   3. --> vibbo        notifications/initialized
   4. --> vibbo        id=2 tools/list
   5. <-- vibbo        id=2 3 tools
   6. --> vibbo        id=3 tools/call
   7. <-- vibbo        id=3 tool result

request: 3  response: 3  notification: 1  total: 7
```

The full messages are appended to `logs/session-<timestamp>.jsonl`, one JSON
object per line, holding the raw payload. That file is byte-for-byte what went
over the wire, so a Wireshark capture can be lined up against it.

`logs/` is in `.gitignore`: session logs contain customer data from the
queries you ran.

### Configuring the servers

`chatbot/servers.json` lists what to connect to. An entry has either a
`command` to launch or a `url` to call, and that alone decides which transport
the host uses.

```json
{
  "servers": [
    { "name": "vibbo", "command": ["python", "-m", "src"], "enabled": true },
    { "name": "filesystem",
      "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "{workspace}"],
      "enabled": true },
    { "name": "vibbo-remote", "url": "https://your-service.run.app", "enabled": true }
  ]
}
```

`{workspace}` is replaced with the repository root. Set `"enabled": false` to
skip a server without deleting the entry. A server that fails to start is
reported and the session continues with the rest.

Tools are exposed to the model as `server__tool`, because two servers can
easily both offer `search` and the model has to be able to name one of them.

### Demonstrating the official servers

`tests/demo_official_servers.py` runs the scenario the assignment describes:
the chatbot writes a README into a repository, stages it, commits it and reads
the log back, using only Anthropic's official Filesystem and Git servers.

```bash
python tests/demo_official_servers.py
```

It works in a temporary directory, so nothing in this repository is touched.
The request is given to the model in one plain-language message; the model
chooses the tools itself. Afterwards the script checks the repository on disk
with plain `git` commands, so the verification does not depend on what the
model claimed it did:

```
  independent verification
  README.md exists          yes
  commits in the repository 1
    9c503d2 docs: add README created over MCP
  tracked files             README.md
```

**One caveat, worth knowing.** The official Git server exposes twelve tools
and none of them is `git_init`; they all operate on a repository that already
exists, and the server is launched with `--repository` pointing at one. So the
demo script creates the directory and runs `git init` itself before handing
the repository to the chatbot. Writing the file, staging, committing and
reading the log are all done through MCP.

---

## Running the server on its own

An MCP server has no user interface. It reads JSON-RPC messages from standard
input and writes responses to standard output, one message per line. It is
meant to be launched by a host application, not stared at.

```bash
python -m src
```

The process waits for input. Paste a line and press Enter to see a response.
`Ctrl+C` stops it.

### Options

```
python -m src --log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
```

Logs always go to **stderr**, never stdout. Stdout carries protocol traffic
only, so redirecting it gives you a clean transcript:

```bash
python -m src --log-level DEBUG < tests/demo_session.jsonl > protocol.txt 2> server.log
```

On Windows, run that through `cmd /c` rather than PowerShell directly, because
PowerShell 5.1 wraps a native program's stderr in its own error records and
pollutes the log file:

```powershell
cmd /c "type tests\demo_session.jsonl | python -m src --log-level DEBUG 2> server.log > protocol.txt"
```

### Trying it by hand

`tests/manual_requests.md` walks through every method with a copy-pasteable
request for each, including all the error paths. Start there.

---

## Using the server from another host

### Claude Code

The repository already contains `.mcp.json`, so Claude Code discovers the
server automatically when you open this folder:

```json
{
  "mcpServers": {
    "vibbo": {
      "command": "python",
      "args": ["-m", "src"]
    }
  }
}
```

Start Claude Code in the repository root and approve the server when asked.
`/mcp` lists it and confirms the connection.

### Claude Desktop

Edit the config file:

- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "vibbo": {
      "command": "python",
      "args": ["-m", "src"],
      "cwd": "C:/path/to/servidor_local"
    }
  }
}
```

Use an absolute path in `cwd`. Claude Desktop reads this file only at startup,
so quit it completely and reopen it after editing.

### Any other host

The server is a plain stdio subprocess. Any MCP client can launch
`python -m src` with the repository root as the working directory.

---

## Running the server remotely

The same server runs over HTTP with one flag:

```bash
python -m src --http --port 8080
```

Nothing in `src/handlers/`, `src/db.py`, `src/mcp_server.py` or
`src/jsonrpc.py` changes. Only the framing does: one JSON message per request
body instead of one per line.

| | stdio | HTTP |
| --- | --- | --- |
| Framing | one message per line | one message per request body |
| Who starts it | the client, as a subprocess | it is already running |
| Session | one process is one client | `Mcp-Session-Id` header |
| Notifications | written, never answered | `202 Accepted`, empty body |

Endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` or `/health` | liveness, and the open session count |
| `POST` | `/mcp` | one JSON-RPC message per request |
| `DELETE` | `/mcp` | end the session named by `Mcp-Session-Id` |

A `POST /mcp` carrying `initialize` opens a session and the response returns
its id in the `Mcp-Session-Id` header. Every later request must echo that
header, because HTTP is stateless and the MCP lifecycle is not.

Try it by hand:

```bash
# initialize -- read the Mcp-Session-Id out of the response headers
curl -i -X POST http://localhost:8080/mcp   -H "Content-Type: application/json"   -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'

# then use it
curl -X POST http://localhost:8080/mcp   -H "Content-Type: application/json"   -H "Mcp-Session-Id: THE-ID"   -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

**[DEPLOYMENT.md](DEPLOYMENT.md)** covers deploying this remotely — Render
(no payment method required) and Google Cloud Run — pointing the chatbot at
the deployed URL, and capturing the encrypted traffic for protocol analysis.
`render.yaml` and `Dockerfile` make the deployment reproducible from the
repository.

---

## Tool reference

### `get_order_status`

Looks up one order. **Both** the email address and the order number are
required; neither works alone.

**Input**

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `email` | string | yes | The address used at checkout. Matched case-insensitively. |
| `order_number` | string | yes | With or without `#`. `1009` and `#1009` are the same order. |

Undeclared fields are rejected with `-32602`.

**Output** — exactly these five fields, and nothing else:

```json
{
  "status": "In transit",
  "carrier": "USPS",
  "tracking_number": "9400366674684573766792",
  "estimated_delivery": "2026-08-25",
  "items": [
    {"title": "All Day Bundle Pack", "variant_title": "Default Title", "quantity": 1},
    {"title": "Reset Bundle Assortment Box", "variant_title": "Default Title", "quantity": 2},
    {"title": "Detox Functional Tea", "variant_title": "Default Title", "quantity": 2}
  ]
}
```

`carrier`, `tracking_number` and `estimated_delivery` are `null` for an order
that has not shipped yet.

**Example call**

```json
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_order_status","arguments":{"email":"ana.morales@example.com","order_number":"1009"}}}
```

**When it fails.** A wrong email, a wrong number, or a mismatched pair all
return the same message inside a successful response with `isError: true`:

> No order matches that order number and email address together. Check that
> both are exactly as they appear in the confirmation email.

The wording is identical in every case on purpose. See
[Security design](#security-design).

---

### `search_products`

Searches the catalog by title, product type, description or ingredient.
Returns at most 10 results.

**Input**

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `query` | string | yes | 1 to 100 characters. `%` and `_` are escaped, not treated as wildcards. |

**Output**

```json
{
  "query": "lavender",
  "count": 3,
  "currency": "USD",
  "products": [
    {
      "title": "Calm Functional Tea",
      "product_type": "Functional tea",
      "price": 25.0,
      "description": "Packed with refreshing botanicals...",
      "variants": [
        {
          "title": "Default Title",
          "sku": "VBB-CHILL-001",
          "inventory_quantity": 61,
          "in_stock": true
        }
      ]
    }
  ]
}
```

Every VIBBO product is sold as a single variant, which Shopify names
`Default Title`, so each product carries exactly one entry in `variants`. The
nesting is kept anyway because that is the shape a real Shopify catalog has.
`in_stock` is `false` when `inventory_quantity` is `0`.

The example query matches three products, not one: the ingredient appears in
the Calm blend and in both bundles that contain it.

**Example call**

```json
{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"search_products","arguments":{"query":"lavender"}}}
```

---

### `create_support_ticket`

The only tool that writes to the database.

**Input**

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `email` | string | yes | Contact address for the reply. |
| `subject` | string | yes | Up to 200 characters. |
| `description` | string | yes | Up to 5000 characters. Stored verbatim. |

**Output**

```json
{"ticket_id": 6, "status": "open", "created_at": "2026-09-06T01:42:42-06:00"}
```

`created_at` is an ISO 8601 timestamp with the server's UTC offset.
`ticket_id` counts on from whatever the seed already created.

**Example call**

```json
{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"create_support_ticket","arguments":{"email":"ana.morales@example.com","subject":"Box arrived crushed","description":"The outer box was crushed on delivery."}}}
```

The description is stored exactly as written and never interpreted as an
instruction, no matter what it contains.

---

## Resource reference

List them:

```json
{"jsonrpc":"2.0","id":8,"method":"resources/list"}
```

Read one:

```json
{"jsonrpc":"2.0","id":9,"method":"resources/read","params":{"uri":"vibbo://policies/returns"}}
```

The response carries the document as `text/markdown` in plain text:

```json
{"contents":[{"uri":"vibbo://policies/returns","name":"returns_policy","mimeType":"text/markdown","text":"# Returns and Refunds Policy\n..."}]}
```

An unregistered URI returns `-32602`. The URI is never converted into a file
path — the server holds a fixed table mapping each URI to a filename, so a
traversal attempt such as `vibbo://policies/../../secrets` is not a path that
gets normalised badly; it is a key that is not in the table.

---

## Prompt reference

### `support_triage`

| Argument | Required | Notes |
| --- | --- | --- |
| `order_number` | yes | Or the literal `unknown`. |
| `purchase_channel` | yes | `online store`, `amazon`, `retail partner`, `event`, `other`. |
| `issue_description` | yes | The customer's own words. |
| `evidence` | no | Photos, screenshots, the confirmation email. |

**Example call**

```json
{"jsonrpc":"2.0","id":11,"method":"prompts/get","params":{"name":"support_triage","arguments":{"order_number":"#1009","purchase_channel":"online store","issue_description":"The box arrived crushed and I want a replacement.","evidence":"photo of the box"}}}
```

The reply is two messages: an `assistant` turn holding the four-step triage
procedure, and a `user` turn holding the case report. The customer's text sits
between `-----BEGIN CUSTOMER TEXT-----` and `-----END CUSTOMER TEXT-----`, and
the assistant turn states that everything inside the fence is quoted material
rather than instructions addressed to the model.

---

## Protocol details

**Specification:** MCP revision `2025-11-25`. The server also accepts
`2025-06-18` and `2025-03-26` during negotiation and echoes back the version
it agreed to.

**Transport:** stdio. **Framing:** NDJSON — one complete JSON message per
line, delimited by `\n`. There is no `Content-Length` header. Blank lines are
skipped; a UTF-8 BOM is stripped, because PowerShell inserts one when piping a
file into a process.

### Methods

| Method | Kind | Purpose |
| --- | --- | --- |
| `initialize` | request | Negotiate version and capabilities |
| `notifications/initialized` | notification | Client confirms it is ready |
| `ping` | request | Liveness check, empty result |
| `tools/list` | request | Discover tools |
| `tools/call` | request | Invoke a tool |
| `resources/list` | request | Discover resources |
| `resources/read` | request | Read a resource |
| `prompts/list` | request | Discover prompts |
| `prompts/get` | request | Render a prompt |

### Lifecycle

```
UNINITIALIZED ──initialize──> INITIALIZING ──notifications/initialized──> READY
```

Only `initialize` and `ping` are accepted before the handshake completes.
Anything else gets `-32600`.

### Requests versus notifications

|  | Request | Notification |
| --- | --- | --- |
| Has `id` | yes | no |
| Expects a response | exactly one | never |
| On error | error response | dropped silently |

Every response echoes the `id` of its request. The one exception is a parse
error: the id cannot be recovered from text that is not valid JSON, so the
response carries `"id": null`.

### Error codes

| Code | Name | Raised when |
| --- | --- | --- |
| `-32700` | Parse error | The line is not valid JSON |
| `-32600` | Invalid request | Not JSON-RPC 2.0, bad `id` type, batch array, or a call before the handshake |
| `-32601` | Method not found | No such method |
| `-32602` | Invalid params | Missing, mistyped, undeclared or out-of-range arguments; unknown tool, resource URI or prompt |
| `-32603` | Internal error | An unexpected exception; the server still answers |

Batching was removed from MCP in revision `2025-11-25`, so a JSON array is
rejected with `-32600`.

### Two kinds of failure

This distinction matters and is deliberate:

- **Protocol errors** are JSON-RPC error responses. They mean the *client*
  sent something malformed. The model never sees them.
- **Execution errors** are successful responses carrying `isError: true`.
  They mean the *request was valid but the answer is bad news* — no such
  order, nothing found. The model does see them, so it can tell the customer
  what to check.

---

## Architecture

Layers, top to bottom. Each one knows nothing about the layer above it:

```
src/handlers/        business logic (tools, resources, prompts)
src/mcp_server.py    MCP semantics: lifecycle, capabilities, routing
src/jsonrpc.py       JSON-RPC 2.0: parsing, validation, error objects
src/transport.py     framing and I/O (stdio today)
```

`src/db.py` sits beside the handlers and is the only module that writes SQL.

The split is not decoration. The same server has to run remotely over HTTP
later in the course, and `jsonrpc.py` knows nothing about MCP while
`mcp_server.py` knows nothing about stdio. Swapping the transport is a
one-line change in `src/__main__.py`; no business logic moves.

Capabilities are **derived**, not declared. `MCPServer` starts with an empty
capability set, and `register_feature()` adds one entry per feature actually
registered. The server cannot advertise something it does not implement,
because the advertisement and the routing table are populated by the same
call.

---

## Security design

Three rules are enforced at the data layer, in `src/db.py`, not in the
handlers. A handler can be rewritten carelessly; the schema is harder to get
wrong by accident.

**1. No lookup by order number alone.** `find_order()` requires the email as
well, and matches both in a single query. Without this, anyone could walk
`#1001`, `#1002`, `#1003` and read every order in the shop.

**2. The data the model never receives does not exist in the schema.** There
are no address, phone or payment columns anywhere in the database. This is
stronger than filtering them out in the handler: a field that was never stored
cannot leak through a future code change, a debug log, or an error message.
`get_order_status` declares an `outputSchema` listing exactly five fields, so
the boundary is machine-checkable rather than a convention.

**3. Failures are explicit and identical.** A non-existent order and a
mismatched email return the same message. Saying *"that order exists but the
email is wrong"* would confirm that the order number is real, which is exactly
the enumeration this design prevents. The response is never ambiguous enough
for the model to fill the gap by guessing.

**Prompt injection.** Customer text is stored and replayed verbatim, never
interpreted. `create_support_ticket` writes the description as given.
`support_triage` places it between explicit fences and tells the model in the
same payload that the fenced content is quoted material. The server does not
try to detect malicious text — that is a losing game — it makes the boundary
between instructions and data unambiguous.

**Input handling.** Every query is parameterized. `LIKE` wildcards in user
text are escaped so a search for `100%` does not become a wildcard. String
lengths are capped so one call cannot write an unbounded blob.

### A known limitation

The email address arrives from the model, which read it out of the chat. That
is fine for a course demo and wrong for production: anyone can claim to be any
customer. In a real deployment the email must come from an authenticated
session held by the host, and the tool should take a session token instead.
The server-side design does not change; the missing piece is on the host.

---

## About the data

The catalog is **real**: the six products, their titles, prices, descriptions
and SKUs are taken from the public storefront at drinkvibbo.com. Products the
store lists as sold out have an inventory of zero here too.

Everything else is **synthetic**. Customers, orders, tracking numbers and
tickets are generated by `data/seed.py` and correspond to no real person or
purchase. The exact on-hand unit count is the one catalog field that is
invented, since the storefront publishes only in-stock or sold-out.

Tracking numbers are shaped like real ones — UPS `1Z` plus 16 digits, FedEx 12
digits, USPS `9400` plus 18 — so that a host displaying them looks realistic.
None of them resolve to a real shipment.

Order dates are derived backwards from the delivery estimate rather than
spread at random, so an order that is in transit can never carry a delivery
date in the past.

---

## Repository layout

```
servidor_local/
├── src/                     THE MCP SERVER -- zero dependencies
│   ├── __main__.py          entry point, picks the transport
│   ├── jsonrpc.py           JSON-RPC 2.0, both halves, no MCP knowledge
│   ├── mcp_server.py        lifecycle, capabilities, routing
│   ├── transport.py         NDJSON framing over stdio
│   ├── http_transport.py    Streamable HTTP framing and sessions
│   ├── db.py                the only module that writes SQL
│   └── handlers/
│       ├── tools.py         tools/list, tools/call
│       ├── resources.py     resources/list, resources/read
│       └── prompts.py       prompts/list, prompts/get
├── chatbot/                 THE HOST -- the chatbot that consumes servers
│   ├── __main__.py          the read-eval-print loop and slash commands
│   ├── mcp_client.py        hand-written MCP client over stdio
│   ├── http_client.py       hand-written MCP client over HTTP
│   ├── host.py              several clients, one namespaced tool list
│   ├── session.py           conversation context and the tool loop
│   ├── logbook.py           the record of every MCP message
│   ├── credentials.py       finding the API key
│   ├── ui.py                colour, layout, the terminal presentation
│   └── servers.json         which servers to connect to
├── data/
│   ├── policies/            markdown served as MCP resources
│   │   ├── shipping.md
│   │   ├── returns.md
│   │   ├── support-faq.md
│   │   └── brewing.md
│   ├── schema.sql           tables, constraints, indexes
│   └── seed.py              deterministic database builder
├── tests/
│   ├── demo_session.jsonl   a full session, 19 messages in, 17 out
│   └── manual_requests.md   every method, by hand, with error paths
├── logs/                    session logs, gitignored
├── Dockerfile               the image Cloud Run runs
├── DEPLOYMENT.md            deploying remotely, and capturing the traffic
├── .mcp.json                Claude Code server discovery
└── requirements.txt         nothing for the server, two for the chatbot
```

---

## Notes for the course

**The protocol is implemented by hand.** `src/jsonrpc.py` holds both halves of
JSON-RPC 2.0 — the server parses requests and builds responses, the client
does the mirror image — so there is one definition of the wire format used
from both ends. `src/mcp_server.py` holds the lifecycle state machine and the
routing; `chatbot/mcp_client.py` and `chatbot/http_client.py` hold the client
side of the same handshake. No MCP SDK is imported anywhere in `src/` or
`chatbot/`.

The one place the MCP SDK exists in this project is as a transitive dependency
of `mcp-server-git`, which is **Anthropic's official Git server** and one of
the servers the assignment asks the chatbot to consume. That SDK is used by
Anthropic's code, never by this project's.

**Requests versus notifications** is the distinction to watch when capturing
traffic. Over stdio, a notification is written and never answered. Over HTTP
it comes back as `202 Accepted` with an empty body, while a request comes back
as `200 OK` with a JSON body — so the two are distinguishable at the HTTP
layer without opening the payload. `tests/demo_session.jsonl` sends 19
messages and produces 17 responses; the two missing replies are the two
notifications.

**Where to look for each thing:**

| Assignment item | Where |
| --- | --- |
| LLM over its API | `chatbot/session.py` |
| Session context | `chatbot/session.py`, the `_messages` list |
| MCP interaction log | `chatbot/logbook.py`, `/log`, `logs/*.jsonl` |
| Official Filesystem and Git servers | `chatbot/servers.json` |
| Custom local MCP server | `src/`, documented above |
| The same server, remotely | `src/http_transport.py`, [DEPLOYMENT.md](DEPLOYMENT.md) |
| Protocol specification | [Protocol details](#protocol-details) |
| Traffic capture setup | [DEPLOYMENT.md](DEPLOYMENT.md) |

---

## License

Coursework for CC3067 Redes, Universidad del Valle de Guatemala.
VIBBO product names and descriptions belong to their owner and are used here
to ground an academic exercise in a real catalog.
