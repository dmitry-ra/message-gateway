import asyncio
import json
from pathlib import Path

import pytest

from tools.check import check_file
from tools.demo import play, sanitize_recording, scan_recording

GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b"
)


def test_only_owned_valid_gif_bypasses_text_ascii(tmp_path: Path) -> None:
    path = tmp_path / "asset"
    path.write_bytes(GIF)
    check_file(path, "docs/demo.gif")
    with pytest.raises(UnicodeDecodeError):
        check_file(path, "docs/other.gif")
    with pytest.raises(UnicodeDecodeError):
        check_file(path, "src/image.py")
    path.write_bytes(b"not a GIF")
    with pytest.raises(ValueError):
        check_file(path, "docs/demo.gif")
    path.write_bytes(GIF + b"x" * (3 * 1024 * 1024))
    with pytest.raises(ValueError):
        check_file(path, "docs/demo.gif")
    path.write_bytes(b"text\xff")
    with pytest.raises(UnicodeDecodeError):
        check_file(path, "README.md")
    with pytest.raises(UnicodeEncodeError):
        check_file(path, "docs/\u00e9.txt")


def test_demo_uses_actual_http_queue_and_fifo_batch() -> None:
    frames: list[str] = []
    result = asyncio.run(play(delay=0, emit=frames.append))
    assert result == {
        "queued_while_busy": 2,
        "batch_count": 1,
        "received": [{"build": 101, "result": "passed"}, {"build": 102, "result": "passed"}],
        "queued_after": 0,
    }
    assert len(frames) == 5


@pytest.mark.parametrize(
    "output",
    [
        "/home/example/private",
        "user@example.org",
        "\x1b]0;title\x07",
        "100000000:abcdefghijklmnopqrstuvwxyz123456",
        "\u00e9",
    ],
)
def test_recording_scan_rejects_identifying_or_hidden_output(tmp_path: Path, output: str) -> None:
    path = tmp_path / "sample.cast"
    path.write_text(json.dumps({"version": 2}) + "\n" + json.dumps([0, "o", output]) + "\n")
    with pytest.raises((ValueError, UnicodeError)):
        scan_recording(path)


def test_recording_scan_accepts_neutral_actual_output(tmp_path: Path) -> None:
    path = tmp_path / "sample.cast"
    path.write_text(json.dumps({"version": 2}) + "\n" + json.dumps([0, "o", "Queued: 2"]) + "\n")
    scan_recording(path)


@pytest.mark.parametrize("exit_code", [0, "0"])
def test_recording_scan_accepts_successful_exit(tmp_path: Path, exit_code: int | str) -> None:
    path = tmp_path / "sample.cast"
    path.write_text(json.dumps({"version": 2}) + "\n" + json.dumps([0, "x", exit_code]) + "\n")
    scan_recording(path)


@pytest.mark.parametrize("exit_code", [1, "1", False])
def test_recording_scan_rejects_unsuccessful_exit(
    tmp_path: Path,
    exit_code: int | str,
) -> None:
    path = tmp_path / "sample.cast"
    path.write_text(json.dumps({"version": 2}) + "\n" + json.dumps([0, "x", exit_code]) + "\n")
    with pytest.raises(ValueError):
        scan_recording(path)


def test_sanitizing_trims_startup_without_fabricating_output_or_event_intervals(
    tmp_path: Path,
) -> None:
    raw, cast = tmp_path / "raw.cast", tmp_path / "demo.cast"
    original = [
        {"version": 2, "command": "demo", "env": {"SHELL": "/bin/sh"}},
        [0.8, "o", "Queued: 2"],
        [2.8, "o", "Batch received"],
        [3, "x", "0"],
    ]
    raw.write_text("\n".join(json.dumps(value) for value in original) + "\n")
    before = raw.read_bytes()
    sanitize_recording(raw, cast)
    result = [json.loads(line) for line in cast.read_text().splitlines()]
    assert result[1:] == [[0, "o", "Queued: 2"], [2, "o", "Batch received"]]
    assert "command" not in result[0]
    assert result[0]["env"] == {"TERM": "xterm-256color"}
    assert raw.read_bytes() == before
