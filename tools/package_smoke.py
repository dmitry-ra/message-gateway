"""Install the built wheel outside the checkout and exercise packaged resources."""

import json
import socket
import subprocess
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    wheel = next((root / "dist").glob("message_gateway-" + version + "-*.whl"))
    with tarfile.open(root / "dist" / ("message_gateway-" + version + ".tar.gz")) as archive:
        assert any(
            name.endswith("plugins/message-gateway/skills/gateway-message/SKILL.md")
            for name in archive.getnames()
        )
    with tempfile.TemporaryDirectory(prefix="gateway-wheel-") as directory:
        work = Path(directory)
        subprocess.run(["uv", "venv", str(work / "env")], check=True)
        python = work / "env/bin/python"
        subprocess.run(["uv", "pip", "install", "--python", str(python), str(wheel)], check=True)
        result = subprocess.run(
            [str(python), "-m", "message_gateway", "--version"],
            cwd=work,
            check=True,
            text=True,
            capture_output=True,
        )
        assert result.stdout.strip() == version
        subprocess.run(
            [
                str(python),
                "-c",
                "from importlib.resources import files; import json; assert json.loads(files('message_gateway.interfaces').joinpath('openapi.json').read_text())['openapi'] == '3.1.0'",
            ],
            cwd=work,
            check=True,
        )
        configuration = work / "gateway.toml"
        configuration.write_text(
            '[[targets]]\nname="work"\nthread="test"\nsocket="' + str(work / "absent.sock") + '"\n'
        )
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen(
            [
                str(python),
                "-m",
                "message_gateway",
                "serve",
                "--config",
                str(configuration),
                "--port",
                str(port),
            ],
            cwd=work,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            url = "http://127.0.0.1:" + str(port) + "/v1/targets"
            deadline = time.monotonic() + 10
            while True:
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        assert "work" in json.load(response)
                    break
                except urllib.error.URLError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            request = urllib.request.Request(
                url + "/work/messages",
                data=json.dumps({"channel": "smoke", "content": {"build": 42}}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                assert response.status == 202
                assert json.load(response)["content"] == {"build": 42}
            subprocess.run(
                [
                    str(python),
                    "-c",
                    """
import asyncio, sys
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
async def main():
    async with streamable_http_client(sys.argv[1] + '/mcp') as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            assert len((await client.list_tools()).tools) == 2
            result = await client.call_tool('send_message', {'target':'work','channel':'wheel','content':{'build':43}})
            assert not result.is_error
            assert result.structured_content['content'] == {'build':43}
asyncio.run(main())
""",
                    "http://127.0.0.1:" + str(port),
                ],
                cwd=work,
                check=True,
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--reinstall",
                "--no-deps",
                "--python",
                str(python),
                str(root / "dist" / ("message_gateway-" + version + ".tar.gz")),
            ],
            check=True,
        )
        subprocess.run([str(python), "-m", "message_gateway", "--version"], cwd=work, check=True)


if __name__ == "__main__":
    main()
