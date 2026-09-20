"""API 连接测试：把失败原因分类为可理解的类别。"""

from __future__ import annotations

from typing import Any

CATEGORY_SUCCESS = "success"
CATEGORY_AUTH_FAILED = "auth_failed"
CATEGORY_UNREACHABLE = "unreachable"
CATEGORY_TIMEOUT = "timeout"
CATEGORY_MODEL_NOT_FOUND = "model_not_found"
CATEGORY_INVALID_REQUEST = "invalid_request"
CATEGORY_RATE_LIMITED = "rate_limited"
CATEGORY_MISSING_KEY = "missing_key"
CATEGORY_UNKNOWN = "unknown"

DEFAULT_TIMEOUT_SECONDS = 15.0

_NAME_TO_CATEGORY = {
    "AuthenticationError": CATEGORY_AUTH_FAILED,
    "PermissionDeniedError": CATEGORY_AUTH_FAILED,
    "APIConnectionError": CATEGORY_UNREACHABLE,
    "ConnectError": CATEGORY_UNREACHABLE,
    "ConnectTimeout": CATEGORY_TIMEOUT,
    "APITimeoutError": CATEGORY_TIMEOUT,
    "ReadTimeout": CATEGORY_TIMEOUT,
    "TimeoutException": CATEGORY_TIMEOUT,
    "NotFoundError": CATEGORY_MODEL_NOT_FOUND,
    "RateLimitError": CATEGORY_RATE_LIMITED,
    "BadRequestError": CATEGORY_INVALID_REQUEST,
    "UnprocessableEntityError": CATEGORY_INVALID_REQUEST,
}

CATEGORY_MESSAGES = {
    CATEGORY_SUCCESS: "连接成功",
    CATEGORY_AUTH_FAILED: "认证失败：请检查 API Key 是否正确、是否已过期",
    CATEGORY_UNREACHABLE: "地址不可达：请检查 Base URL、网络与代理设置",
    CATEGORY_TIMEOUT: "请求超时：服务可能较慢或网络不稳定，可稍后重试",
    CATEGORY_MODEL_NOT_FOUND: "模型不存在：请检查模型名是否在该服务上可用",
    CATEGORY_INVALID_REQUEST: "请求被拒绝：可能是该模型不支持所选参数（如温度或推理参数）",
    CATEGORY_RATE_LIMITED: "触发限流：请降低 QPS 或稍后重试",
    CATEGORY_MISSING_KEY: "尚未保存 API Key",
    CATEGORY_UNKNOWN: "未知错误：请查看技术详情",
}


def classify_exception(exc: BaseException) -> tuple[str, str]:
    """按异常类型名分类（不依赖 openai 的具体类，便于测试与版本兼容）。"""
    for klass in type(exc).__mro__:
        category = _NAME_TO_CATEGORY.get(klass.__name__)
        if category:
            return category, CATEGORY_MESSAGES[category]
    return CATEGORY_UNKNOWN, CATEGORY_MESSAGES[CATEGORY_UNKNOWN]


def build_client(base_url: str | None, api_key: str, timeout: float):
    """构造仅用于连接测试的客户端：不重试，避免界面长时间等待。"""
    import openai

    return openai.OpenAI(
        base_url=base_url or None,
        api_key=api_key,
        timeout=timeout,
        max_retries=0,
    )


def test_connection(
    *,
    base_url: str | None,
    model: str,
    api_key: str | None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """发起一次极小的对话补全请求，返回分类结果。"""
    if not api_key:
        return {
            "ok": False,
            "category": CATEGORY_MISSING_KEY,
            "message": CATEGORY_MESSAGES[CATEGORY_MISSING_KEY],
            "model": model,
        }
    try:
        client = build_client(base_url, api_key, timeout)
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
    except Exception as exc:  # noqa: BLE001 - 所有失败都要分类给用户看
        category, message = classify_exception(exc)
        return {
            "ok": False,
            "category": category,
            "message": message,
            "detail": f"{type(exc).__name__}: {exc}",
            "model": model,
        }
    return {
        "ok": True,
        "category": CATEGORY_SUCCESS,
        "message": CATEGORY_MESSAGES[CATEGORY_SUCCESS],
        "model": model,
    }
