import asyncio
import sys
from pathlib import Path

import pytest

from message_gateway.cli import load_service, main, serve
from message_gateway.core.service import Service, Target
from tests.helpers import FakeAdapter


def test_configuration(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[[targets]]\nname="work"\nthread="thread"\nbatching=false\n')
    service = load_service(path)
    assert not service.targets["work"].batching
    for config in [
        "",
        '[[targets]]\nname=""\nthread="x"',
        '[[targets]]\nname="bad/name"\nthread="x"',
        '[[targets]]\nname="w"\nthread=1',
        '[[targets]]\nname="w"\nthread="x"\nbatching="yes"',
        '[[targets]]\nname="w"\nthread="x"\n[[targets]]\nname="w"\nthread="y"',
    ]:
        path.write_text(config)
        with pytest.raises(ValueError):
            load_service(path)


def test_serve_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = FakeAdapter()
    service = Service({"work": Target(adapter)})

    async def finish() -> None:
        service.submit("work", "feed", "x")
        await service.tick()
        await adapter.close()

    monkeypatch.setattr(service, "run", finish)
    monkeypatch.setattr("message_gateway.cli.load_service", lambda path: service)
    asyncio.run(serve(tmp_path / "unused", 0))
    assert adapter.batches and adapter.closed


def test_serve_invalid_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys, "argv", ["message-gateway", "serve", "--config", str(tmp_path / "missing")]
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
