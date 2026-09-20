"""日志配置与统一脱敏。

要求（计划书步骤 3）：日志写入应用数据目录的 ``logs``，并且在格式化层统一脱敏，
保证异常堆栈中也不出现 API Key、会话令牌等敏感内容。
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_REDACTION_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # OpenAI 风格 Key
    (re.compile(r"sk-[A-Za-z0-9_\-]{6,}"), "sk-***"),
    # 常见的键值形式
    (
        re.compile(r"(?i)\b(api[_-]?key|apikey|token|secret|password)\b(\s*[=:]\s*)(\S+)"),
        r"\1\2***",
    ),
    # HTTP 头
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)(\S+)"), r"\1***"),
    (re.compile(r"(?i)(x-workbench-token\s*[:=]\s*)(\S+)"), r"\1***"),
    # 长随机串（令牌、会话 id 等）：要求同时含大小写与数字，
    # 避免把 test_log_file_keeps_plain_text_and_masks_secrets 这类标识符误伤
    (
        re.compile(
            r"\b(?=[A-Za-z0-9_\-]{40,}\b)"
            r"(?=[A-Za-z0-9_\-]*[A-Z])(?=[A-Za-z0-9_\-]*[a-z])"
            r"(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]+\b"
        ),
        "***",
    ),
)


def redact(text: str) -> str:
    """对任意文本做脱敏，供日志与其他输出复用。"""
    result = text
    for pattern, replacement in _REDACTION_RULES:
        result = pattern.sub(replacement, result)
    return result


class RedactingFormatter(logging.Formatter):
    """先完整格式化（含异常堆栈）再脱敏，避免堆栈里的内容漏出去。"""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(
    logs_dir: Path,
    *,
    level: int = logging.INFO,
    console: bool = True,
    max_bytes: int = 1_000_000,
    backup_count: int = 5,
) -> Path:
    """配置根日志：轮转文件 + 可选控制台，两者都经过脱敏。"""
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "workbench.log"

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)

    formatter = RedactingFormatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if console and sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    return log_file
