# Getting started

[Overview](../README.md) | [Reference](reference.md) | [Development](development.md)

This guide connects the included native adapter to an existing Codex
conversation on Linux. It has been exercised with Codex 0.166.0 and its local
background App Server. The gateway does not launch another Codex process.

## Install and prepare the receiver

Have Codex installed and authenticated, plus uv. Clone the repository and run
from the checkout:

```sh
uv sync --locked
codex plugin marketplace add .
codex plugin add message-gateway@message-gateway-local
```

The plugin and incoming-message skill live in `plugins/message-gateway`.
Start or restart the intended Codex session after installing the plugin and
enable it if requested. No hooks are required. The skill reads complete
`gateway_packet` context rather than answering from a shortened display preview.
The [operator skill](../.agents/skills/operate-message-gateway/SKILL.md) in this
repository guides setup and troubleshooting, not packet parsing.

## Verify the Codex runtime

Before binding a target, check the actual CLI, shared daemon and Unix socket:

```sh
codex --version
codex app-server daemon version
test -S "$HOME/.codex/app-server-control/app-server-control.sock"
```

The daemon version command is read-only JSON. Check `status` is `running`,
`appServerVersion` matches the tested version, and `socketPath` is the socket
Gateway should use. The example `test -S` checks the default; use the configured
socket path if yours differs. The adapter was tested with CLI and App Server
0.166.0, not merely with a terminal process named Codex.

If the daemon is absent, deliberately start it with
`codex app-server daemon start` as part of your setup, then open or resume the
intended conversation in the normal daemon-backed CLI. `--no-daemon` bypasses
the shared server and is unsuitable for this attachment. Use `codex agents`
or `/status` inside the intended conversation to verify its identity. Do not
restart a shared daemon or create another conversation merely to repair a
binding; first establish which receiver the operator intended.

## Bind a destination

Obtain the existing session's UUID from Codex, for example with `/status`.
Copy [examples/gateway.toml](../examples/gateway.toml) to `local.toml` and replace
the placeholder thread ID. Do not commit the private configuration.

```toml
[[targets]]
name = "work"
thread = "REPLACE_WITH_THREAD_ID"
batching = true
```

By default, messages wait while Codex is active and pending events can be
combined into a batch. For an interactive conversation set `interactive = true`.
Set `batching = false` as well if each message should be submitted separately.
These settings are independent. See [Codex target options](reference.md#codex-targets)
for delivery behavior, socket overrides and optional reasoning effort.

## Start the gateway

Configure `MESSAGE_GATEWAY_TOKEN` securely in the server and client
environments to require a bearer token. Never put secrets in command arguments
or committed files. Without a token, any local process can submit messages.
The service binds to loopback and is not intended for public exposure.

```sh
uv run --locked message-gateway serve --config local.toml
```

The default address is `127.0.0.1:10880`; `--port` overrides the port.

## Send through HTTP

For a local instance without authentication, inspect destinations and submit:

```sh
curl http://127.0.0.1:10880/v1/targets
curl -X POST http://127.0.0.1:10880/v1/targets/work/messages \
  -H 'Content-Type: application/json' \
  -d '{"channel":"builds","content":{"build":42,"request":"Review this result"}}'
```

When authentication is enabled, configure your HTTP client's
`Authorization: Bearer TOKEN` header from its secure environment or credential
configuration. HTTP `202` confirms queue acceptance in this process, not
delivery or processing. Check destination status, the intended conversation's
complete input and its actual response. The [HTTP reference](reference.md#http-api)
describes the contract and limits.

## Send through MCP

The same daemon exposes Streamable HTTP MCP on `/mcp`. Configure an MCP client:

```toml
[mcp_servers.message_gateway]
url = "http://127.0.0.1:10880/mcp"
# Include only when the daemon uses this token:
# bearer_token_env_var = "MESSAGE_GATEWAY_TOKEN"
```

Use `list_targets()` and `send_message(target, channel, content)`. Optional
sender, preview and sending-time metadata are described in the
[MCP reference](reference.md#mcp). No separate proxy or poller is launched.

## Add a source integration

Scripts and services construct their own text or JSON content; the gateway
routes by explicit destination and treats the source channel as opaque.
For two-way Telegram messaging, follow the canonical
[Telegram Relay + Gateway + Codex cookbook](https://github.com/dmitry-ra/telegram-bot-relay/blob/main/docs/codex.md).
Source integrations handle their own permissions, downloads and source-specific
skills. Gateway does not decode Telegram or other source payloads.

## Inspect and troubleshoot

Check `/v1/targets` or `list_targets()` for readiness, queued/in-flight counts
and the latest transport result. Then inspect the existing service log using
the [diagnostic event reference](reference.md#diagnostics). Under systemd, use
`journalctl --user -u UNIT -o cat` for your actual unit.

An accepted turn receipt is not a processing acknowledgment. Confirm the
intended conversation is loaded, its receiver skill is available, and the
target still points to that conversation. Loaded threads can outlive their
terminal. Do not blindly replay a batch with an uncertain submission result.

## Stop and remove

Stop the foreground service with Ctrl-C. All pending messages are discarded
on exit: account for the queue before a restart. Temporary context documents
are not an archive or durable queue. Remove the checkout and virtual
environment only when no longer needed and remove the plugin with:

```sh
codex plugin remove message-gateway@message-gateway-local
```

No database or queue files are created. See [delivery limits](reference.md#delivery-results-and-limits).
