"""会话令牌、来源校验与凭据封装的离线单元测试。"""

from __future__ import annotations

import pytest

from babeldoc_workbench import security


def test_generate_session_token_is_long_and_unique() -> None:
    first = security.generate_session_token()
    second = security.generate_session_token()
    assert first != second
    assert len(first) >= 32


def test_tokens_equal_uses_constant_time_compare() -> None:
    assert security.tokens_equal("abc", "abc")
    assert not security.tokens_equal("abc", "abd")
    assert not security.tokens_equal(None, "abc")
    assert not security.tokens_equal("abc", "")


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1:8123", True),
        ("localhost:8123", True),
        ("[::1]:8123", True),
        ("127.0.0.1", True),
        ("127.0.0.1:9999", False),
        ("evil.example.com:8123", False),
        ("192.168.1.10:8123", False),
        (None, False),
        ("", False),
    ],
)
def test_check_host(host: str | None, expected: bool) -> None:
    assert security.check_host(host, 8123) is expected


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("http://127.0.0.1:8123", True),
        ("http://localhost:8123", True),
        (None, True),  # 非浏览器请求（curl、测试）
        ("http://127.0.0.1:9999", False),
        ("https://evil.example.com", False),
        ("http://evil.example.com:8123", False),
    ],
)
def test_check_origin(origin: str | None, expected: bool) -> None:
    assert security.check_origin(origin, 8123) is expected


def test_save_key_reports_credential_store_error(monkeypatch) -> None:
    class BrokenKeyring:
        def set_password(self, *_args, **_kwargs):
            raise RuntimeError("no backend")

    monkeypatch.setattr(security, "_import_keyring", lambda: BrokenKeyring())
    with pytest.raises(security.CredentialStoreError):
        security.save_key("profile-1", "sk-test-value")


def test_load_key_returns_none_when_missing(monkeypatch) -> None:
    class EmptyKeyring:
        def get_password(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(security, "_import_keyring", lambda: EmptyKeyring())
    assert security.load_key("profile-1") is None


def test_credentials_available_reports_backend(monkeypatch) -> None:
    class FakeKeyring:
        class _Backend:
            name = "fake-backend"

        @staticmethod
        def get_keyring():
            return FakeKeyring._Backend()

    monkeypatch.setattr(security, "_import_keyring", lambda: FakeKeyring())
    available, detail = security.credentials_available()
    assert available is True
    assert detail == "fake-backend"


def test_credentials_available_reports_failure(monkeypatch) -> None:
    def boom():
        raise RuntimeError("no keyring")

    monkeypatch.setattr(security, "_import_keyring", boom)
    available, detail = security.credentials_available()
    assert available is False
    assert "no keyring" in (detail or "")
