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


@dataclass(frozen=True)
class ParamSpec:
    key: str
    group: str
    label: str
    type: str
    default: Any
    engine_field: str
    hint: str = ""


PARAM_SPECS: tuple[ParamSpec, ...] = (
    ParamSpec(
        "lang_in", "common", "源语言", "str", DEFAULT_LANG_IN, "lang_in", "例如 en"
    ),
    ParamSpec(
        "lang_out",
        "common",
        "目标语言",
        "str",
        DEFAULT_LANG_OUT,
        "lang_out",
        "例如 zh，会影响输出文件名",
    ),
    ParamSpec(
        "pages", "common", "页码范围", "pages", None, "pages", "例如 1-5,8；留空表示全部"
    ),
    ParamSpec("no_mono", "common", "不输出单语", "bool", False, "no_mono"),
    ParamSpec("no_dual", "common", "不输出双语", "bool", False, "no_dual"),
    ParamSpec(
        "use_alternating_pages_dual",
        "common",
        "双语排列为交替页",
        "bool",
        False,
        "use_alternating_pages_dual",
        "默认同页对照",
    ),
    ParamSpec(
        "auto_extract_glossary", "common", "自动提取术语", "bool", True, "auto_extract_glossary"
    ),
    ParamSpec(
        "glossary_version_id",
        "common",
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
        "str",
        DEFAULT_WATERMARK_MODE,
        "watermark_output_mode",
        "取值：watermarked / no_watermark / both",
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
        }
        for spec in PARAM_SPECS
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

    normalized["no_mono"] = bool(normalized.get("no_mono"))
    normalized["no_dual"] = bool(normalized.get("no_dual"))
    if normalized["no_mono"] and normalized["no_dual"]:
        errors["no_dual"] = "不能同时关闭单语与双语输出，至少要保留一种"

    normalized["use_alternating_pages_dual"] = bool(
        normalized.get("use_alternating_pages_dual")
    )
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
        target = str(glossary.get("tgt_lng") or "").strip().lower()
        if target and target != str(normalized["lang_out"]).strip().lower():
            errors["glossary_version_id"] = (
                f"术语表目标语言（{target}）与本次目标语言"
                f"（{normalized['lang_out']}）不一致"
            )

    return ValidationResult(params=normalized, errors=errors)


def to_engine_fields(params: Mapping[str, Any]) -> dict[str, Any]:
    """把校验后的参数映射为 :class:`EngineJobRequest` 字段。"""
    fields: dict[str, Any] = {}
    for spec in PARAM_SPECS:
        if spec.key == "glossary_version_id":
            continue
        fields[spec.engine_field] = params.get(spec.key, spec.default)
    return fields


def selected_param_keys() -> Sequence[str]:
    return tuple(spec.key for spec in PARAM_SPECS)
