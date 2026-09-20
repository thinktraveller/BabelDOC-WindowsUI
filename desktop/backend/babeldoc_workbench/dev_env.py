""".env 开发配置读取（仅标准库，无第三方依赖）。

约定见 ``project-docs/project-plan.md`` 的「API 凭据与 `.env` 约定」：

- 真实凭据只放在仓库根目录的 ``.env``；该文件已被 ``.gitignore`` 忽略，不得提交。
- 读取优先级：**进程环境变量 > .env**。
- 本模块只读取变量，不写回文件，也不打印变量值。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

DEFAULT_ENV_FILENAME = ".env"


def parse_env_text(text: str) -> dict[str, str]:
    """解析 ``KEY=VALUE`` 文本；忽略空行、``#`` 注释与 ``export`` 前缀。"""
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        result[key] = value
    return result


def load_env_file(path: Path) -> dict[str, str]:
    """读取 ``.env``；文件不存在或不可读时返回空字典，不抛异常。"""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    return parse_env_text(text)


def resolve(
    names: str | Iterable[str],
    dotenv: Mapping[str, str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    default: str | None = None,
) -> str | None:
    """按“进程环境变量 > .env”的顺序取第一个非空值。"""
    if isinstance(names, str):
        names = (names,)
    environment = os.environ if environ is None else environ
    dotenv = dotenv or {}
    for name in names:
        for source in (environment, dotenv):
            value = source.get(name)
            if value is not None and str(value).strip():
                return str(value).strip()
    return default


def describe_source(
    names: str | Iterable[str],
    dotenv: Mapping[str, str],
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """说明变量来自进程环境变量还是 ``.env``，用于日志（不返回值内容）。"""
    if isinstance(names, str):
        names = (names,)
    environment = os.environ if environ is None else environ
    for name in names:
        if str(environment.get(name) or "").strip():
            return f"环境变量 {name}"
    for name in names:
        if str(dotenv.get(name) or "").strip():
            return f".env 中的 {name}"
    return "未设置"
