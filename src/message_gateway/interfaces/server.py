"""One HTTP/MCP application using the same hosting pattern as the relay."""

import hmac
from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from message_gateway.core.service import Service
from message_gateway.interfaces.http import routes
from message_gateway.interfaces.mcp import create_server
from message_gateway.interfaces.operations import Operations


class Security:
    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self.app, self.token = app, token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        if self.token is not None and not hmac.compare_digest(
            headers.get(b"authorization", b""), ("Bearer " + self.token).encode()
        ):
            await JSONResponse({"error": "Unauthorized"}, 401)(scope, receive, send)
            return
        host = headers.get(b"host", b"").decode("latin1")
        origin = headers.get(b"origin")
        if urlsplit("http://" + host).hostname not in ("127.0.0.1", "localhost") or (
            origin is not None
            and origin.decode("latin1") not in ("http://" + host, "https://" + host)
        ):
            await JSONResponse({"error": "Invalid origin"}, 403)(scope, receive, send)
            return
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > 512 * 1024:
                await JSONResponse({"error": "Request body exceeds 512 KiB"}, 413)(
                    scope, receive, send
                )
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        consumed = False

        async def buffered() -> Message:
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, buffered, send)


def create_app(service: Service, token: str | None = None) -> ASGIApp:
    operations = Operations(service)
    app = create_server(operations).streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=512 * 1024,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1:*", "localhost:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )
    app.router.routes.extend(routes(operations))
    return Security(app, token)
