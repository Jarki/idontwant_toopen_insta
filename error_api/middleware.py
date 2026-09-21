"""ASGI middleware enforcing the private API request boundary."""

from __future__ import annotations

import asyncio
import logging
import re

from starlette.authentication import AuthCredentials
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_BODY_READ_TIMEOUT_SECONDS = 2.0
MAX_MUTATION_BODY_BYTES = 32_768
_MUTATION_PATH = re.compile(r"/v1/errors/[^/]+(?:/notes)?\Z")
_LOGGER = logging.getLogger("error_api")


class InternalErrorMiddleware:
    """Contain unexpected failures without exposing request or database details."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        response_started = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, receive, tracked_send)
        except Exception:
            _LOGGER.error("Error API request failed")
            if response_started:
                raise
            response = JSONResponse(
                status_code=500,
                content={"detail": "internal server error"},
            )
            await response(scope, receive, send)


class MutationBodyLimitMiddleware:
    """Authorize mutations before reading and bound their request bodies."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int = MAX_MUTATION_BODY_BYTES,
        body_timeout_seconds: float = DEFAULT_BODY_READ_TIMEOUT_SECONDS,
    ) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes
        self._body_timeout_seconds = body_timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_mutation(scope):
            await self._app(scope, receive, send)
            return

        auth = scope.get("auth")
        scopes = auth.scopes if isinstance(auth, AuthCredentials) else []
        if "authenticated" not in scopes:
            await self._reject(scope, receive, send, 401, "invalid credentials", True)
            return
        if "triage" not in scopes:
            await self._reject(scope, receive, send, 403, "insufficient scope")
            return

        for name, value in scope["headers"]:
            if name.lower() != b"content-length":
                continue
            try:
                declared = int(value)
            except ValueError:
                continue
            if declared > self._max_body_bytes:
                await self._reject(scope, receive, send, 413, "request body too large")
                return

        body = bytearray()
        disconnected = False
        try:
            async with asyncio.timeout(self._body_timeout_seconds):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        disconnected = True
                        break
                    if message["type"] != "http.request":
                        continue
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > self._max_body_bytes:
                        await self._reject(
                            scope, receive, send, 413, "request body too large"
                        )
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await self._reject(scope, receive, send, 408, "request body timed out")
            return

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                if disconnected:
                    return {"type": "http.disconnect"}
                return {
                    "type": "http.request",
                    "body": bytes(body),
                    "more_body": False,
                }
            return {"type": "http.request", "body": b"", "more_body": False}

        await self._app(scope, replay_receive, send)

    @staticmethod
    def _is_mutation(scope: Scope) -> bool:
        method = scope["method"]
        path = scope["path"]
        is_note = path.endswith("/notes")
        return _MUTATION_PATH.fullmatch(path) is not None and (
            (method == "PATCH" and not is_note) or (method == "POST" and is_note)
        )

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        detail: str,
        authenticate: bool = False,
    ) -> None:
        headers = {"WWW-Authenticate": "Bearer"} if authenticate else None
        response = JSONResponse(
            status_code=status_code,
            content={"detail": detail},
            headers=headers,
        )
        await response(scope, receive, send)
