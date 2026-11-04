"""Reproduce a real Gateway queue demonstration with a simulated receiver."""

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from message_gateway.core.models import JsonValue, Readiness  # noqa: E402
from message_gateway.core.service import Service, Target  # noqa: E402
from message_gateway.interfaces.server import create_app  # noqa: E402
from tests.helpers import FakeAdapter, running  # noqa: E402

WIDTH, HEIGHT = 88, 23


def frame(service: Service, receiver: FakeAdapter, submitted: list[str], phase: str) -> str:
    status = service.status()["demo"]
    assert isinstance(status, dict)
    delivered = [message.content for batch in receiver.batches for message in batch]
    lines = [
        "MESSAGE GATEWAY - queue two events, deliver one batch",
        "Actual HTTP Gateway | synthetic events | simulated receiver",
        "",
        "[ PRODUCER: real HTTP requests ]",
        *submitted,
        *[""] * (2 - len(submitted)),
        "",
        "[ GATEWAY: actual queue and batch policy ]",
        f"Target: demo    State: {status['state']}    Queued: {status['queued']}",
        "Policy: wait while busy; batch pending input when ready",
        "",
        "[ RECEIVER: simulated; same demo destination throughout ]",
        f"Received batches: {len(receiver.batches)}    Received events: {len(delivered)}",
        *[json.dumps(content, separators=(",", ":")) for content in delivered],
        *[""] * (2 - len(delivered)),
        "",
        phase,
        "",
        "Receiver is simulated. No Codex session or external service is contacted.",
    ]
    assert max(map(len, lines)) <= WIDTH and len(lines) <= HEIGHT
    return "\x1b[2J\x1b[H" + "\r\n".join(lines)


async def play(delay: float = 1, emit: Callable[[str], None] | None = None) -> dict[str, JsonValue]:
    logging_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    receiver = FakeAdapter(Readiness.BUSY)
    service = Service({"demo": Target(receiver)})
    submitted: list[str] = []

    def show(phase: str) -> None:
        text = frame(service, receiver, submitted, phase)
        if emit is not None:
            emit(text)

    contents: list[JsonValue] = [
        {"build": 101, "result": "passed"},
        {"build": 102, "result": "passed"},
    ]
    try:
        async with running(create_app(service)) as url:
            await service.tick()
            show("The receiver is busy. Incoming events will wait in Gateway.")
            await asyncio.sleep(2 * delay)
            async with aiohttp.ClientSession() as client:
                for content in contents:
                    async with client.post(
                        url + "/v1/targets/demo/messages",
                        json={"channel": "builds", "content": content},
                    ) as response:
                        response.raise_for_status()
                        accepted = await response.json()
                        assert response.status == 202 and accepted["content"] == content
                        submitted.append(
                            f"POST event {len(submitted) + 1}: HTTP {response.status} accepted"
                        )
                    await service.tick()
                    assert not receiver.batches
                    show("Events are accepted, but the busy receiver has received none.")
                    await asyncio.sleep((2 if len(submitted) == 1 else 4) * delay)
            queued = len(service.targets["demo"].queue)
            assert queued == 2
            receiver.state = Readiness.READY
            await service.tick()
            received = [message.content for batch in receiver.batches for message in batch]
            assert len(receiver.batches) == 1 and received == contents
            show("Receiver ready: both original JSON events arrived in one batch.")
            await asyncio.sleep(4 * delay)
            show("Verified: FIFO preserved, 2 original events, 1 batch, queue empty.")
            await asyncio.sleep(5 * delay)
            return {
                "queued_while_busy": queued,
                "batch_count": len(receiver.batches),
                "received": received,
                "queued_after": len(service.targets["demo"].queue),
            }
    finally:
        await receiver.close()
        logging.disable(logging_level)


def scan_recording(path: Path) -> None:
    text = path.read_text(encoding="ascii")
    records = [json.loads(line) for line in text.splitlines()]
    combined = json.dumps(records, ensure_ascii=False)
    patterns = (
        r"/(?:home|Users|mnt)/",
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
        r"[0-9]{8,12}:[A-Za-z0-9_-]{30,}",
    )
    if any(re.search(pattern, combined) for pattern in patterns):
        raise ValueError("Recording contains a private path or identifying value")
    for event in records[1:]:
        if isinstance(event, list) and len(event) == 3 and event[1] == "x":
            if type(event[2]) not in (int, str) or event[2] not in (0, "0"):
                raise ValueError("Recorded command did not exit successfully")
            continue
        if not isinstance(event, list) or len(event) != 3 or event[1] != "o":
            raise ValueError("Expected output-only recording events")
        output = event[2]
        if not isinstance(output, str):
            raise ValueError("Expected textual terminal output")
        output.encode("ascii")
        if "\x1b]" in output:
            raise ValueError("Recording contains an OSC sequence")


def sanitize_recording(raw: Path, cast: Path) -> None:
    scan_recording(raw)
    records = [json.loads(line) for line in raw.read_text(encoding="ascii").splitlines()]
    events = [event for event in records[1:] if event[1] == "o"]
    if not events:
        raise ValueError("Recording has no terminal output")
    offset = events[0][0]
    for event in events:
        event[0] = round(event[0] - offset, 6)
    header = {
        "version": 2,
        "width": WIDTH,
        "height": HEIGHT,
        "title": "Message Gateway queue and batch demonstration",
        "env": {"TERM": "xterm-256color"},
    }
    cast.write_text(
        "\n".join(json.dumps(value) for value in [header, *events]) + "\n", encoding="ascii"
    )
    scan_recording(cast)


def record(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    expected = {"asciinema": "asciinema 3.2.1", "agg": "agg 1.9.0"}
    for command, version in expected.items():
        actual = subprocess.check_output([command, "--version"], text=True).strip()
        if actual != version:
            raise RuntimeError(f"Expected {version}")
    version = subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0]
    if not version.startswith("ffmpeg version 6.1.1"):
        raise RuntimeError("Expected ffmpeg 6.1.1")
    environment = dict(os.environ, SHELL="/bin/sh", TERM="xterm-256color", LC_ALL="C.UTF-8")
    environment["ASCIINEMA_CONFIG_HOME"] = str(directory / "recorder-config")
    environment.pop("BASH_ENV", None)
    raw = directory / "raw.cast"
    subprocess.run(
        [
            "asciinema",
            "rec",
            "--headless",
            "--return",
            "--quiet",
            "--output-format",
            "asciicast-v2",
            "--window-size",
            f"{WIDTH}x{HEIGHT}",
            "--command",
            ".venv/bin/python tools/demo.py play",
            str(raw),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        timeout=45,
    )
    cast = directory / "demo.cast"
    sanitize_recording(raw, cast)
    gif = directory / "demo.gif"
    subprocess.run(
        [
            "agg",
            "--theme",
            "github-dark",
            "--font-family",
            "DejaVu Sans Mono",
            "--font-size",
            "18",
            "--idle-time-limit",
            "10",
            "--last-frame-duration",
            "4",
            str(cast),
            str(gif),
        ],
        check=True,
        timeout=60,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-i",
            str(gif),
            "-frames:v",
            "1",
            str(directory / "poster.png"),
        ],
        check=True,
        timeout=30,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-i",
            str(gif),
            "-vf",
            "setpts=N/TB,scale=440:-1,tile=3x2:nb_frames=5",
            "-frames:v",
            "1",
            str(directory / "contact-sheet.png"),
        ],
        check=True,
        timeout=30,
    )
    print("Recorded actual Gateway behavior with a simulated receiver.")
    print(f"Review {gif}, poster.png, contact-sheet.png and both casts before publication.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("play")
    capture = commands.add_parser("record")
    capture.add_argument("--directory", type=Path, required=True)
    scan = commands.add_parser("scan")
    scan.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "play":
        asyncio.run(play(emit=lambda text: print(text, end="", flush=True)))
    elif args.command == "record":
        record(args.directory.resolve())
    else:
        scan_recording(args.path)


if __name__ == "__main__":
    main()
