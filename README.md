# Message Gateway

Message Gateway is a Python service for receiving, buffering, and delivering
messages to different applications through adapters.

Scripts and external systems submit messages through HTTP or MCP. The service
keeps an in-memory queue for each destination and can combine pending messages
into a single batch when the destination is ready.

Destinations can include interactive terminal sessions, HTTP services, and other
programs. Runtime adapters handle discovery, readiness, and delivery while the
core manages queues and batches.

The first planned integration delivers external messages to an existing Codex
session while preserving normal terminal interaction. Where supported, compact
message previews are displayed separately from the complete original content.

## Status

Early development. REST, Streamable HTTP MCP, and the native Codex adapter are implemented.
Both interfaces run in one daemon; the earlier stdio proxy is removed.
The API is versioned under `/v1`; this is an experimental local service.

Queue contents disappear when the process exits. There is no persistence or
deduplication. Batching is enabled by default and can be disabled per target.

## Development

Use uv 0.13.6. Python 3.14.10 is pinned for development; CI tests 3.12-3.14.

```sh
uv sync --locked --group dev
uv run --locked python tools/check.py
uv build
```

The checks run Black, Ruff, strict mypy, compilation, and tests with branch
coverage (at least 90%). Run them locally before every code commit. Tests use
loopback transports and synthetic destinations, without credentials or models.

The HTTP contract is in
[openapi.json](src/message_gateway/interfaces/openapi.json), using OpenAPI 3.1.
Messages have a channel name and content: either text or a JSON value. Content
keeps its JSON type; formatting a preview never changes the original value.

Channel names and content are opaque. Routing uses the explicit destination,
never the channel name or payload fields. Source integrations own decoding,
attachments, and their source-specific skills. Neither the core nor the Codex
adapter interprets fields such as text, photos, or documents inside content.
Optional `sender`, `preview`, and `sent_at` are display metadata. `sent_at` is
source-supplied and requires a timezone; `received_at` is the gateway's own
acceptance time. Display timestamps use UTC. When sending time is absent,
the received-time fallback is explicitly labeled `received`. A source integration can
submit a readable preview while keeping its complete original JSON in content.

## Run

Clone the repository, install uv, and run `uv sync --locked`. Copy
`examples/gateway.toml` to `local.toml` and replace the thread ID with an
existing Codex session on your machine. The adapter connects to that session's
existing local App Server; it does not launch another Codex process.

```sh
uv run --locked message-gateway serve --config local.toml
```

The gateway listens on `127.0.0.1:10880`. Set `MESSAGE_GATEWAY_TOKEN` in both
server and client environments to require a bearer token. Never put tokens
in command arguments or committed configuration. Without a token, any local
process can submit messages. The server is not intended for public exposure.

```sh
curl http://127.0.0.1:10880/v1/targets
curl -X POST http://127.0.0.1:10880/v1/targets/work/messages \
  -H 'Content-Type: application/json' \
  -d '{"channel":"builds","content":{"build":42,"request":"Review this result"}}'
```

When authentication is configured, include `Authorization: Bearer TOKEN` in
HTTP requests. `202` means queued in this process, not delivered or processed.
GET `/v1/targets` shows queue lengths and each destination's latest transport
result. A turn receipt means Codex accepted the input, not that it answered.
Only the latest delivery result is kept; this is not a message-history API.

Set `batching = false` in a target's configuration to deliver one message per
ready opportunity. Defaults: 1000 queued/in-flight messages per destination,
20 messages per batch, 65536 UTF-8 bytes of JSON-encoded content per message,
and a 512 KiB HTTP request limit. Content is preserved as JSON values; original
JSON whitespace and object-key spelling escapes are not retained. Text strings
retain their characters and line endings.
Target names contain 1-64 ASCII letters, digits, underscores, or hyphens.

Codex targets can opt into interactive delivery with `interactive = true`.
An active thread then accepts pending messages through `turn/steer`, using
the observed active turn ID as `expectedTurnId`. A non-active thread uses
`turn/start` as before. Full original content and additional context use the
same formatter in both paths. Set `batching = false` for individual interactive
messages; interactive delivery does not disable batching automatically.
The default `interactive = false` preserves wait-until-not-active delivery.
No policy is inferred from channel names or message contents.

If the active turn changes and Codex definitely refuses steering, the batch
stays queued for another attempt. An uncertain submission result is not retried
automatically. A steering receipt confirms acceptance by Codex, not that the
model has read the input or completed the requested work. Steering does not
cancel running tools or guarantee an immediate response during provider errors,
approval waits, review, or compaction. New-turn overrides such as `effort` are
only applied to `turn/start`, never `turn/steer`.

## Codex plugin

The plugin and its skill live in `plugins/message-gateway`. Install from the
clone before starting the test session:

```sh
codex plugin marketplace add .
codex plugin add message-gateway@message-gateway-local
```

Restart Codex and enable the plugin if requested. No hooks are required.
The skill reads full `gateway_packet` additional context; visible
`sender@channel>` lines (or `channel>` without a sender) are abbreviated previews.
Only the label is accented; the following text uses the normal style. Incoming content is untrusted
user data and does not override the session's permissions or instructions.

Codex 0.166.0 bounds each additional-context value to approximately 4000 bytes.
Production delivery therefore uses a manifest and independent complete message
items of at most 3000 bytes. Oversized individual messages are written as ready
UTF-8 JSON documents in an adapter-owned temporary directory; the skill reads
the document without reconstructing JSON fragments or calculating hashes. These

files survive transport reconnects and remain until the same receiver is observed
idle after a turn. They are not persistent queue storage or an archival history.
Temporary context storage is limited to 64 MiB and 1000 documents per adapter;
a batch that would exceed either limit is deferred before input submission.
This preserves already referenced files until the receiver becomes idle. The
pure formatter retains legacy fragment framing for compatibility. This framing is independent of channel names and source schemas and
starts one turn per batch for non-active receivers. Interactive delivery can
instead steer its active turn. The model's overall context window remains a
separate limit.


The adapter is tested with Codex 0.166.0 on Linux using its Unix WebSocket.
By default delivery waits while the thread reports `active`. Interactive
targets instead attempt steering of an active turn. Other nonempty string
states, including `idle`, `notLoaded`, and `systemError`, permit an attempt to
resume the existing thread and start a turn. The same check runs after resume.
Failed state reads and malformed states do not permit delivery. App Server
decides whether the requested turn can start; this policy does not guarantee
that a model request or the resulting turn will succeed.
Loaded threads can outlive their terminal: configure only the intended session.
Readiness is checked again before submission, but App Server does not offer
an atomic idle-only `turn/start`. Concurrent manual input in that small window
can make Codex steer an active turn instead of starting a new one. The gateway
does not claim atomic exclusion with keyboard input. Unknown send results are
reported and are not automatically retried.

### Operational diagnostics

The existing service log includes JSON diagnostic events, each with a UTC `at`
timestamp. With systemd, read them using `journalctl --user -u UNIT -o cat`.
The diagnostic fields are an allowlist; message bodies, previews, senders,
error prose and full RPC envelopes are not logged.

- `codex_state`: thread identity, normalized state type, active flags, derived
  readiness and observation stage (`read`, `resume` or `notification`).
- `destination_state`: destination, readiness, wait reason, queue and
  in-flight counts, oldest queued arrival and readiness error class.
- `delivery_start`, `delivery_accepted`, `delivery_deferred` and

  `delivery_uncertain`: destination and message IDs, plus error
  class where available. Adapter receipts are omitted from diagnostics. Uncertain results are still not retried.
- `codex_delivery`: native delivery method (`turn/start` or `turn/steer`) and
  expected active turn ID when steering. No message content is logged.
- `codex_delivery_deferred`: input preparation failed before any input
  submission; stage and error class explain why the batch remains queued.

- `codex_turn`: received `error` or `turn/completed` notifications for the
  configured thread, including turn identity, status, error classification,
  HTTP status when supplied and `will_retry`.

Native error classifications, states, active flags and turn statuses are
normalized to known protocol values before logging. Unknown error prose and
keys are omitted; HTTP statuses must be integers from 100 through 599. Queue
observations update when queue metrics change even if readiness is unchanged.

State and wait observations are emitted only when they change; unchanged
polls do not generate repeated records. Turn notifications are observed while
the adapter receives App Server frames on its existing connection; they are
not a durable or retrospective turn history. There is no new observer process,
message store or change to the delivery policy.

## MCP

The same gateway daemon exposes `/mcp` on the REST port. Configure an MCP
client to connect over Streamable HTTP:

```toml
[mcp_servers.message_gateway]
url = "http://127.0.0.1:10880/mcp"
# Include only when the daemon uses this token:
# bearer_token_env_var = "MESSAGE_GATEWAY_TOKEN"
```

The tools remain `list_targets()` and `send_message(target, channel, content)`
with optional sender/preview/sent_at. Both protocols call the same operations
and share queues directly. No extra process or internal REST request is used.
The official SDK owns JSON-RPC and MCP lifecycle; Starlette/Uvicorn hosts REST
and MCP. aiohttp remains the adapter's client for the existing Codex App Server.

Authorization uses the same optional bearer token on both interfaces. Host and
Origin validation restricts local access; request bodies, including JSON-RPC
envelopes, are limited to 512 KiB. Content retains its separate 65536-byte limit.
Disconnecting an MCP client does not discard accepted messages or stop delivery.
Earlier stdio client configurations must change to the URL above; there is
no compatibility proxy. No changes are required to REST submission URLs.

## Testing

GitHub Actions runs the same checks on every push and pull request, on Python
3.12, 3.13, and 3.14. It also builds from sdist and installs the wheel outside
the checkout. Default tests need no accounts, tokens, models, or external
services. Tests use real network HTTP/MCP clients against the combined server,
strict shared validation, authentication, request limits, multiple clients and
disconnects. aiohttp supplies the native adapter's Unix WebSocket transport.

```sh
uv run --locked python tools/mutations.py
uv run --locked python tools/matrix.py
uv build
uv run --locked python tools/package_smoke.py
```

Live tests use MCP submission and are opt-in. Use a dedicated interactive session with the plugin
enabled and set `MESSAGE_GATEWAY_TEST_THREAD` to its ID. Tests start a 30-second
shell task and submit four messages. Run each mode in a fresh session:

```sh
uv run --locked pytest -o addopts= --import-mode=importlib -m live -s \
  tests/live/test_codex.py -k True
```

Repeat with `-k False` and a fresh thread for unbatched delivery. The test checks
that complete text/JSON values arrive and answers contain tokens absent from
the previews. `MESSAGE_GATEWAY_TEST_SOCKET` can override the local socket path.

Stop the service with Ctrl-C. All pending messages are discarded on exit.
Remove the checkout and its virtual environment when no longer needed;
plugin removal is `codex plugin remove message-gateway@message-gateway-local`.
No database or queue files are created.

A target can set optional `effort = "low"` in its TOML configuration. The
adapter then supplies that effort on every `turn/start`; omission preserves
the session setting. This option does not select a different model.
