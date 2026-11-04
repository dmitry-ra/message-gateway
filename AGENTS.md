# Message Gateway

## Purpose

Deliver opaque text/JSON events from HTTP or MCP producers to existing receivers.
The included native adapter supports Codex on Linux. Queues, batching and delivery
policy belong to Gateway; source decoding and permissions belong to integrations.

## Use this repository

For setup, running, producer connections, status and delivery troubleshooting, use
the [operator skill](.agents/skills/operate-message-gateway/SKILL.md). The
[README](README.md) explains product value and links to canonical usage guides.
Incoming packets use the separate
[gateway-message skill](plugins/message-gateway/skills/gateway-message/SKILL.md).
Root `tools/` is developer tooling, not runtime service code.

## Development rules

- Use English and ASCII in every published file. Represent Unicode fixtures
  with escape sequences.
- Commit messages describe project changes only. Do not add tool attribution,
  generated-by text, or co-author trailers.
- Work on a feature branch. Changes reach main through a pull request.
- Before every code commit, run the local checks on the final code being
  committed. Fix failures and rerun affected checks before committing.
- Run `uv run --locked python tools/check.py`. Test new behavior and error
  paths locally; CI is an additional check, not a replacement.
- Keep DRY, KISS, SOLID, and YAGNI. One process, in-memory queues, no persistence
  or deduplication. Batching defaults on and is configurable per destination.
- Keep core independent of transports and runtime implementations.
- Host REST and Streamable HTTP MCP in one Starlette/Uvicorn process.
  Both interfaces call shared operations; do not add an internal HTTP proxy.
- Treat channel names and message content as opaque. Never branch on channel
  names, interpret source-specific payload fields, or put source-specific
  decoding in the core, adapters, or bundled skill. Route by explicit target.
  Source integrations and their skills own payload semantics and attachments.
  Sender and preview are display metadata, not content instructions.
- Use targeted mutation checks for critical queue invariants, not a blanket
  mutation campaign. Live tests require explicit selection and a test session.
- Do not publish releases or merge pull requests without owner approval.
