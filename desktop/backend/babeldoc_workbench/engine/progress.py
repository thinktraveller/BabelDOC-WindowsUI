"""引擎阶段名到界面中文文案的映射。

阶段名来自 ``babeldoc`` 各处理类的 ``stage_name``。引擎版本升级后若出现新阶段，
:func:`stage_label` 会原样透传而不是丢弃，避免界面出现空白阶段。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

STAGE_LABELS_ZH: dict[str, str] = {
    "Parse PDF and Create Intermediate Representation": "解析 PDF 并生成中间表示",
    "DetectScannedFile": "检测扫描件",
    "Parse Page Layout": "分析页面版面",
    "Parse Table": "解析表格",
    "Parse Paragraphs": "识别段落",
    "Parse Formulas and Styles": "解析公式与样式",
    "Automatic Term Extraction": "提取术语",
    "Translate Paragraphs": "翻译段落",
    "Typesetting": "重新排版",
    "Add Fonts": "处理字体",
    "Generate drawing instructions": "生成 PDF 绘制指令",
    "Subset font": "子集化字体",
    "Save PDF": "保存 PDF",
    "Add Debug Information": "写入调试信息",
}


def stage_label(stage_name: str | None) -> str:
    """返回阶段中文文案；未知阶段名原样返回。"""
    if not stage_name:
        return "未知阶段"
    return STAGE_LABELS_ZH.get(stage_name, stage_name)


def stage_summary_payload(stages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """把引擎的 ``stage_summary`` 条目转换为界面可用结构。"""
    result: list[dict[str, Any]] = []
    for item in stages:
        name = item.get("name")
        result.append(
            {
                "name": name,
                "label": stage_label(name),
                "weight_percent": item.get("percent"),
            }
        )
    return result
