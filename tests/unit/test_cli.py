import subprocess
import sys

import pytest

from message_gateway import __version__
from message_gateway.cli import main


def test_cli(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["message-gateway"])
    main()
    assert "Route and batch" in capsys.readouterr().out
    result = subprocess.run(
        [sys.executable, "-m", "message_gateway", "--version"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert result.stdout.strip() == __version__
    assert (
        subprocess.run(
            [sys.executable, "-m", "message_gateway", "--invalid"], capture_output=True
        ).returncode
        == 2
    )
