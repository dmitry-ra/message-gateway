# Message Gateway

Message Gateway is a Python service for receiving, buffering, and delivering
messages to different applications through adapters.

Scripts and external systems submit messages through HTTP or MCP. The service
keeps a persistent queue for each destination and can combine pending messages
into a single batch when the destination is ready.

Destinations can include interactive terminal sessions, HTTP services, and other
programs. Runtime adapters handle discovery, readiness, and delivery while the
core manages queues and batches.

The first planned integration delivers external messages to an existing Codex
session while preserving normal terminal interaction. Where supported, compact
message previews are displayed separately from the complete original content.

## Status

Early development. The implementation and public API are being designed.

The planned development stack uses uv, pinned Python versions, Black, Ruff,
strict type checking, and automated tests.
