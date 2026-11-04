"""Command-line entry point."""

import argparse
import asyncio
import logging
import os
import re
import tomllib
from pathlib import Path

import uvicorn

from message_gateway import __version__
from message_gateway.adapters.codex import CodexAdapter
from message_gateway.core.service import Service, Target
from message_gateway.interfaces.server import create_app


def load_service(path: Path) -> Service:
    configuration = tomllib.loads(path.read_text())
    targets: dict[str, Target] = {}
    for item in configuration.get("targets", []):
        name, thread = item["name"], item["thread"]
        batching = item.get("batching", True)
        effort = item.get("effort")
        if effort is not None and effort not in (
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
            "ultra",
        ):
            raise ValueError("Invalid reasoning effort")
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) is None
            or name in targets
        ):
            raise ValueError("Target names must be unique, 1-64 ASCII letters, digits, '_' or '-'")
        if not isinstance(thread, str) or not thread or not isinstance(batching, bool):
            raise ValueError("Thread must be a string and batching a boolean")
        socket = Path(
            item.get("socket", "~/.codex/app-server-control/app-server-control.sock")
        ).expanduser()
        targets[name] = Target(CodexAdapter(thread, socket, effort=effort), batching=batching)
    return Service(targets)


async def serve(configuration: Path, port: int) -> None:
    service = load_service(configuration)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(service, os.environ.get("MESSAGE_GATEWAY_TOKEN")),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    tasks = [asyncio.create_task(service.run()), asyncio.create_task(server.serve())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        server.should_exit = True
        tasks[0].cancel()
        try:
            await asyncio.wait_for(asyncio.shield(tasks[1]), 5)
        except TimeoutError:
            tasks[1].cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Route and batch messages in memory")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    server = commands.add_parser("serve", help="Run the loopback HTTP gateway")
    server.add_argument("--config", type=Path, required=True)
    server.add_argument("--port", type=int, default=10880)
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(serve(args.config, args.port))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    except KeyboardInterrupt:
        pass
