from collections.abc import Iterator

import pytest
import requests

from counsel_modules import docs_qa_auth

ENDPOINT = "https://script.google.com/macros/s/test-deployment/exec"
DOC_ID = "synthetic-test-doc"
RESULT_URL = "https://script.googleusercontent.com/macros/echo?user_content_key=test"


def response(status: int, location: str = "") -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result.headers.update({"Content-Type": "application/json"})
    if location:
        result.headers["Location"] = location
    return result


@pytest.fixture(autouse=True)
def configure_qa(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CONNECT_LOG_DOCS_QA_URL", ENDPOINT)
    monkeypatch.setenv("CONNECT_LOG_DOCS_QA_ID", DOC_ID)
    monkeypatch.setenv("CONNECT_LOG_DOCS_QA_BEARER_TOKEN", "synthetic-token")
    yield


@pytest.mark.parametrize("verify_only", [False, True])
def test_qa_token_authenticates_both_save_and_verify_without_forwarding_token(
    monkeypatch: pytest.MonkeyPatch, verify_only: bool,
) -> None:
    def post(url: str, **kwargs: dict) -> requests.Response:
        assert url == ENDPOINT
        assert kwargs["headers"]["Authorization"] == "Bearer synthetic-token"
        assert kwargs["json"]["verify_only"] is verify_only
        assert not kwargs["allow_redirects"]
        return response(302, RESULT_URL)

    def get(url: str, **kwargs: dict) -> requests.Response:
        assert url == RESULT_URL
        assert "headers" not in kwargs and not kwargs["allow_redirects"]
        return response(200)

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    result = docs_qa_auth.post_docs(
        ENDPOINT, json={"doc_id": DOC_ID, "verify_only": verify_only}, headers={}, timeout=10,
    )
    assert result.status_code == 200


@pytest.mark.parametrize(("url", "doc_id"), [
    (ENDPOINT.replace("test-deployment", "other-deployment"), DOC_ID),
    (ENDPOINT, "different-doc"),
])
def test_qa_refuses_any_other_target_before_network(
    monkeypatch: pytest.MonkeyPatch, url: str, doc_id: str,
) -> None:
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: pytest.fail("unexpected POST"))
    with pytest.raises(docs_qa_auth.DocsQaAuthError):
        docs_qa_auth.post_docs(url, json={"doc_id": doc_id}, headers={}, timeout=10)


def test_qa_missing_token_never_sends_anonymous_post(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONNECT_LOG_DOCS_QA_BEARER_TOKEN")
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: pytest.fail("unexpected POST"))
    with pytest.raises(docs_qa_auth.DocsQaAuthError):
        docs_qa_auth.post_docs(ENDPOINT, json={"doc_id": DOC_ID}, headers={}, timeout=10)


def test_qa_rejects_sign_in_redirect_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: response(302, "https://accounts.google.com"))
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: pytest.fail("unexpected redirect"))
    with pytest.raises(docs_qa_auth.DocsQaAuthError, match="Google OAuth"):
        docs_qa_auth.post_docs(ENDPOINT, json={"doc_id": DOC_ID}, headers={}, timeout=10)


def test_unconfigured_app_keeps_its_existing_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    for suffix in ("URL", "ID", "BEARER_TOKEN"):
        monkeypatch.delenv("CONNECT_LOG_DOCS_QA_" + suffix)

    def post(url: str, **kwargs: dict) -> requests.Response:
        assert kwargs == {"json": {"doc_id": DOC_ID}, "headers": {}, "timeout": 10}
        return response(200)

    monkeypatch.setattr(requests, "post", post)
    assert docs_qa_auth.post_docs(ENDPOINT, json={"doc_id": DOC_ID}, headers={}, timeout=10).ok
