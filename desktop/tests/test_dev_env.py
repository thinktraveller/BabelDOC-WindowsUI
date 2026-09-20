"""`.env` 开发配置读取的离线单元测试（不涉及任何真实凭据）。"""

from __future__ import annotations

from pathlib import Path

from babeldoc_workbench.dev_env import (
    describe_source,
    load_env_file,
    parse_env_text,
    resolve,
)


def test_parse_env_text_ignores_comments_and_blanks() -> None:
    text = "\n".join(
        [
            "# 注释",
            "",
            "  ",
            "BABELDOC_MODEL=demo-model",
            "  BABELDOC_LANG_OUT = zh  ",
            "export BABELDOC_LANG_IN=en",
            "NO_EQUALS_SIGN",
        ]
    )
    parsed = parse_env_text(text)
    assert parsed["BABELDOC_MODEL"] == "demo-model"
    assert parsed["BABELDOC_LANG_OUT"] == "zh"
    assert parsed["BABELDOC_LANG_IN"] == "en"
    assert "NO_EQUALS_SIGN" not in parsed


def test_parse_env_text_strips_quotes_and_keeps_inner_equals() -> None:
    parsed = parse_env_text(
        'A="quoted value"\nB=\'single\'\nC=https://host/v1?x=1&y=2\n'
    )
    assert parsed["A"] == "quoted value"
    assert parsed["B"] == "single"
    assert parsed["C"] == "https://host/v1?x=1&y=2"


def test_load_env_file_missing_returns_empty(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "not-exists.env") == {}


def test_load_env_file_reads_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("\ufeffBABELDOC_MODEL=demo\n", encoding="utf-8")
    assert load_env_file(path) == {"BABELDOC_MODEL": "demo"}


def test_process_environment_wins_over_dotenv() -> None:
    dotenv = {"BABELDOC_MODEL": "from-dotenv"}
    environ = {"BABELDOC_MODEL": "from-process"}
    assert (
        resolve("BABELDOC_MODEL", dotenv, environ=environ) == "from-process"
    )


def test_dotenv_used_when_process_missing() -> None:
    dotenv = {"BABELDOC_BASE_URL": "https://example.test/v1"}
    assert (
        resolve("BABELDOC_BASE_URL", dotenv, environ={})
        == "https://example.test/v1"
    )


def test_blank_process_value_falls_back_to_dotenv() -> None:
    dotenv = {"BABELDOC_MODEL": "from-dotenv"}
    environ = {"BABELDOC_MODEL": "   "}
    assert resolve("BABELDOC_MODEL", dotenv, environ=environ) == "from-dotenv"


def test_alias_names_are_tried_in_order() -> None:
    dotenv = {"LEGACY_NAME": "legacy-value"}
    assert (
        resolve(("NEW_NAME", "LEGACY_NAME"), dotenv, environ={})
        == "legacy-value"
    )


def test_default_returned_when_nothing_set() -> None:
    assert resolve("MISSING", {}, environ={}, default="fallback") == "fallback"
    assert resolve("MISSING", {}, environ={}) is None


def test_describe_source_reports_origin_without_value() -> None:
    dotenv = {"BABELDOC_MODEL": "demo-model"}
    assert describe_source("BABELDOC_MODEL", dotenv, environ={}) == ".env 中的 BABELDOC_MODEL"
    assert (
        describe_source("BABELDOC_MODEL", dotenv, environ={"BABELDOC_MODEL": "x"})
        == "环境变量 BABELDOC_MODEL"
    )
    assert describe_source("BABELDOC_API_KEY", dotenv, environ={}) == "未设置"
