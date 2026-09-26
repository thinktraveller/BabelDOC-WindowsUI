"""参数校验与映射的离线单元测试（计划书步骤 4 通过标准 1）。"""

from __future__ import annotations

import pytest

from babeldoc.format.pdf.translation_config import WatermarkOutputMode
from babeldoc_workbench.engine.protocol import WATERMARK_MODES
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
        ({"output_mode": "neither"}, "output_mode"),
        ({"primary_font_family": "comic"}, "primary_font_family"),
        ({"only_include_translated_page": True}, "only_include_translated_page"),
        ({"min_text_length": 0}, "min_text_length"),
        ({"dual_original_position": "middle"}, "dual_original_position"),
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


@pytest.mark.parametrize(
    ("target_language", "glossary_language"),
    [("zh", "zh"), ("zh-cn", "zh_cn"), ("zh_cn", "zh-cn")],
)
def test_glossary_language_match_passes(
    target_language: str, glossary_language: str
) -> None:
    result = params.validate_params(
        {"lang_out": target_language, "glossary_version_id": 7},
        glossary={"tgt_lng": glossary_language},
    )
    assert result.ok


def test_language_and_watermark_choices_follow_engine_contract() -> None:
    schema = {item["key"]: item for item in params.schema_for_ui()}
    for key, default in (("lang_in", "en"), ("lang_out", "zh")):
        spec = schema[key]
        assert spec["type"] == "choice"
        values = [option["value"] for option in spec["options"]]
        assert default in values
        assert len(values) == len(set(values))
        assert all(params.LANGUAGE_PATTERN.fullmatch(value) for value in values)
    watermark = schema["watermark_output_mode"]
    assert watermark["type"] == "choice"
    modes = [option["value"] for option in watermark["options"]]
    assert modes == list(WATERMARK_MODES)
    assert modes == [mode.value for mode in WatermarkOutputMode]

    # BabelDOC 没有封闭语言枚举；旧预设中的合法代码仍可复用。
    legacy = params.validate_params({"lang_in": "la", "lang_out": "zh_cn"})
    assert legacy.ok
    assert params.to_engine_fields(legacy.params)["lang_out"] == "zh_cn"


@pytest.mark.parametrize(
    "key", ["table_model", "use_side_by_side_dual", "use_rich_pbar", "show_char_box"]
)
def test_deprecated_or_dev_only_params_are_rejected(key: str) -> None:
    result = params.validate_params({key: "x"})
    assert not result.ok
    assert key in result.errors


def test_schema_for_ui_excludes_deprecated_options() -> None:
    keys = {item["key"] for item in params.schema_for_ui()}
    assert keys == set(params.PARAM_KEYS) - {"no_mono", "no_dual"}
    assert not keys & set(params.REJECTED_PARAM_KEYS)
    for item in params.schema_for_ui():
        assert item["group"] in {"common", "advanced"}
        assert item["label"]


def test_bilingual_and_glossary_controls_are_grouped_and_mapped() -> None:
    schema_items = params.schema_for_ui()
    schema = {item["key"]: item for item in schema_items}
    common_order = [item["key"] for item in schema_items if item["group"] == "common"]
    assert common_order == [
        "lang_in", "lang_out", "pages", "output_mode"
    ]
    assert schema["dual_original_position"]["group"] == "advanced"
    assert schema["dual_original_position"]["default"] == "left"
    assert {option["value"] for option in schema["dual_original_position"]["options"]} == {
        "left", "right"
    }
    assert schema["use_alternating_pages_dual"]["group"] == "advanced"
    assert schema["auto_extract_glossary"]["group"] == "advanced"
    assert schema["glossary_version_id"]["group"] == "advanced"

    left = params.validate_params({})
    right = params.validate_params(
        {"dual_original_position": "right", "use_alternating_pages_dual": True}
    )
    assert left.ok and right.ok
    assert params.to_engine_fields(left.params)["dual_translate_first"] is False
    right_fields = params.to_engine_fields(right.params)
    assert right_fields["dual_translate_first"] is True
    assert right_fields["use_alternating_pages_dual"] is True


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
    assert "output_mode" not in fields


@pytest.mark.parametrize(
    ("mode", "no_mono", "no_dual"),
    [("both", False, False), ("mono", False, True), ("dual", True, False)],
)
def test_output_mode_maps_to_legacy_engine_flags(
    mode: str, no_mono: bool, no_dual: bool
) -> None:
    validated = params.validate_params({"output_mode": mode})
    assert validated.ok
    fields = params.to_engine_fields(validated.params)
    assert fields["no_mono"] is no_mono
    assert fields["no_dual"] is no_dual


def test_old_output_flags_remain_valid_and_both_disabled_is_rejected() -> None:
    only_dual = params.validate_params({"no_mono": True})
    only_mono = params.validate_params({"no_dual": True})
    assert only_dual.ok and only_dual.params["output_mode"] == "dual"
    assert only_mono.ok and only_mono.params["output_mode"] == "mono"
    invalid = params.validate_params({"no_mono": True, "no_dual": True})
    assert "no_dual" in invalid.errors
    legacy_ui = params.validate_params({"output_mode": "legacy-invalid"})
    assert "旧预设" in legacy_ui.errors["output_mode"]
    mixed = params.validate_params({
        "output_mode": "both", "no_mono": True, "no_dual": True
    })
    assert mixed.ok
    assert mixed.params["no_mono"] is False
    assert mixed.params["no_dual"] is False
    assert params.to_engine_fields(mixed.params)["no_mono"] is False
    assert params.to_engine_fields(mixed.params)["no_dual"] is False


def test_new_advanced_options_reach_engine_with_valid_defaults() -> None:
    schema = {item["key"]: item for item in params.schema_for_ui()}
    for key in (
        "primary_font_family", "only_include_translated_page",
        "min_text_length", "disable_rich_text_translate",
    ):
        assert schema[key]["group"] == "advanced"
    validated = params.validate_params({
        "pages": "2-3", "primary_font_family": "serif",
        "only_include_translated_page": True, "min_text_length": 7,
        "disable_rich_text_translate": True,
    })
    assert validated.ok
    fields = params.to_engine_fields(validated.params)
    assert fields["primary_font_family"] == "serif"
    assert fields["only_include_translated_page"] is True
    assert fields["min_text_length"] == 7
    assert fields["disable_rich_text_translate"] is True
    defaults = params.to_engine_fields(params.validate_params({}).params)
    assert defaults["primary_font_family"] is None
    assert defaults["only_include_translated_page"] is False
    assert defaults["min_text_length"] == 5
    assert defaults["disable_rich_text_translate"] is False


def test_parse_pages_accepts_chinese_comma() -> None:
    assert params.parse_pages("1-2，4") == [(1, 2), (4, 4)]
