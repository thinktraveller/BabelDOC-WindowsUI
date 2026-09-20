"""连接测试分类的离线单元测试（不发起真实请求）。"""

from __future__ import annotations

import pytest

from babeldoc_workbench.services import connection_test


class AuthenticationError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


class APIConnectionError(Exception):
    pass


class APITimeoutError(Exception):
    pass


class NotFoundError(Exception):
    pass


class BadRequestError(Exception):
    pass


class RateLimitError(Exception):
    pass


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (AuthenticationError("bad key"), connection_test.CATEGORY_AUTH_FAILED),
        (PermissionDeniedError("forbidden"), connection_test.CATEGORY_AUTH_FAILED),
        (APIConnectionError("dns"), connection_test.CATEGORY_UNREACHABLE),
        (APITimeoutError("slow"), connection_test.CATEGORY_TIMEOUT),
        (NotFoundError("model"), connection_test.CATEGORY_MODEL_NOT_FOUND),
        (BadRequestError("temperature"), connection_test.CATEGORY_INVALID_REQUEST),
        (RateLimitError("429"), connection_test.CATEGORY_RATE_LIMITED),
        (ValueError("weird"), connection_test.CATEGORY_UNKNOWN),
    ],
)
def test_classify_exception(exception: Exception, expected: str) -> None:
    category, message = connection_test.classify_exception(exception)
    assert category == expected
    assert message == connection_test.CATEGORY_MESSAGES[expected]


def test_test_connection_requires_key() -> None:
    result = connection_test.test_connection(
        base_url="https://example.test/v1", model="demo", api_key=None
    )
    assert result["ok"] is False
    assert result["category"] == connection_test.CATEGORY_MISSING_KEY


def test_test_connection_success(monkeypatch) -> None:
    class Completions:
        @staticmethod
        def create(**_kwargs):
            return {"choices": []}

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    monkeypatch.setattr(
        connection_test, "build_client", lambda base_url, api_key, timeout: Client()
    )
    result = connection_test.test_connection(
        base_url="https://example.test/v1", model="demo", api_key="sk-fake-key"
    )
    assert result["ok"] is True
    assert result["category"] == connection_test.CATEGORY_SUCCESS


def test_test_connection_classifies_errors(monkeypatch) -> None:
    class Completions:
        @staticmethod
        def create(**_kwargs):
            raise AuthenticationError("bad key")

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    monkeypatch.setattr(
        connection_test, "build_client", lambda base_url, api_key, timeout: Client()
    )
    result = connection_test.test_connection(
        base_url="https://example.test/v1", model="demo", api_key="sk-fake-key"
    )
    assert result["ok"] is False
    assert result["category"] == connection_test.CATEGORY_AUTH_FAILED
    assert "detail" in result
