# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["streamlit>=1.63,<2", "requests", "send2trash", "pyperclip", "imageio-ffmpeg"]
# ///
# Run from the preview checkout: python -B -m scripts.run_browser_workspace_preview
from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
from starlette.middleware import Middleware

from counsel_modules.browser_workspace import BrowserWorkspaceMiddleware

ADDRESS = "127.0.0.1"
PORT = 8511


def run_preview(script: Path) -> None:
    """Use browser workspaces only on the dedicated loopback preview instance."""
    secret = os.environ.get("CONNECT_LOG_BROWSER_WORKSPACE_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise SystemExit("Set a dedicated CONNECT_LOG_BROWSER_WORKSPACE_SECRET (at least 32 bytes).")
    os.environ["CONNECT_LOG_MULTI_USER"] = "1"
    os.environ["CONNECT_LOG_ADMIN_PROXY_SECRET"] = secret
    st.App(
        script.resolve(), middleware=[
            Middleware(BrowserWorkspaceMiddleware, secret=secret, origin=f"http://{ADDRESS}:{PORT}"),
        ],
    ).run(config={
        "server.address": ADDRESS,
        "server.port": PORT,
        "server.baseUrlPath": "",
        "server.headless": True,
        "server.fileWatcherType": "none",
        "server.enableXsrfProtection": True,
        "server.enableCORS": True,
        "browser.gatherUsageStats": False,
    })


if __name__ == "__main__":
    run_preview(Path(__file__).resolve().parents[1] / "app.py")
