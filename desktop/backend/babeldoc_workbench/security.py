"""会话令牌、来源校验与凭据封装。"""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit

TOKEN_HEADER = "X-Workbench-Token"
KEYRING_SERVICE = "babeldoc-workbench"
ALLOWED_HOSTNAMES = ("127.0.0.1", "localhost", "::1")


class CredentialStoreError(RuntimeError):
    """系统凭据服务不可用；按约定明确报错，不降级为明文落盘。"""


def generate_session_token() -> str:
    """生成一次性会话令牌（进程生命周期内有效）。"""
    return secrets.token_urlsafe(32)


def tokens_equal(candidate: str | None, expected: str) -> bool:
    if not candidate or not expected:
        return False
    return secrets.compare_digest(str(candidate), str(expected))


def _hostname_of(value: str) -> str:
    """从 ``host:port`` 或 ``scheme://host:port`` 中取出主机名。"""
    text = value.strip()
    if not text:
        return ""
    if "://" in text:
        parsed = urlsplit(text)
        return (parsed.hostname or "").lower()
    if text.startswith("["):  # IPv6 字面量
        end = text.find("]")
        return text[1:end].lower() if end > 0 else text.lower()
    return text.split(":", 1)[0].lower()


def _port_of(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    parsed = urlsplit(text) if "://" in text else None
    try:
        if parsed is not None:
            return parsed.port
        if text.startswith("["):
            end = text.find("]")
            rest = text[end + 1 :] if end > 0 else ""
            return int(rest[1:]) if rest.startswith(":") else None
        if text.count(":") == 1:
            return int(text.split(":", 1)[1])
    except ValueError:
        return None
    return None


def check_host(host_header: str | None, port: int) -> bool:
    """Host 头必须指向本机回环地址与当前端口。"""
    if not host_header:
        return False
    hostname = _hostname_of(host_header)
    if hostname not in ALLOWED_HOSTNAMES:
        return False
    header_port = _port_of(host_header)
    return header_port in (None, port)


def check_origin(origin: str | None, port: int) -> bool:
    """浏览器来源必须是本地窗口/页面；没有 Origin 的请求（curl、测试）按非浏览器处理。"""
    if not origin:
        return True
    hostname = _hostname_of(origin)
    if hostname not in ALLOWED_HOSTNAMES:
        return False
    return _port_of(origin) == port


def _import_keyring():
    import keyring

    return keyring


def credentials_available() -> tuple[bool, str | None]:
    """检查系统凭据服务是否可用（不写入任何内容）。"""
    try:
        keyring = _import_keyring()
        backend = keyring.get_keyring()
        return True, str(getattr(backend, "name", backend))
    except Exception as exc:  # noqa: BLE001 - 需要把失败原因交给界面
        return False, f"{type(exc).__name__}: {exc}"


def save_key(ref: str, api_key: str) -> None:
    try:
        _import_keyring().set_password(KEYRING_SERVICE, ref, api_key)
    except Exception as exc:  # noqa: BLE001
        raise CredentialStoreError(
            f"系统凭据服务不可用，无法保存 Key：{type(exc).__name__}: {exc}"
        ) from exc


def load_key(ref: str) -> str | None:
    try:
        return _import_keyring().get_password(KEYRING_SERVICE, ref)
    except Exception as exc:  # noqa: BLE001
        raise CredentialStoreError(
            f"系统凭据服务不可用，无法读取 Key：{type(exc).__name__}: {exc}"
        ) from exc


def delete_key(ref: str) -> None:
    try:
        _import_keyring().delete_password(KEYRING_SERVICE, ref)
    except Exception as exc:  # noqa: BLE001
        raise CredentialStoreError(
            f"系统凭据服务不可用，无法删除 Key：{type(exc).__name__}: {exc}"
        ) from exc
