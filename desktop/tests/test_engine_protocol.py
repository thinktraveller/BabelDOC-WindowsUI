"""步骤 1 的离线单元测试：协议结构、事件映射与错误映射。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from babeldoc_workbench.engine.adapter import (
    build_config,
    error_event,
    finish_payload,
    to_event,
)
from babeldoc_workbench.engine.progress import stage_label, stage_summary_payload
from babeldoc_workbench.engine.protocol import (
    ERROR_ENGINE_FAILURE,
    EVENT_CANCELLED,
    EVENT_ERROR,
    EVENT_FINISH,
    EVENT_PROGRESS_UPDATE,
    EVENT_STAGE_SUMMARY,
    EngineApiConfig,
    EngineJobRequest,
)


def test_request_roundtrip() -> None:
    request = EngineJobRequest(
        job_id="job-1",
        input_path="C:/tmp/in.pdf",
        output_dir="C:/tmp/out",
        glossary_files=("a.csv", "b.csv"),
        qps=8,
        dual_translate_first=True,
        primary_font_family="serif",
        only_include_translated_page=True,
        min_text_length=7,
        disable_rich_text_translate=True,
    )
    restored = EngineJobRequest.from_dict(request.to_dict())
    assert restored == request
    assert isinstance(restored.glossary_files, tuple)


def test_bilingual_order_reaches_translation_config(monkeypatch, tmp_path: Path) -> None:
    from babeldoc_workbench.engine import adapter

    monkeypatch.setattr(adapter, "set_translate_rate_limiter", lambda _qps: None)
    monkeypatch.setattr(adapter.DocLayoutModel, "load_available", lambda: object())
    monkeypatch.setattr(adapter, "TranslationConfig", lambda **kwargs: kwargs)
    request = EngineJobRequest(
        job_id="dual-order",
        input_path=str(tmp_path / "input.pdf"),
        output_dir=str(tmp_path),
        dual_translate_first=True,
        use_alternating_pages_dual=True,
        skip_translation=True,
    )
    config = build_config(request, EngineApiConfig(model="offline-stub"), tmp_path)
    assert config["dual_translate_first"] is True
    assert config["use_alternating_pages_dual"] is True


def test_new_advanced_options_reach_translation_config(monkeypatch, tmp_path: Path) -> None:
    from babeldoc_workbench.engine import adapter

    monkeypatch.setattr(adapter, "set_translate_rate_limiter", lambda _qps: None)
    monkeypatch.setattr(adapter.DocLayoutModel, "load_available", lambda: object())
    monkeypatch.setattr(adapter, "TranslationConfig", lambda **kwargs: kwargs)
    request = EngineJobRequest(
        job_id="advanced", input_path=str(tmp_path / "input.pdf"),
        output_dir=str(tmp_path), pages="1-2", primary_font_family="sans-serif",
        only_include_translated_page=True, min_text_length=8,
        disable_rich_text_translate=True, skip_translation=True,
    )
    config = build_config(request, EngineApiConfig(model="offline-stub"), tmp_path)
    assert config["primary_font_family"] == "sans-serif"
    assert config["only_include_translated_page"] is True
    assert config["min_text_length"] == 8
    assert config["disable_rich_text_translate"] is True


def test_request_rejects_unknown_field() -> None:
    with pytest.raises(ValueError):
        EngineJobRequest.from_dict(
            {"job_id": "j", "input_path": "i", "output_dir": "o", "unknown": 1}
        )


def test_request_does_not_carry_api_key() -> None:
    payload = EngineJobRequest(
        job_id="j", input_path="i", output_dir="o"
    ).to_dict()
    assert "api_key" not in payload


def test_api_config_redacts_key() -> None:
    config = EngineApiConfig(model="gpt-x", base_url="https://example.test", api_key="sk-secret")
    redacted = config.redacted()
    assert redacted["api_key"] == "<已设置>"
    assert "sk-secret" not in str(redacted)


def test_stage_label_maps_known_and_passes_through_unknown() -> None:
    assert stage_label("Translate Paragraphs") == "翻译段落"
    assert stage_label("Brand New Stage") == "Brand New Stage"
    assert stage_label(None) == "未知阶段"


def test_stage_summary_payload_adds_labels() -> None:
    payload = stage_summary_payload(
        [{"name": "Save PDF", "percent": 6.34}, {"name": "未知", "percent": 1.0}]
    )
    assert payload[0] == {"name": "Save PDF", "label": "保存 PDF", "weight_percent": 6.34}
    assert payload[1]["label"] == "未知"


def test_progress_event_mapping_keeps_engine_numbers() -> None:
    event = to_event(
        {
            "type": "progress_update",
            "stage": "Translate Paragraphs",
            "stage_progress": 42.0,
            "stage_current": 42,
            "stage_total": 100,
            "overall_progress": 61.5,
            "part_index": 1,
            "total_parts": 2,
        }
    )
    assert event is not None
    assert event.type == EVENT_PROGRESS_UPDATE
    assert event.payload["stage_label"] == "翻译段落"
    assert event.payload["overall_progress"] == 61.5
    assert event.overall_progress() == 61.5


def test_stage_summary_event_mapping() -> None:
    event = to_event(
        {
            "type": "stage_summary",
            "stages": [{"name": "DetectScannedFile", "percent": 2.45}],
            "part_index": 1,
            "total_parts": 1,
        }
    )
    assert event is not None
    assert event.type == EVENT_STAGE_SUMMARY
    assert event.payload["stages"][0]["label"] == "检测扫描件"


def test_finish_payload_stringifies_paths() -> None:
    class FakeResult:
        original_pdf_path = Path("C:/tmp/in.pdf")
        mono_pdf_path = Path("C:/tmp/out.mono.pdf")
        dual_pdf_path = None
        no_watermark_mono_pdf_path = None
        no_watermark_dual_pdf_path = None
        auto_extracted_glossary_path = Path("C:/tmp/out.glossary.csv")
        total_seconds = 12.5
        peak_memory_usage = 1024
        total_valid_character_count = 100
        total_valid_text_token_count = 20

    payload = finish_payload(FakeResult())
    assert payload["mono_pdf_path"] == str(Path("C:/tmp/out.mono.pdf"))
    assert payload["dual_pdf_path"] is None
    assert payload["total_seconds"] == 12.5


def test_cancelled_error_class_maps_to_cancelled_event() -> None:
    event = error_event(asyncio.CancelledError)
    assert event.type == EVENT_CANCELLED
    assert event.payload["code"] == "cancelled_by_user"


def test_cancelled_error_instance_maps_to_cancelled_event() -> None:
    assert error_event(asyncio.CancelledError()).type == EVENT_CANCELLED


def test_known_engine_error_gets_hint() -> None:
    class ScannedPDFError(Exception):
        pass

    ScannedPDFError.__name__ = "ScannedPDFError"
    event = error_event(ScannedPDFError("scanned"))
    assert event.type == EVENT_ERROR
    assert event.payload["code"] == ERROR_ENGINE_FAILURE
    assert "扫描件" in event.payload["hint"]


def test_unknown_event_type_is_ignored() -> None:
    assert to_event({"type": "something_new"}) is None
    assert to_event("not-a-dict") is None


def test_finish_event_mapping_has_type() -> None:
    event = to_event({"type": "finish", "translate_result": None})
    assert event is not None
    assert event.type == EVENT_FINISH
