# Validation record

## Current foundation checks (0.5.0)

The current deterministic suite exercises one daemon hosting HTTP and Streamable
HTTP MCP, queue ordering and batching, complete original context, CLI startup,
packaged resources and authentication. `tools/check.py`, `uv build`, and
`tools/package_smoke.py` are required before merging. CI repeats those checks on
Python 3.12, 3.13, and 3.14 for the actual pull request head.

Package, CLI, OpenAPI, lockfile and plugin versions share the same release series.
Earlier branch prototypes reached 0.4.0 before a development version reset;
0.5.0 starts the integrated series without reusing those prototype versions.

## Historical prototype observations

The following records describe tests performed on earlier development snapshots,
starting 2026-10-31. They are evidence about those snapshots, not current test
counts or claims that removed transports remain supported.

- `tools/check.py`: Black, Ruff, strict mypy, compilation, ASCII, unit and
  loopback integration tests with branch coverage. The historical stdio MCP
  subprocess transport was subsequently replaced by Streamable HTTP MCP.
- `tools/mutations.py`: clean control passed; tests detected busy delivery,
  ignored batching configuration, and reversed FIFO in disposable source copies.
- `tools/matrix.py`: full local checks on Python 3.12 and 3.13, in addition to
  the pinned Python 3.14.10 environment.
- `uv build` and `tools/package_smoke.py`: wheel built from sdist, plugin skill
  present in sdist, installed wheel started a loopback server outside the
  checkout, accepted JSON content, and exposed packaged OpenAPI resources.
- Native live acceptance used Codex 0.166.0 in fresh interactive sessions with
  this repository's plugin. A 30-second shell task kept the receiver busy.
  Four HTTP submissions remained queued, then became one turn with batching
  enabled (39.54 seconds total), or four sequential turns with batching disabled
  (43.89 seconds total). Complete text and JSON values were preserved. Answers
  contained all four test tokens absent from visible previews; the skill was
  read in the terminal. Both tests passed.

Default CI runs no model sessions and requires no credentials. Live tests are
separately selected and do not substitute for the local deterministic suite.
CI must also pass for the exact commit before merging.

Limits: delivery acceptance is not a processing receipt; queues are volatile;
there is no deduplication. App Server's status check and turn submission are
not atomic with concurrent keyboard input. Only Linux Unix WebSocket attachment
to the tested Codex version has been exercised.

### Opaque content and display metadata (historical 0.3.0)

34 deterministic tests pass on Python 3.12-3.14, with approximately 99% branch
coverage. Source-shaped fields, nonexistent file paths, and different channel
names pass unchanged; no content decoder or file reader is called by routing
or rendering. HTTP and MCP accept optional sender/preview/sent_at display metadata.
The preview can differ from content without modifying the full context.

A separate, external source integration exercised three deliveries during a
30-second task: text, an image reference, and a document reference. The source
skill opened the actual image through the session's image tool and measured
its dimensions and byte size; it read the document's token and byte size.
These operations were performed by the receiver using the external skill,
not by the gateway or its adapter. Terminal capture confirmed accent color 75
for sender@channel> labels and a foreground reset before normal preview text.

## Bounded burst and response timing (Low)

The first 3x12 burst detected a real receiver limit: Codex truncated the middle
of each large additional-context value. All messages reached the transport,
but the model could not read all identifiers. Pinned Codex 0.166.0 source
confirmed a 1000 approximate-token limit per value (four bytes per token).
Regression tests now verify bounded fragments and exact reconstruction of
large ASCII, Unicode, and structured JSON content.

The same burst passed after framing, in a fresh interactive session with
effort=low supplied through turn/start. All 36 identifiers appeared exactly
as expected in three logical batches of 12. The model-visible rollout had
six complete fragments without truncation markers. All five recorded turns
in the session used Low. No source decoder was added to the gateway.

| Batch | Messages | Queue wait | First text after acceptance | Completion after acceptance |
|---|---|---|---|---|
| 1 | 12 | 13.814 s | 1.600 s | 9.140 s |
| 2 | 12 | 9.184 s | 3.090 s | 9.894 s |
| 3 | 12 | 9.989 s | 2.003 s | 9.381 s |

Dispatch calls took 13.9-15.2 ms. Each response deliberately included a
five-second shell task. The initial warmup included ten seconds of work;
later waves arrived while the previous packet was being processed. Queue
times therefore include intended busy time. First text means the first native
text event, including commentary. These are local timing observations from
one bounded run, not latency guarantees or maximum-throughput measurements.

The separate text/image/document Low run measured 1.863 s to first text and
6.254 s to completion after acceptance, following 33.848 s in the busy queue.
