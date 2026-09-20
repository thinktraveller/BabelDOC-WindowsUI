"""日志脱敏的离线单元测试（使用假值，不涉及真实凭据）。"""

from __future__ import annotations

import logging
from pathlib import Path

from babeldoc_workbench.logging_setup import redact, setup_logging

FAKE_OPENAI_KEY = "sk-test-0123456789abcdefghijklmn"
FAKE_LONG_TOKEN = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"


def test_redact_masks_openai_style_key() -> None:
    text = redact(f"使用 {FAKE_OPENAI_KEY} 调用模型")
    assert FAKE_OPENAI_KEY not in text
    assert "sk-***" in text


def test_redact_masks_key_value_forms() -> None:
    for text in (
        "api_key=secret-value-123",
        "API-KEY: secret-value-123",
        "password = secret-value-123",
        "Authorization: Bearer secret-value-123",
        "X-Workbench-Token: secret-value-123",
    ):
        assert "secret-value-123" not in redact(text)


def test_redact_masks_long_random_strings() -> None:
    assert FAKE_LONG_TOKEN not in redact(f"token {FAKE_LONG_TOKEN} end")


def test_redact_keeps_long_snake_case_identifiers() -> None:
    identifier = "test_log_file_keeps_plain_text_and_masks_secrets"
    assert redact(identifier) == identifier


def test_log_file_keeps_plain_text_and_masks_secrets(tmp_path: Path) -> None:
    log_file = setup_logging(tmp_path, console=False)
    logger = logging.getLogger("babeldoc_workbench.test")
    try:
        logger.warning("普通日志：任务已开始")
        logger.error("带密钥的日志：%s", FAKE_OPENAI_KEY)
        try:
            raise RuntimeError(f"异常里带着 {FAKE_OPENAI_KEY}")
        except RuntimeError:
            logger.exception("捕获异常")
    finally:
        for handler in logging.getLogger().handlers:
            handler.flush()
            handler.close()
        logging.getLogger().handlers.clear()

    content = log_file.read_text(encoding="utf-8")
    assert "普通日志：任务已开始" in content
    assert FAKE_OPENAI_KEY not in content
    assert "sk-***" in content
    # 错误日志行与异常最后一行各出现一次
    assert content.count("sk-***") >= 2
