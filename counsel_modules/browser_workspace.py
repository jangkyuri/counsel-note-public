from __future__ import annotations

import base64
import hmac
import re
import secrets
import time
from typing import Final
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.requests import HTTPConnection
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from counsel_modules.admin_workspace import ASSERTION_HEADER, MAX_ASSERTION_SECONDS

COOKIE_NAME: Final = "__Host-connect-log-browser-8511"
COOKIE_MAX_AGE: Final = 30 * 24 * 60 * 60
COOKIE_PATTERN: Final = re.compile(r"v1\.([0-9]{1,12})\.([0-9a-f]{64})\.([0-9a-f]{64})")
ASSERTION_HEADER_BYTES: Final = ASSERTION_HEADER.lower().encode("ascii")


class BrowserWorkspaceMiddleware:
    """Map a first-party browser cookie to the app's existing signed workspace handoff."""

    def __init__(self, app: ASGIApp, *, secret: str, origin: str) -> None:
        self.app = app
        self.secret = secret.encode("utf-8")
        self.origin = origin
        self.host = urlsplit(origin).netloc
        if len(self.secret) < 32:
            raise RuntimeError("The browser preview requires a dedicated workspace secret (32+ bytes)")

    def _mac(self, payload: str) -> str:
        return hmac.digest(self.secret, payload.encode("ascii"), "sha256").hex()

    def _profile_id(self, cookie: str, now: int) -> str:
        match = COOKIE_PATTERN.fullmatch(cookie)
        if match is None:
            return ""
        expiry, profile_id, signature = match.groups()
        expected = self._mac(f"browser-cookie:v1.{expiry}.{profile_id}")
        if not hmac.compare_digest(signature, expected) or not now < int(expiry) <= now + COOKIE_MAX_AGE:
            return ""
        return profile_id

    def _new_cookie(self, now: int) -> bytes:
        payload = f"v1.{now + COOKIE_MAX_AGE}.{secrets.token_hex(32)}"
        response = Response()
        response.set_cookie(
            COOKIE_NAME, f"{payload}.{self._mac('browser-cookie:' + payload)}",
            secure=True, httponly=True, samesite="lax", max_age=COOKIE_MAX_AGE,
        )
        return next(value for key, value in response.raw_headers if key == b"set-cookie")

    def _assertion(self, profile_id: str, now: int) -> bytes:
        principal = base64.urlsafe_b64encode(f"browser-profile-v1:{profile_id}".encode("ascii"))
        payload = f"v1.{now + MAX_ASSERTION_SECONDS}.{principal.rstrip(b'=').decode('ascii')}"
        return f"{payload}.{self._mac(payload)}".encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        websocket = scope["type"] == "websocket"
        if not websocket and scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        host_ok = headers.getlist("host") == [self.host]
        now = int(time.time())
        profile_id = self._profile_id(HTTPConnection(scope).cookies.get(COOKIE_NAME, ""), now)
        if websocket:
            if not host_ok or headers.getlist("origin") != [self.origin] or not profile_id:
                await send({"type": "websocket.close", "code": 1008})
                return
        elif not host_ok:
            await PlainTextResponse("Invalid preview host", status_code=400)(scope, receive, send)
            return

        forwarded = [(key, value) for key, value in scope["headers"] if key.lower() != ASSERTION_HEADER_BYTES]
        if profile_id:
            forwarded.append((ASSERTION_HEADER_BYTES, self._assertion(profile_id, now)))
        if websocket or scope["method"] != "GET" or scope["path"] != "/":
            await self.app({**scope, "headers": forwarded}, receive, send)
            return

        # The cached HTML must never bypass cookie bootstrap after expiration or key rotation.
        forwarded = [(key, value) for key, value in forwarded if key not in {b"if-none-match", b"if-modified-since"}]

        async def serve_document(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = [(k, v) for k, v in message.get("headers", ()) if k != b"cache-control"]
                response_headers.append((b"cache-control", b"no-store"))
                if not profile_id and message["status"] == 200:
                    response_headers.append((b"set-cookie", self._new_cookie(now)))
                message = {
                    **message,
                    "headers": response_headers,
                }
            await send(message)

        await self.app({**scope, "headers": forwarded}, receive, serve_document)
