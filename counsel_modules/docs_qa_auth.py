from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Final
from urllib.parse import urlsplit

import requests

QA_URL: Final = re.compile(r"https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec")


class DocsQaAuthError(RuntimeError):
    """Safe message for errors in the opt-in test Docs connection."""


def post_docs(
    url: str,
    *,
    json: Mapping[str, str | bool],
    headers: Mapping[str, str],
    timeout: float,
) -> requests.Response:
    """Leave the ordinary transport untouched unless test authentication is configured."""
    qa_url = os.environ.get("CONNECT_LOG_DOCS_QA_URL", "").strip()
    qa_doc_id = os.environ.get("CONNECT_LOG_DOCS_QA_ID", "").strip()
    token = os.environ.get("CONNECT_LOG_DOCS_QA_BEARER_TOKEN", "").strip()
    if not (qa_url or qa_doc_id or token):
        return requests.post(url, json=json, headers=headers, timeout=timeout)

    if not qa_doc_id or not QA_URL.fullmatch(qa_url) or url != qa_url or json.get("doc_id") != qa_doc_id:
        raise DocsQaAuthError("테스트용 Google Docs는 지정한 배포 주소·문서에만 연결할 수 있습니다.")
    if not token or not token.isascii() or any(char.isspace() for char in token):
        raise DocsQaAuthError("승인된 Google OAuth 액세스 토큰이 테스트 앱에 설정되지 않았습니다.")

    try:
        response = requests.post(
            url, json=json, headers={**headers, "Authorization": f"Bearer {token}"},
            timeout=timeout, allow_redirects=False,
        )
    except requests.Timeout:
        raise requests.ReadTimeout("Google Docs 테스트 저장의 응답을 받지 못했습니다.") from None
    except requests.RequestException:
        raise DocsQaAuthError("Google Docs 테스트 배포에 연결할 수 없습니다.") from None

    if response.status_code in (302, 303):
        # Content Service moves the response, not the original request or its bearer token.
        location = response.headers.get("Location", "")
        target = urlsplit(location)
        if target.netloc == "accounts.google.com":
            raise DocsQaAuthError("테스트 배포가 Google OAuth 인증을 요구합니다.")
        if (target.scheme, target.netloc, target.path) != ("https", "script.googleusercontent.com", "/macros/echo"):
            raise requests.ReadTimeout("Google Docs 테스트 저장의 결과 주소를 확인할 수 없습니다.")
        try:
            response = requests.get(location, timeout=timeout, allow_redirects=False)
        except requests.RequestException:
            raise requests.ReadTimeout("Google Docs 테스트 저장 결과를 읽지 못했습니다.") from None
    if response.status_code in (401, 403):
        raise DocsQaAuthError(f"테스트 배포가 Google OAuth 인증을 거부했습니다 (HTTP {response.status_code}).")
    if "application/json" not in response.headers.get("Content-Type", "").lower():
        raise requests.ReadTimeout("Google Docs 테스트 저장 결과를 JSON으로 확인할 수 없습니다.")
    return response
