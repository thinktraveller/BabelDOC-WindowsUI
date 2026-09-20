"""参数校验与映射的离线单元测试（计划书步骤 4 通过标准 1）。"""

from __future__ import annotations

import pytest

from babeldoc_workbench.services import params


def test_default_params_are_valid() -> None:
    result = params.validate_params({})
    assert result.ok
    assert result.params["lang_in"] == "en"
    assert result.params["lang_out"] == "zh"
    assert result.params["qps"] == 4


@pytest.mark.parametrize(
    ("payload", "error_key"),
    [
        ({"lang_in": "   "}, "lang_in"),
        ({"lang_out": ""}, "lang_out"),
        ({"lang_in": "12!"}, "lang_in"),
        ({"pages": "0-3"}, "pages"),
        ({"pages": "5-2"}, "pages"),
        ({"pages": "abc"}, "pages"),
        ({"pages": "1-"}, "pages"),
        ({"qps": 0}, "qps"),
        ({"qps": -3}, "qps"),
        ({"qps": "many"}, "qps"),
        ({"pool_max_workers": 0}, "pool_max_workers"),
        ({"term_pool_max_workers": -1}, "term_pool_max_workers"),
        ({"max_pages_per_part": 0}, "max_pages_per_part"),
        ({"report_interval": 0}, "report_interval"),
        ({"report_interval": "fast"}, "report_interval"),
        ({"watermark_output_mode": "unknown"}, "watermark_output_mode"),
        ({"no_mono": True, "no_dual": True}, "no_dual"),
        ({"custom_system_prompt": "x" * 8001}, "custom_system_prompt"),
    ],
)
def test_invalid_params_are_blocked(payload: dict, error_key: str) -> None:
    result = params.validate_params(payload)
    assert not result.ok
    assert error_key in result.errors


def test_pages_beyond_document_are_blocked() -> None:
    result = params.validate_params({"pages": "1-5"}, page_count=3)
    assert not result.ok
    assert "共 3 页" in result.errors["pages"]


def test_valid_pages_pass_with_page_count() -> None:
    result = params.validate_params({"pages": "1-3,7"}, page_count=10)
    assert result.ok
    assert result.params["pages"] == "1-3,7"


def test_glossary_language_mismatch_is_blocked() -> None:
    result = params.validate_params(
        {"lang_out": "zh", "glossary_version_id": 7},
        glossary={"tgt_lng": "ja"},
    )
    assert not result.ok
    assert "不一致" in result.errors["glossary_version_id"]


def test_glossary_language_match_passes() -> None:
    result = params.validate_params(
        {"lang_out": "zh", "glossary_version_id": 7},
        glossary={"tgt_lng": "zh"},
    )
    assert result.ok


@pytest.mark.parametrize(
    "key", ["table_model", "use_side_by_side_dual", "use_rich_pbar", "show_char_box"]
)
def test_deprecated_or_dev_only_params_are_rejected(key: str) -> None:
    result = params.validate_params({key: "x"})
    assert not result.ok
    assert key in result.errors


def test_schema_for_ui_excludes_deprecated_options() -> None:
    keys = {item["key"] for item in params.schema_for_ui()}
    assert keys == set(params.PARAM_KEYS)
    assert not keys & set(params.REJECTED_PARAM_KEYS)
    for item in params.schema_for_ui():
        assert item["group"] in {"common", "advanced"}
        assert item["label"]


def test_to_engine_fields_maps_engine_names() -> None:
    validated = params.validate_params(
        {"lang_in": "en", "lang_out": "zh", "qps": 8, "max_pages_per_part": 10}
    )
    fields = params.to_engine_fields(validated.params)
    assert fields["lang_in"] == "en"
    assert fields["qps"] == 8
    assert fields["max_pages_per_part"] == 10
    assert fields["watermark_output_mode"] == "watermarked"
    assert "glossary_version_id" not in fields


def test_parse_pages_accepts_chinese_comma() -> None:
    assert params.parse_pages("1-2，4") == [(1, 2), (4, 4)]
