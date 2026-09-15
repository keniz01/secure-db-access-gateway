import os

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BodySizeLimitMiddleware:
    """
    Reject oversize /graphql request bodies before they are buffered.

    Starlette/FastAPI buffer the whole request body in memory via the ASGI
    receive channel, so an unbounded multi-megabyte POST is a cheap memory
    exhaustion vector. This middleware drains at most ``max_bytes`` bytes and
    aborts with ``413`` as soon as the client exceeds the budget.
    """

    def __init__(self, app: ASGIApp, max_bytes: int | None = None) -> None:
        self.app = app
        self.max_bytes = max_bytes or int(os.getenv("GRAPHQL_MAX_BODY_BYTES", str(1024 * 1024)))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Enforce the body budget on GraphQL POSTs before downstream handling."""
        if scope["type"] != "http" or not scope.get("path", "").startswith("/graphql"):
            await self.app(scope, receive, send)
            return

        announced_length = self._content_length(scope)
        if announced_length is not None and announced_length > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body") or b"")
            if len(body) > self.max_bytes:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body"):
                break

        payload = bytes(body)

        # Replay the drained body to the application from a cached channel.
        sent = False

        async def replay_receive() -> dict:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return None
        return None

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": "Request body exceeds the maximum allowed size."},
        )
        await response(scope, receive, send)
