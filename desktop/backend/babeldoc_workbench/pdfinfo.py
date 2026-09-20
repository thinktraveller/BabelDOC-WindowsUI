"""PDF 元信息读取（用于页码范围校验）。"""

from __future__ import annotations

from pathlib import Path


class PdfInfoError(RuntimeError):
    """无法读取 PDF 时抛出，由调用方转换为用户可读提示。"""


def page_count(path: str | Path) -> int:
    """返回页数；读取失败时抛 :class:`PdfInfoError`。"""
    try:
        import pymupdf
    except Exception as exc:  # pragma: no cover - 依赖缺失时
        raise PdfInfoError(f"无法加载 PDF 组件：{exc}") from exc
    try:
        with pymupdf.open(str(path)) as document:
            return int(document.page_count)
    except Exception as exc:
        raise PdfInfoError(f"无法读取 PDF 页数：{exc}") from exc
