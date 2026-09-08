# Deploying the MCP server remotely

The same server that runs over stdio also runs over HTTP. Nothing in
`src/handlers/`, `src/db.py`, `src/mcp_server.py` or `src/jsonrpc.py` changes;
only `src/http_transport.py` is added and `--http` selects it.

---

## What changes between local and remote

| | Local (stdio) | Remote (HTTP) |
| --- | --- | --- |
| Framing | one JSON message per line | one JSON message per request body |
| Who starts the server | the client, as a subprocess | it is already running |
| Session | one process is one client | `Mcp-Session-Id` header |
| Notifications | written, never answered | `202 Accepted`, empty body |
| Transport security | none needed, it is a pipe | TLS, terminated by the platform |

---

## Render (the deployment actually used)

**Live at https://vibbo-mcp.onrender.com** — endpoint `/mcp`.

Render's free plan needs no card and no prepayment, which is why this project
uses it. Google Cloud requires a 30 USD prepayment in some countries,
including Guatemala; the Cloud Run instructions are kept further down as an
alternative and work unchanged.

### 1. Push the repository

Render deploys from GitHub. The repository can stay private; Render asks for
access to it when you connect your account.

```powershell
git push origin main
```

`render.yaml` and `Dockerfile` must be committed. Render reads both.

### 2. Create the service

1. Sign in at [render.com](https://render.com) with GitHub.
2. **New → Blueprint**.
3. Pick this repository. Render finds `render.yaml` and shows one service,
   `vibbo-mcp`, on the free plan.
4. **Apply**.

The first build takes a few minutes: Render builds the image, which runs
`python data/seed.py` and bakes the database in.

The service gets a URL like:

```
https://vibbo-mcp.onrender.com
```

That is the base URL. The MCP endpoint is that plus `/mcp`.

### 3. Verify

```powershell
curl https://vibbo-mcp.onrender.com/
```

Expected:

```json
{"status":"ok","server":"vibbo-mcp-server","transport":"streamable-http","endpoint":"/mcp","sessions":0}
```

If it hangs for half a minute first, the service was asleep. That is the free
plan working as designed, not a failure.

### The sleep, and what to do about it

A free Render service stops after 15 minutes with no traffic and takes 30 to
50 seconds to answer the next request. Two consequences worth planning for:

- **Before a live demo,** send one request to wake it. The chatbot's own
  startup handshake will otherwise appear to hang.
- **The `STARTUP_TIMEOUT` in `chatbot/http_client.py` is 120 seconds**, which
  covers a cold start with room to spare.

```powershell
# warm it up, then start the chatbot
curl https://vibbo-mcp.onrender.com/
python -m chatbot
```

A restart also wipes the container's filesystem, which matters for tickets.
See [Known limitation](#known-limitation-the-database-is-ephemeral).

---

## Google Cloud Run (alternative)

Cloud Run does not sleep and is the provider the assignment links a tutorial
for. It requires a billing account, and in some countries a 30 USD refundable
prepayment.

### Prerequisites

Install the Google Cloud CLI. On Windows, download the installer from
https://cloud.google.com/sdk/docs/install and run it, then reopen the
terminal.

```powershell
gcloud --version
```

Log in and pick a project:

```powershell
gcloud auth login
gcloud projects create vibbo-mcp-<something-unique>
gcloud config set project vibbo-mcp-<something-unique>
```

Billing must be enabled on the project. Cloud Run's free tier covers this
project comfortably, but Google requires a billing account to exist.

Enable the two services the deploy needs:

```powershell
gcloud services enable run.googleapis.com cloudbuild.googleapis.com
```

---

### Deploy

From the repository root:

```powershell
gcloud run deploy vibbo-mcp `
  --source . `
  --region us-central1 `
  --allow-unauthenticated `
  --port 8080
```

`--source .` means Cloud Build builds the image from the `Dockerfile` in the
cloud; Docker does not need to be installed or running locally.

The command prints a URL like:

```
https://vibbo-mcp-abcdefghij-uc.a.run.app
```

That is the base URL. The MCP endpoint is that plus `/mcp`.

`--allow-unauthenticated` makes the service publicly reachable. That is what
this demo needs. It is also the reason the server exposes no tool that could
leak customer data: see the security section of the README.

---

### Verify

Health check:

```powershell
curl https://YOUR-URL.run.app/
```

A full handshake, by hand:

```powershell
# 1. initialize -- note the Mcp-Session-Id in the response headers
curl -i -X POST https://YOUR-URL.run.app/mcp `
  -H "Content-Type: application/json" `
  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-11-25\",\"capabilities\":{},\"clientInfo\":{\"name\":\"curl\",\"version\":\"1\"}}}'

# 2. every later request carries that id
curl -X POST https://YOUR-URL.run.app/mcp `
  -H "Content-Type: application/json" `
  -H "Mcp-Session-Id: THE-ID-FROM-STEP-1" `
  -d '{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\"}'
```

---

## Point the chatbot at it

Add an entry to `chatbot/servers.json`. A `url` instead of a `command` is the
only difference; the host picks the HTTP client on that basis alone.

```json
{
  "name": "vibbo-remote",
  "description": "The same VIBBO server, running remotely over HTTP.",
  "url": "https://vibbo-mcp.onrender.com",
  "enabled": true
}
```

Run `python -m chatbot` and `/servers` lists it beside the local ones. The
model calls `vibbo-remote__get_order_status` exactly the way it calls the
local `vibbo__get_order_status`; nothing in `session.py` knows the difference.

Keeping both enabled at once is the clearest demonstration: the same three
tools appear twice in `/tools`, once per transport, and either one answers.

---

## Known limitation: the database is ephemeral

The container's filesystem is in memory and is discarded when the instance
stops. Two consequences:

1. A ticket opened through the remote server is lost when the instance is
   recycled, and is not visible to any other instance.
2. Two instances serving at once do not share tickets.

This is correct behaviour for a stateless container, not a bug in the server.
Fixing it properly means moving the database out of the container — Cloud SQL,
Firestore, or a bucket — which is a deployment change, not a protocol one:
`src/db.py` is the only module that would be touched.

Render's free plan runs a single instance, so at least there is only ever
one copy of the data. On Cloud Run, cap it explicitly:

```powershell
gcloud run services update vibbo-mcp --region us-central1 --max-instances 1
```

Reads (`get_order_status`, `search_products`, the resources) are unaffected,
because the seed is baked into the image and is identical in every instance.

---

## Capturing the traffic for the Wireshark analysis

Both platforms serve HTTPS only, so a raw capture shows TLS records rather
than JSON. Two things make the traffic readable and the packets identifiable.

### Export the TLS session keys

`chatbot/http_client.py` sets `keylog_filename` on its SSL context from the
`SSLKEYLOGFILE` variable, so every TLS session writes its secrets to that
file and Wireshark can decrypt the capture.

```powershell
$env:SSLKEYLOGFILE = "$PWD\logs\tls-keys.log"
```

In Wireshark: **Edit -> Preferences -> Protocols -> TLS -> (Pre)-Master-Secret
log filename**, point it at that file. The capture then decodes into readable
HTTP.

The keys file is in `.gitignore` along with the rest of `logs/`. Anyone
holding it can decrypt that capture, so it does not belong in the repository.

### Generate traffic worth capturing

Capturing the chatbot directly is awkward: the model decides how many calls to
make and when, so no two captures look alike and nothing lines up. Use the
capture script instead. It sends a fixed sequence, one message at a time with
a pause between each, and writes a manifest naming every message.

```powershell
python tests/capture_session.py
```

It wakes the server first, so a 50-second cold start does not end up in the
capture, then prints the capture filter with the resolved addresses and waits
for you to start Wireshark. Press Enter and it sends:

| # | Kind | Method | Response |
| --- | --- | --- | --- |
| 1 | request | `initialize` | 200, result |
| 2 | **notification** | `notifications/initialized` | **202, empty body** |
| 3 | request | `ping` | 200, result |
| 4 | request | `tools/list` | 200, result |
| 5 | request | `tools/call` | 200, result |
| 6 | request | `resources/list` | 200, result |
| 7 | request | `resources/read` | 200, result |
| 8 | request | `prompts/list` | 200, result |
| 9 | request | `tools/call` | 200, **error -32602** |
| 10 | request | `admin/shutdown` | 200, **error -32601** |

Then a `DELETE /mcp` closes the session. Ten messages in, nine responses out:
the missing one is the notification.

Afterwards, `logs/capture-manifest.md` holds the addresses, the filters, the
timestamp of every message and how to classify what you see.

### Filters

Capture filter. The addresses come from the script's output; Render resolves
to more than one, so capture on whichever the run actually used.

```
host 216.24.57.15 and tcp port 443
```

Display filters, once decryption works:

```
http                              all decrypted HTTP
http.request.method == "POST"     every MCP message sent
http.response.code == 202         notifications only
http.response.code == 200         requests that got an answer
http contains "jsonrpc"           the JSON-RPC payloads
tcp.flags.syn == 1                connection setup
tls.handshake.type == 1           TLS Client Hello
```

### One thing to expect

`urllib` does not keep connections alive, so **each MCP message opens its own
TCP connection and its own TLS session**. A ten-message run produces about
twelve TLS handshakes. A production client would reuse one connection, and
this is a real inefficiency in this client.

For the analysis it is convenient: every message carries its own complete
layer stack, from the Ethernet frame through the TCP three-way handshake and
the TLS negotiation to the HTTP request and the JSON-RPC payload inside it. A
single message can be walked through all four layers in isolation.

## Cost

**Render's free plan** costs nothing and needs no payment method. The trade is
the sleep after 15 minutes of inactivity.

**Cloud Run's free tier** includes 2 million requests and 180,000 vCPU-seconds
a month, which this project uses a rounding error of. It does not sleep, but
it requires a billing account, and a 30 USD refundable prepayment in some
countries.

To take a deployment down: delete the service in Render's dashboard, or

```powershell
gcloud run services delete vibbo-mcp --region us-central1
```
