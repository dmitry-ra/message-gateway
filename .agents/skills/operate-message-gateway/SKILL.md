---
name: operate-message-gateway
description: Configure, run, inspect and troubleshoot Message Gateway or connect a producer to an existing Codex conversation. Use for operating this repository's service; delivered gateway packets use the separate gateway-message skill.
---

# Operate Message Gateway

Message Gateway routes opaque text/JSON from HTTP or MCP producers through
per-destination volatile queues. The included native receiver adapter is Codex
on Linux. Source decoding, attachment handling and permissions belong to the
source integration, not Gateway.

## Choose the relevant guide

- Setup, receiver plugin, private target configuration, startup, HTTP/MCP clients
  and stop/remove: [getting started](../../../docs/getting-started.md).
- Options, message/timestamp contracts, receipts, context lifetimes and diagnostic
  fields: [reference](../../../docs/reference.md).
- Source changes and validation: [development](../../../docs/development.md)
  and [repository rules](../../../AGENTS.md).
- Optional bidirectional Telegram integration: the canonical
  [Relay cookbook](https://github.com/dmitry-ra/telegram-bot-relay/blob/main/docs/codex.md).

Read only the guide needed for the requested operation. Reuse the existing
service/configuration and the owner's chosen receiver when available; do not
invent a session binding or start a second Codex process. Setup requires an
explicit destination name and existing thread identity. Keep credentials out
of output and public files; use configured authentication without disclosing it.

## Operate and verify

For status, inspect `/v1/targets` or `list_targets()` through the configured
client, then the existing service diagnostics. Readiness, queued/in-flight
counts and latest transport result answer different questions. A queue response
or turn receipt is not proof of model processing; verify original input and an
actual response when the task requires end-to-end acceptance.

Before a requested restart, account for accepted but undelivered messages:
queues are memory-only. Uncertain submissions must not be blindly replayed.
Context documents are temporary transport artifacts, not durable message storage.
Loaded conversations can outlive their terminal, so verify the intended target.

Repository discovery does not itself request startup, installation, configuration
changes, synthetic messages or restarts. Perform the operation actually requested.
The separate [gateway-message skill](../../../plugins/message-gateway/skills/gateway-message/SKILL.md)
interprets incoming packet framing; it does not operate the daemon.
