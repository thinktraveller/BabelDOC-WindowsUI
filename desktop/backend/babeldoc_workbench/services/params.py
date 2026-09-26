"""参数分组、校验与到引擎字段的映射。

只暴露当前版本真正生效的选项；已弃用/已失效的选项（表格 OCR、拼版式双语、
开发用进程池开关等）不进入界面，也不被接受。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from babeldoc_workbench.engine.protocol import (
    DEFAULT_LANG_IN,
    DEFAULT_LANG_OUT,
    DEFAULT_QPS,
    DEFAULT_REPORT_INTERVAL,
    DEFAULT_WATERMARK_MODE,
    WATERMARK_MODES,
)

VALIDATION_ERROR_CODE = "invalid_params"

LANGUAGE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,15}$")

# 引擎 CLI 不限定语言枚举，以下仅是工作台提供的常用代码。模型能否翻译某种语言
# 取决于用户选择的服务；历史预设中的其它合法代码仍由校验器接受。
LANGUAGE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("en", "英语 (en)"),
    ("zh", "中文 (zh)"),
    ("zh-cn", "简体中文 (zh-cn)"),
    ("zh-tw", "繁体中文 (zh-tw)"),
    ("zh-hans", "简体中文 (zh-hans)"),
    ("zh-hant", "繁体中文 (zh-hant)"),
    ("ja", "日语 (ja)"),
    ("ko", "韩语 (ko)"),
    ("fr", "法语 (fr)"),
    ("de", "德语 (de)"),
    ("es", "西班牙语 (es)"),
    ("it", "意大利语 (it)"),
    ("pt", "葡萄牙语 (pt)"),
    ("ru", "俄语 (ru)"),
    ("ar", "阿拉伯语 (ar)"),
    ("hi", "印地语 (hi)"),
    ("uk", "乌克兰语 (uk)"),
    ("vi", "越南语 (vi)"),
    ("th", "泰语 (th)"),
    ("id", "印度尼西亚语 (id)"),
)
WATERMARK_MODE_LABELS = {
    "watermarked": "添加水印",
    "no_watermark": "不添加水印",
    "both": "同时输出两种版本",
}
WATERMARK_OPTIONS = tuple(
    (mode, WATERMARK_MODE_LABELS[mode]) for mode in WATERMARK_MODES
)
OUTPUT_MODE_OPTIONS = (
    ("both", "同时输出单语与双语"),
    ("mono", "仅输出单语"),
    ("dual", "仅输出双语"),
)
OUTPUT_MODES = frozenset(value for value, _label in OUTPUT_MODE_OPTIONS)
FONT_FAMILY_OPTIONS = (
    ("auto", "自动选择"),
    ("serif", "衬线字体"),
    ("sans-serif", "无衬线字体"),
    ("script", "手写／斜体字体"),
)
FONT_FAMILIES = frozenset(value for value, _label in FONT_FAMILY_OPTIONS)
LEGACY_OUTPUT_KEYS = frozenset({"no_mono", "no_dual"})


@dataclass(frozen=True)
class ParamSpec:
    key: str
    group: str
    label: str
    type: str
    default: Any
    engine_field: str
    hint: str = ""
    options: tuple[tuple[str, str], ...] = ()


PARAM_SPECS: tuple[ParamSpec, ...] = (
    ParamSpec(
        "lang_in", "common", "源语言", "choice", DEFAULT_LANG_IN, "lang_in",
        "常用语言代码；引擎还接受其它代码，旧预设可继续使用", LANGUAGE_OPTIONS,
    ),
    ParamSpec(
        "lang_out",
        "common",
        "目标语言",
        "choice",
        DEFAULT_LANG_OUT,
        "lang_out",
        "常用语言代码；会影响输出文件名和术语版本筛选",
        LANGUAGE_OPTIONS,
    ),
    ParamSpec(
        "pages", "common", "页码范围", "pages", None, "pages", "例如 1-5,8；留空表示全部"
    ),
    ParamSpec(
        "output_mode", "common", "输出类型", "choice", "both", "output_mode",
        "至少输出一种 PDF", OUTPUT_MODE_OPTIONS,
    ),
    # 旧任务和预设继续接受这两个字段；界面统一使用 output_mode。
    ParamSpec("no_mono", "common", "不输出单语", "bool", False, "no_mono"),
    ParamSpec("no_dual", "common", "不输出双语", "bool", False, "no_dual"),
    ParamSpec(
        "dual_original_position",
        "advanced",
        "双语原文位置",
        "choice",
        "left",
        "dual_translate_first",
        "同页对照：原文在左/右；交替页：原文先/后",
        (("left", "左侧（交替页时在前）"), ("right", "右侧（交替页时在后）")),
    ),
    ParamSpec(
        "use_alternating_pages_dual",
        "advanced",
        "双语排列为交替页",
        "bool",
        False,
        "use_alternating_pages_dual",
        "默认同页对照",
    ),
    ParamSpec(
        "auto_extract_glossary", "advanced", "自动提取术语", "bool", True, "auto_extract_glossary"
    ),
    ParamSpec(
        "glossary_version_id",
        "advanced",
        "使用术语版本",
        "glossary",
        None,
        "glossaries",
        "来自术语资料库的某个版本",
    ),
    ParamSpec("qps", "advanced", "QPS", "int", DEFAULT_QPS, "qps", "引擎默认 4"),
    ParamSpec(
        "pool_max_workers",
        "advanced",
        "翻译并发",
        "int",
        None,
        "pool_max_workers",
        "不填时等于 QPS",
    ),
    ParamSpec(
        "term_pool_max_workers",
        "advanced",
        "术语提取并发",
        "int",
        None,
        "term_pool_max_workers",
    ),
    ParamSpec(
        "max_pages_per_part",
        "advanced",
        "分片页数",
        "int",
        None,
        "max_pages_per_part",
        "不填则不分片",
    ),
    ParamSpec(
        "primary_font_family", "advanced", "译文字体风格", "choice", "auto",
        "primary_font_family", "不指定时由引擎自动选择", FONT_FAMILY_OPTIONS,
    ),
    ParamSpec(
        "only_include_translated_page", "advanced", "仅保留所选翻译页", "bool", False,
        "only_include_translated_page", "只在设置页码范围时生效",
    ),
    ParamSpec(
        "min_text_length", "advanced", "最短翻译文本长度", "int", 5,
        "min_text_length", "短于此字符数的文本不翻译；引擎默认 5",
    ),
    ParamSpec(
        "disable_rich_text_translate", "advanced", "关闭富文本翻译", "bool", False,
        "disable_rich_text_translate", "兼容性选项，可能减少译文格式保留",
    ),
    ParamSpec(
        "custom_system_prompt", "advanced", "自定义提示词", "str", None, "custom_system_prompt"
    ),
    ParamSpec(
        "report_interval",
        "advanced",
        "进度上报间隔（秒）",
        "float",
        DEFAULT_REPORT_INTERVAL,
        "report_interval",
    ),
    ParamSpec(
        "watermark_output_mode",
        "advanced",
        "水印输出模式",
        "choice",
        DEFAULT_WATERMARK_MODE,
        "watermark_output_mode",
        "控制译文 PDF 的水印输出",
        WATERMARK_OPTIONS,
    ),
)

PARAM_KEYS = frozenset(spec.key for spec in PARAM_SPECS)

# 已弃用或仅开发使用，界面不展示、接口也不接受
REJECTED_PARAM_KEYS = {
    "table_model": "表格 OCR 相关参数在当前版本已弃用并被忽略",
    "use_side_by_side_dual": "拼版式双语已停用",
    "use_rich_pbar": "进度条渲染开关仅供开发使用",
    "show_char_box": "字符框调试开关仅供开发使用",
}


@dataclass
class ValidationResult:
    params: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def schema_for_ui() -> list[dict[str, Any]]:
    """给前端的参数定义（分组、标签、类型、默认值），避免前端硬编码。"""
    return [
        {
            "key": spec.key,
            "group": spec.group,
            "label": spec.label,
            "type": spec.type,
            "default": spec.default,
            "hint": spec.hint,
            "options": [
                {"value": value, "label": label} for value, label in spec.options
            ],
        }
        for spec in PARAM_SPECS
        if spec.key not in LEGACY_OUTPUT_KEYS
    ]


def default_params() -> dict[str, Any]:
    return {spec.key: spec.default for spec in PARAM_SPECS}


def parse_pages(value: str) -> list[tuple[int, int]]:
    """解析 ``1-5,8`` 形式的页码范围；语法错误时抛 ``ValueError``。"""
    ranges: list[tuple[int, int]] = []
    for chunk in value.replace("，", ",").split(","):
        part = chunk.strip()
        if not part:
            continue
        if "-" in part:
            start_text, _, end_text = part.partition("-")
            start_text, end_text = start_text.strip(), end_text.strip()
            if not start_text.isdigit() or not end_text.isdigit():
                raise ValueError("页码范围格式不正确，示例：1-5,8")
            start, end = int(start_text), int(end_text)
            if start == 0 or end == 0:
                raise ValueError("页码从第 1 页开始，不能使用 0")
            if start > end:
                raise ValueError(f"起始页 {start} 不能大于结束页 {end}")
        else:
            if not part.isdigit():
                raise ValueError("页码范围格式不正确，示例：1-5,8")
            start = end = int(part)
            if start == 0:
                raise ValueError("页码从第 1 页开始，不能使用 0")
        ranges.append((start, end))
    if not ranges:
        raise ValueError("页码范围不能为空，留空表示全部页面")
    return ranges


def _check_positive_int(
    value: Any, key: str, errors: dict[str, str], *, allow_none: bool = True, minimum: int = 1
) -> int | None:
    if value is None or value == "":
        if allow_none:
            return None
        errors[key] = "该项必填"
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        errors[key] = "必须是整数"
        return None
    if number < minimum:
        errors[key] = f"必须是不小于 {minimum} 的整数"
        return None
    return number


def _check_language(value: Any, key: str, errors: dict[str, str]) -> str:
    text = str(value or "").strip()
    if not text:
        errors[key] = "语言代码不能为空"
        return ""
    if not LANGUAGE_PATTERN.match(text):
        errors[key] = "语言代码格式不正确，例如 en、zh、zh-cn"
        return text
    return text


def _normalized_language(value: Any) -> str:
    """与引擎术语 CSV 的语言比较规则保持一致。"""
    return str(value or "").strip().lower().replace("-", "_")


def validate_params(
    params: Mapping[str, Any],
    *,
    page_count: int | None = None,
    glossary: Mapping[str, Any] | None = None,
) -> ValidationResult:
    """校验并归一化参数。

    :param page_count: 输入 PDF 的页数；提供时页码范围不得超过它。
    :param glossary: 选中的术语版本信息，需包含 ``tgt_lng``；与目标语言不一致时报错。
    """
    errors: dict[str, str] = {}
    normalized: dict[str, Any] = default_params()

    unknown = set(params) - PARAM_KEYS
    for key in sorted(unknown):
        if key in REJECTED_PARAM_KEYS:
            errors[key] = REJECTED_PARAM_KEYS[key]
        else:
            errors[key] = "未知参数"
    if errors:
        return ValidationResult(params=dict(params), errors=errors)

    normalized.update({key: value for key, value in params.items() if key in PARAM_KEYS})
    normalized["lang_in"] = _check_language(normalized.get("lang_in"), "lang_in", errors)
    normalized["lang_out"] = _check_language(normalized.get("lang_out"), "lang_out", errors)

    pages_value = normalized.get("pages")
    if pages_value in (None, ""):
        normalized["pages"] = None
    else:
        try:
            parsed = parse_pages(str(pages_value))
        except ValueError as exc:
            errors["pages"] = str(exc)
        else:
            if page_count is not None:
                over = [end for _start, end in parsed if end > page_count]
                if over:
                    errors["pages"] = (
                        f"页码范围超出文档页数：文档共 {page_count} 页，"
                        f"但指定了第 {max(over)} 页"
                    )
            normalized["pages"] = str(pages_value).strip()

    legacy_no_mono = bool(normalized.get("no_mono"))
    legacy_no_dual = bool(normalized.get("no_dual"))
    if "output_mode" in params:
        output_mode = normalized.get("output_mode")
        if output_mode not in OUTPUT_MODES:
            errors["output_mode"] = (
                "旧预设同时关闭了单语与双语，请重新选择输出类型"
                if output_mode == "legacy-invalid"
                else "请选择同时输出、仅单语或仅双语"
            )
    else:
        if legacy_no_mono and legacy_no_dual:
            errors["no_dual"] = "不能同时关闭单语与双语输出，至少要保留一种"
        output_mode = "dual" if legacy_no_mono else "mono" if legacy_no_dual else "both"
    if output_mode in OUTPUT_MODES:
        normalized["output_mode"] = output_mode
        normalized["no_mono"] = output_mode == "dual"
        normalized["no_dual"] = output_mode == "mono"

    normalized["use_alternating_pages_dual"] = bool(
        normalized.get("use_alternating_pages_dual")
    )
    position = normalized.get("dual_original_position")
    if position not in ("left", "right"):
        errors["dual_original_position"] = "请选择原文在左侧或右侧"
    normalized["auto_extract_glossary"] = bool(normalized.get("auto_extract_glossary"))

    normalized["qps"] = _check_positive_int(
        normalized.get("qps"), "qps", errors, allow_none=False
    )
    normalized["pool_max_workers"] = _check_positive_int(
        normalized.get("pool_max_workers"), "pool_max_workers", errors
    )
    normalized["term_pool_max_workers"] = _check_positive_int(
        normalized.get("term_pool_max_workers"), "term_pool_max_workers", errors
    )
    normalized["max_pages_per_part"] = _check_positive_int(
        normalized.get("max_pages_per_part"), "max_pages_per_part", errors
    )
    normalized["min_text_length"] = _check_positive_int(
        normalized.get("min_text_length"), "min_text_length", errors, allow_none=False
    )
    font_family = normalized.get("primary_font_family")
    if font_family is None:
        font_family = "auto"  # 已有预设可能没有这一字段
    if font_family not in FONT_FAMILIES:
        errors["primary_font_family"] = "请选择自动、衬线、无衬线或手写字体"
    normalized["primary_font_family"] = font_family
    normalized["only_include_translated_page"] = bool(
        normalized.get("only_include_translated_page")
    )
    if normalized["only_include_translated_page"] and normalized["pages"] is None:
        errors["only_include_translated_page"] = "请先设置页码范围"
    normalized["disable_rich_text_translate"] = bool(
        normalized.get("disable_rich_text_translate")
    )

    interval = normalized.get("report_interval")
    try:
        interval_value = float(interval) if interval not in (None, "") else DEFAULT_REPORT_INTERVAL
    except (TypeError, ValueError):
        errors["report_interval"] = "必须是数字（秒）"
    else:
        if interval_value <= 0:
            errors["report_interval"] = "必须大于 0 秒"
        normalized["report_interval"] = interval_value

    mode = str(normalized.get("watermark_output_mode") or DEFAULT_WATERMARK_MODE)
    if mode not in WATERMARK_MODES:
        errors["watermark_output_mode"] = "取值必须是 " + " / ".join(WATERMARK_MODES)
    normalized["watermark_output_mode"] = mode

    prompt = normalized.get("custom_system_prompt")
    if prompt in (None, ""):
        normalized["custom_system_prompt"] = None
    elif len(str(prompt)) > 8000:
        errors["custom_system_prompt"] = "自定义提示词过长（上限 8000 字符）"
    else:
        normalized["custom_system_prompt"] = str(prompt)

    glossary_id = normalized.get("glossary_version_id")
    if glossary_id in (None, ""):
        normalized["glossary_version_id"] = None
    elif glossary is None:
        errors["glossary_version_id"] = "找不到该术语版本，请重新选择"
    else:
        target = _normalized_language(glossary.get("tgt_lng"))
        if target and target != _normalized_language(normalized["lang_out"]):
            errors["glossary_version_id"] = (
                f"术语表目标语言（{target}）与本次目标语言"
                f"（{normalized['lang_out']}）不一致"
            )

    return ValidationResult(params=normalized, errors=errors)


def to_engine_fields(params: Mapping[str, Any]) -> dict[str, Any]:
    """把校验后的参数映射为 :class:`EngineJobRequest` 字段。"""
    fields: dict[str, Any] = {}
    for spec in PARAM_SPECS:
        if spec.key in ("glossary_version_id", "output_mode"):
            continue
        if spec.key == "primary_font_family":
            family = params.get(spec.key, spec.default)
            fields[spec.engine_field] = None if family in (None, "auto") else family
            continue
        if spec.key == "dual_original_position":
            fields["dual_translate_first"] = params.get(spec.key, spec.default) == "right"
            continue
        fields[spec.engine_field] = params.get(spec.key, spec.default)
    return fields


def selected_param_keys() -> Sequence[str]:
    return tuple(spec.key for spec in PARAM_SPECS)
