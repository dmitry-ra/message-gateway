import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiohttp
import uvicorn
from starlette.types import ASGIApp

from message_gateway.core.models import Message, Readiness


class FakeAdapter:
    def __init__(self, state: Readiness = Readiness.READY) -> None:
        self.state = state
        self.batches: list[list[Message]] = []
        self.error: Exception | None = None
        self.read_error: Exception | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.closed = False

    async def readiness(self) -> Readiness:
        if self.read_error:
            raise self.read_error
        return self.state

    async def send(self, messages: list[Message]) -> str:
        self.entered.set()
        await self.release.wait()
        if self.error:
            raise self.error
        self.batches.append(messages)
        return "receipt"

    async def close(self) -> None:
        self.closed = True


@asynccontextmanager
async def running(app: ASGIApp, port: int = 0) -> AsyncIterator[str]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", port))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="critical"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    task.result()
                    raise RuntimeError("Test server did not start")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


@asynccontextmanager
async def http_client(app: ASGIApp) -> AsyncIterator[aiohttp.ClientSession]:
    async with running(app) as url:
        async with aiohttp.ClientSession(base_url=url) as client:
            yield client
