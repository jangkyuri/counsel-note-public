from __future__ import annotations

from http.cookies import SimpleCookie
from pathlib import Path

import anyio
from starlette.types import Message, Receive, Scope, Send

from counsel_modules.admin_workspace import ASSERTION_HEADER, verified_workspace
from counsel_modules.browser_workspace import BrowserWorkspaceMiddleware

SECRET = "synthetic-only-workspace-cookie-secret"
HOST = b"127.0.0.1:8511"
ORIGIN = b"http://" + HOST
ASSERTION = ASSERTION_HEADER.lower().encode("ascii")


def exchange(
    *, websocket: bool = False, cookie: str = "", origin: bytes = ORIGIN, host: bytes = HOST,
    cached: bool = False,
) -> tuple[list[Message], list[str]]:
    messages: list[Message] = []
    forwarded: list[str] = []

    async def downstream(scope: Scope, _receive: Receive, send: Send) -> None:
        forwarded.extend(value.decode() for key, value in scope["headers"] if key == ASSERTION)
        if scope["type"] == "http":
            status = 304 if any(key == b"if-none-match" for key, _ in scope["headers"]) else 200
            await send({"type": "http.response.start", "status": status, "headers": []})
            await send({"type": "http.response.body", "body": b""})
        else:
            await send({"type": "websocket.accept"})

    async def send(message: Message) -> None:
        messages.append(message)

    async def receive() -> Message:
        return {"type": "websocket.connect"} if websocket else {"type": "http.request", "body": b""}

    scope: Scope = {
        "type": "websocket" if websocket else "http", "method": "GET",
        "path": "/_stcore/stream" if websocket else "/", "scheme": "ws" if websocket else "http",
        "headers": [
            (b"host", host), (b"origin", origin), (b"cookie", cookie.encode("ascii")),
            (ASSERTION, b"not-a-trusted-assertion"),
        ],
    }
    if cached:
        scope["headers"].append((b"if-none-match", b'"cached"'))
    app = BrowserWorkspaceMiddleware(downstream, secret=SECRET, origin=ORIGIN.decode())
    anyio.run(app, scope, receive, send)
    return messages, forwarded


def initial_cookie() -> str:
    messages, forwarded = exchange()
    assert not forwarded
    values = [value.decode() for key, value in messages[0]["headers"] if key == b"set-cookie"]
    [header] = values
    cookies: SimpleCookie[str] = SimpleCookie(header)
    [(name, morsel)] = list(cookies.items())
    assert morsel["httponly"] and morsel["secure"] and morsel["samesite"] == "lax"
    assert morsel["path"] == "/" and not morsel["domain"] and morsel["max-age"]
    return f"{name}={morsel.value}"


def test_separate_browser_cookies_keep_stable_workspaces_after_reload(tmp_path: Path) -> None:
    first, second = initial_cookie(), initial_cookie()
    _, a = exchange(websocket=True, cookie=first)
    _, b = exchange(websocket=True, cookie=second)
    refresh, _ = exchange(cookie=first)
    assert not any(key == b"set-cookie" for key, _ in refresh[0]["headers"])
    _, a_reload = exchange(websocket=True, cookie=first)
    workspace_a, workspace_b, restored = (
        verified_workspace(tmp_path, headers, SECRET) for headers in (a, b, a_reload)
    )
    assert workspace_a == restored
    assert workspace_a.root != workspace_b.root


def test_unsigned_altered_cross_origin_or_wrong_host_request_cannot_select_a_workspace() -> None:
    cookie = initial_cookie()
    for arguments in (
        {},
        {"cookie": cookie[:-1] + ("0" if cookie[-1] != "0" else "1")},
        {"cookie": cookie, "origin": b"https://example.invalid"},
        {"cookie": cookie, "host": b"example.invalid"},
    ):
        messages, forwarded = exchange(websocket=True, **arguments)
        assert messages == [{"type": "websocket.close", "code": 1008}]
        assert not forwarded


def test_cached_document_can_bootstrap_again_when_cookie_no_longer_valid() -> None:
    messages, _ = exchange(cookie=initial_cookie() + "altered", cached=True)
    assert messages[0]["status"] == 200
    assert (b"cache-control", b"no-store") in messages[0]["headers"]
    assert any(key == b"set-cookie" for key, _ in messages[0]["headers"])
