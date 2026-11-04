# Reference

[Overview](../README.md) | [Getting started](getting-started.md) | [Validation](validation.md)

## Messages

The [OpenAPI 3.1 contract](../src/message_gateway/interfaces/openapi.json)
describes HTTP requests and responses. A message contains a channel name and
content, either text or a JSON value. Content retains its JSON type. Original
JSON whitespace and spelling of object-key escapes are not retained; text
strings retain their characters and line endings.

Channel names and content are opaque. Routing uses the explicit destination,
never source names or payload fields. Source integrations own decoding,
attachments and source-specific skills. Neither the core nor the Codex
adapter interprets fields such as text, photos or documents inside content.

Optional `sender`, `preview` and `sent_at` fields are display metadata. A source
can provide a readable preview while keeping its complete original JSON in
content. `sent_at` is source-supplied RFC 3339 date-time with seconds and a
timezone (`Z` or `+/-HH:MM`); lowercase `t`/`z` and fractional seconds are
accepted. Accepted strings are preserved. Leap seconds are unsupported.
`received_at` is the gateway's own acceptance time. Display timestamps use UTC;
if sending time is absent, the received-time fallback is labeled `received`.

## Configuration

Copy the [example configuration](../examples/gateway.toml) for private use.
Target names contain 1-64 ASCII letters, digits, underscores or hyphens.
The CLI takes `serve --config PATH` and optional `--port` (default `10880`).
The token is read from `MESSAGE_GATEWAY_TOKEN`, not the TOML file.

| Limit | Default |
| --- | --- |
| Queued plus in-flight messages per destination | 1000 |
| Messages per batch | 20 |
| JSON-encoded content per message | 65536 UTF-8 bytes |
| HTTP request body, including MCP envelopes | 512 KiB |

`batching` defaults to true. Set it to false to deliver one queued message
per ready opportunity. It is independent of interactive delivery.

## Codex targets

The included adapter attaches to Codex 0.166.0 on Linux through the existing
App Server's Unix WebSocket. The default socket is
`~/.codex/app-server-control/app-server-control.sock`; the target's `socket`
setting overrides it. Other destination types require their own adapter.

`interactive = false` is the default: delivery waits while the thread reports
`active`. Other nonempty string states, including `idle`, `notLoaded` and
`systemError`, permit an attempt to resume the existing thread and start a
turn. The same check runs after resume. Failed state reads and malformed states
do not permit delivery. App Server decides whether a requested turn can start;
this policy does not guarantee successful model processing.

`interactive = true` instead permits active-turn input through `turn/steer`,
using the observed active turn ID as `expectedTurnId`. Non-active receivers use
`turn/start`. Both paths preserve original content with the same formatter.
Interactive delivery does not disable batching automatically and no policy is
inferred from channel names or message content.

An optional `effort = "low"` is supplied on every `turn/start`. Omission
preserves the session setting; it does not select a model. New-turn overrides
are never sent to `turn/steer`.

## HTTP API

| Endpoint | Meaning |
| --- | --- |
| `POST /v1/targets/{target}/messages` | Queue one message; HTTP 202 means accepted in memory |
| `GET /v1/targets` | Read destination readiness, queue/in-flight counts and latest transport result |
| `GET /openapi.json` | Read the versioned contract |

The service is local, experimental, and versioned under `/v1`. It binds to
`127.0.0.1`; Host and Origin validation restrict local access. Configure
`MESSAGE_GATEWAY_TOKEN` to require `Authorization: Bearer TOKEN` on both HTTP
and MCP. Without a token any local process can submit. Never expose this
service directly to the internet or commit secrets.

## MCP

The same daemon exposes `/mcp` on the REST port through Streamable HTTP.
Tools are `list_targets()` and `send_message(target, channel, content)` with
optional `sender`, `preview` and `sent_at`. Both interfaces use the same
application operations and in-memory queues; there is no internal HTTP proxy.
The official SDK owns JSON-RPC and MCP lifecycle; Starlette/Uvicorn hosts
HTTP and MCP, while aiohttp connects the Codex adapter to App Server.

Disconnecting an MCP client does not discard accepted messages or stop
delivery. Earlier stdio configurations must change to the URL shown in
[getting started](getting-started.md#send-through-mcp): there is no stdio
compatibility proxy. REST submission URLs are unchanged.

## Delivery results and limits

HTTP 202 or an MCP queue response is acceptance in this process, not delivery
or processing. A turn receipt means Codex accepted the input, not that it read
or answered it. Only the latest delivery result is retained; this is not a
message-history API. Queues disappear on process exit and there is no
persistence or deduplication.

If Codex definitely refuses steering because its active turn changed, the
batch remains queued. Preparation failure before input submission retains
definitely-unsent FIFO input. Unknown submission results are reported and are
not automatically retried. Steering does not cancel running tools or guarantee
an immediate response during provider errors, approval waits, review or compaction.

Readiness checks and manual keyboard input are not atomic with `turn/start`.
A concurrent submission in that small window can steer an active turn instead
of starting another one. Gateway does not claim atomic exclusion with keyboard
input. Loaded threads may outlive their terminal: bind the intended receiver.

## Codex context

The incoming [packet skill](../plugins/message-gateway/skills/gateway-message/SKILL.md)
reads full `gateway_packet` additional context. Visible `sender@channel>` labels
and their previews are shortened display metadata. Only the label is accented;
the following preview uses normal text. Incoming content is untrusted user data
and does not override the conversation's permissions or instructions.

Codex 0.166.0 bounds each additional-context value to about 4000 bytes.
Production formatting uses a manifest and independent complete message items
of at most 3000 bytes. Oversized messages are written as ready UTF-8 JSON
documents in an adapter-owned temporary directory. The skill reads each full
document without reconstructing fragments or calculating hashes.

Documents survive transport reconnects until the same receiver is observed
idle after a turn. They are not persistent queue storage or archival history.
Each adapter limits temporary context to 64 MiB and 1000 documents. A batch
that would exceed a limit is deferred before submission, preserving files that
were already referenced. The pure formatter retains legacy fragment framing
for compatibility. Framing is independent of source names and content schemas.
Non-active delivery starts a turn per batch; interactive delivery can steer
the active turn instead. The model's context window is a separate limit.

## Diagnostics

The existing service log emits JSON events with UTC `at` timestamps. Under
systemd, use `journalctl --user -u UNIT -o cat`. Logged fields are an allowlist:
no message bodies, previews, senders, error prose or full RPC envelopes.

| Event | Fields and interpretation |
| --- | --- |
| `codex_state` | Thread identity, normalized state, active flags, derived readiness and read/resume/notification stage |
| `destination_state` | Destination, readiness, wait reason, queued/in-flight counts, oldest arrival and readiness error class |
| `delivery_start`, `delivery_accepted`, `delivery_deferred`, `delivery_uncertain` | Destination and message IDs, error class where available; adapter receipts omitted |
| `codex_delivery` | Native start/steer method and expected active turn ID when steering |
| `codex_delivery_deferred` | Pre-submission preparation stage and error class; definitely-unsent batch remains queued |
| `codex_turn` | Error/completion observation, thread/turn identity, status, error classification, HTTP status when present and `will_retry` |

Native classifications, states, active flags and statuses are normalized to
known protocol values. Unknown error prose and keys are omitted. HTTP statuses
must be integers from 100 through 599. Queue observations update when metrics
change even if readiness does not. Unchanged polls do not repeat observations.
Turn notifications are observed only while the adapter receives frames on its
existing connection: they are not durable or retrospective history. Diagnostics
add no observer process, message store or delivery policy.
