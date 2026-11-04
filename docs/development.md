# Development

[Overview](../README.md) | [Reference](reference.md) | [Validation](validation.md)

Use uv 0.13.6. Python 3.14.10 is pinned for development; CI tests 3.12-3.14.
The [repository instructions](../AGENTS.md) describe architecture and contribution
rules. Root `tools/` contains developer checks and reproducible demonstrations,
not another runtime service.

## Local checks

```sh
uv sync --locked --group dev
uv run --locked python tools/check.py
uv build
uv run --locked python tools/package_smoke.py
```

Run required local checks on final code before each code commit. They include
ASCII checks, Black, Ruff, strict mypy, compilation, tests and branch coverage
(at least 90%). Tests use real loopback HTTP/MCP transports, synthetic
destinations, validation/authentication errors, concurrent clients and disconnects.
No model credentials or external service accounts are needed by default.

CI repeats checks on Python 3.12, 3.13 and 3.14 for every pull request and main
push. It builds distributions and installs the wheel outside the checkout.
The exact final head must pass CI before merging; local success alone is not CI.

```sh
uv run --locked python tools/mutations.py
uv run --locked python tools/matrix.py
```

Use targeted mutation checks for queue invariants, rather than a blanket mutation
campaign. See the [validation record](validation.md) for current and historical
evidence and compatibility limits.

## Native live tests

Live tests are opt-in. Select a dedicated interactive Codex session with the
incoming plugin enabled; never point a synthetic test at an operational thread.
Set `MESSAGE_GATEWAY_TEST_THREAD` to its ID. Busy-delivery tests start a
30-second shell task, submit four messages through MCP, and check complete
text/JSON including tokens absent from previews. Use a fresh thread per mode:

```sh
uv run --locked pytest -o addopts= --import-mode=importlib -m live -s \
  tests/live/test_codex.py -k True
```

Repeat with `-k False` and a new thread for unbatched delivery.
`MESSAGE_GATEWAY_TEST_SOCKET` overrides the native socket. The separate
[interactive acceptance procedure](validation.md#native-interactive-acceptance-060)
creates and archives its own dedicated thread; it verifies active-turn steering.

## Usage demo

The README GIF runs the real Gateway HTTP API, queue and batching implementation
with two synthetic build messages and a simulated receiver. It is not a native
Codex run or a live-delivery proof. Both the visible receiver and caption state
this boundary. The initial frame describes the scenario and the final frame
holds the verified batch result, so the story also works without animation.

The reproducible driver is [tools/demo.py](../tools/demo.py). Rendering requires
asciinema 3.2.1, agg 1.9.0, ffmpeg 6.1.1 and an existing DejaVu Sans Mono font;
the script does not install tools, fonts or services. After syncing dev dependencies:

```sh
uv run --locked python tools/demo.py play
uv run --locked python tools/demo.py record --directory /tmp/gateway-demo
```

Use a new output directory. Recording produces raw/sanitized casts, GIF, poster
and contact sheet. Scan both casts and inspect the poster, contact sheet and
animation before publishing. Only the reviewed GIF belongs at `docs/demo.gif`.
Sanitization removes recorder metadata and trims only the startup blank period;
real output and relative event intervals are preserved. Recordings contain no
owner desktop, credentials, private paths or live session
IDs. The GIF is an intentional source-tree documentation asset; it is excluded
from wheel/sdist packages, while reproduction code and text guides ship in sdist.
