"""请求转换为 ``TranslationConfig``，并把引擎事件流映射为应用事件。

本模块只在工作进程中导入：引擎在资源下载失败时会直接 ``exit(1)``，不能放在
界面进程里执行。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from babeldoc.docvision.doclayout import DocLayoutModel
from babeldoc.format.pdf.high_level import async_translate
from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
from babeldoc.glossary import Glossary
from babeldoc.translator.translator import (
    BaseTranslator,
    OpenAITranslator,
    set_translate_rate_limiter,
)

from babeldoc_workbench.engine.progress import stage_label, stage_summary_payload
from babeldoc_workbench.engine.protocol import (
    ERROR_CANCELLED_BY_USER,
    ERROR_ENGINE_FAILURE,
    ERROR_ENGINE_STOPPED,
    EVENT_CANCELLED,
    EVENT_ERROR,
    EVENT_FINISH,
    EVENT_STAGE_SUMMARY,
    PROGRESS_EVENT_TYPES,
    TERMINAL_EVENT_TYPES,
    EngineApiConfig,
    EngineEvent,
    EngineJobRequest,
)

logger = logging.getLogger(__name__)

# 引擎异常到用户可读提示的映射；未命中时使用原始消息
ERROR_HINTS: dict[str, str] = {
    "ScannedPDFError": "该 PDF 可能是扫描件，当前版本无法直接翻译其中的文字。",
    "ExtractTextError": "无法从该 PDF 提取文字，文件可能已加密或结构异常。",
    "InputFileGeneratedByBabelDOCError": "输入文件像是 BabelDOC 生成的译文，请改用原始文件。",
    "ContentFilterError": "模型服务触发了内容过滤，请检查文档内容或更换模型服务。",
    "APIConnectionError": "无法连接模型服务，请检查 Base URL 与网络。",
    "AuthenticationError": "模型服务认证失败，请检查 API Key。",
    "NotFoundError": "模型服务返回模型不存在，请检查模型名。",
    "PermissionDeniedError": "模型服务拒绝访问，请检查账号权限或模型可用范围。",
}


class OfflineStubTranslator(BaseTranslator):
    """离线占位翻译器，只在 ``skip_translation`` 自检模式下使用。"""

    name = "offline-stub"

    def __init__(self, lang_in: str, lang_out: str):
        super().__init__(lang_in, lang_out, True)

    def do_llm_translate(self, text, rate_limit_params: dict | None = None):
        return text

    def do_translate(self, text, rate_limit_params: dict | None = None):
        return text


def build_translator(
    request: EngineJobRequest, api: EngineApiConfig
) -> BaseTranslator:
    """构造翻译器；离线自检模式不触发任何网络请求。"""
    if request.skip_translation:
        return OfflineStubTranslator(request.lang_in, request.lang_out)
    if not api.model:
        raise ValueError("缺少模型名，无法创建翻译器")
    if not api.api_key:
        raise ValueError("缺少 API Key，无法创建翻译器")
    return OpenAITranslator(
        lang_in=request.lang_in,
        lang_out=request.lang_out,
        model=api.model,
        base_url=api.base_url or None,
        api_key=api.api_key,
        reasoning=api.reasoning or None,
        thinking=api.thinking or None,
    )


def build_config(
    request: EngineJobRequest,
    api: EngineApiConfig,
    output_dir: Path,
    working_dir: Path | None = None,
) -> TranslationConfig:
    """把应用请求转换为引擎配置。

    ``set_translate_rate_limiter()`` 是模块级全局状态，因此一个工作进程只允许
    承载一个任务，且必须在构造翻译器之前设置。
    """
    set_translate_rate_limiter(request.qps)
    translator = build_translator(request, api)
    glossaries = [
        Glossary.from_csv(Path(path), request.lang_out)
        for path in request.glossary_files
    ]
    split_strategy = (
        TranslationConfig.create_max_pages_per_part_split_strategy(
            request.max_pages_per_part
        )
        if request.max_pages_per_part
        else None
    )
    return TranslationConfig(
        translator=translator,
        input_file=request.input_path,
        lang_in=request.lang_in,
        lang_out=request.lang_out,
        doc_layout_model=DocLayoutModel.load_available(),
        output_dir=output_dir,
        working_dir=working_dir or output_dir,
        pages=request.pages,
        qps=request.qps,
        pool_max_workers=request.pool_max_workers,
        term_pool_max_workers=request.term_pool_max_workers,
        no_mono=request.no_mono,
        no_dual=request.no_dual,
        auto_extract_glossary=request.auto_extract_glossary,
        glossaries=glossaries,
        custom_system_prompt=request.custom_system_prompt,
        watermark_output_mode=WatermarkOutputMode(request.watermark_output_mode),
        split_strategy=split_strategy,
        use_alternating_pages_dual=request.use_alternating_pages_dual,
        report_interval=request.report_interval,
        skip_translation=request.skip_translation,
        debug=request.debug,
    )


@dataclass
class EngineRunState:
    """一次引擎运行的结果摘要，用于工作进程决定退出码。"""

    finished: bool = False
    cancelled: bool = False
    cancel_event_sent: bool = False
    error: EngineEvent | None = None
    last_stage: str | None = None
    seen_stages: list[str] = field(default_factory=list)


def is_cancelled_error(error: Any) -> bool:
    """引擎会用 ``error`` 事件回传 ``CancelledError``（类或实例）表示取消。"""
    if error is None:
        return False
    if isinstance(error, type):
        return issubclass(error, asyncio.CancelledError)
    if isinstance(error, asyncio.CancelledError):
        return True
    return type(error).__name__ == "CancelledError"


def error_event(error: Any) -> EngineEvent:
    """把引擎抛出的对象转换为结构化错误事件。"""
    if is_cancelled_error(error):
        return EngineEvent.of(
            EVENT_CANCELLED,
            code=ERROR_CANCELLED_BY_USER,
            message="任务已取消",
        )
    if isinstance(error, BaseException):
        error_type = type(error).__name__
        message = str(error) or error_type
    else:
        error_type = type(error).__name__
        message = str(error)
    payload: dict[str, Any] = {
        "code": ERROR_ENGINE_FAILURE,
        "error_type": error_type,
        "message": message,
    }
    hint = ERROR_HINTS.get(error_type)
    if hint:
        payload["hint"] = hint
    return EngineEvent(type=EVENT_ERROR, payload=payload)


def finish_payload(result: Any) -> dict[str, Any]:
    """把 ``TranslateResult`` 转换为可 JSON 序列化的成果摘要。"""
    if result is None:
        return {}

    def _path(name: str) -> str | None:
        value = getattr(result, name, None)
        return str(value) if value is not None else None

    return {
        "original_pdf_path": _path("original_pdf_path"),
        "mono_pdf_path": _path("mono_pdf_path"),
        "dual_pdf_path": _path("dual_pdf_path"),
        "no_watermark_mono_pdf_path": _path("no_watermark_mono_pdf_path"),
        "no_watermark_dual_pdf_path": _path("no_watermark_dual_pdf_path"),
        "auto_extracted_glossary_path": _path("auto_extracted_glossary_path"),
        "total_seconds": getattr(result, "total_seconds", None),
        "peak_memory_usage": getattr(result, "peak_memory_usage", None),
        "total_valid_character_count": getattr(
            result, "total_valid_character_count", None
        ),
        "total_valid_text_token_count": getattr(
            result, "total_valid_text_token_count", None
        ),
    }


def to_event(raw: Any) -> EngineEvent | None:
    """把引擎原始事件字典映射为应用事件。"""
    if not isinstance(raw, dict):
        return None
    event_type = raw.get("type")
    if event_type == EVENT_STAGE_SUMMARY:
        return EngineEvent.of(
            EVENT_STAGE_SUMMARY,
            stages=stage_summary_payload(raw.get("stages") or []),
            part_index=raw.get("part_index"),
            total_parts=raw.get("total_parts"),
        )
    if event_type in PROGRESS_EVENT_TYPES:
        stage = raw.get("stage")
        return EngineEvent.of(
            str(event_type),
            stage=stage,
            stage_label=stage_label(stage),
            stage_progress=raw.get("stage_progress"),
            stage_current=raw.get("stage_current"),
            stage_total=raw.get("stage_total"),
            overall_progress=raw.get("overall_progress"),
            part_index=raw.get("part_index"),
            total_parts=raw.get("total_parts"),
        )
    if event_type == EVENT_FINISH:
        return EngineEvent.of(EVENT_FINISH, **finish_payload(raw.get("translate_result")))
    if event_type == EVENT_ERROR:
        return error_event(raw.get("error"))
    logger.warning("忽略未知的引擎事件类型：%r", event_type)
    return None


async def _consume(
    config: TranslationConfig, emit, state: EngineRunState
) -> None:
    """消费引擎事件流，出错时转换为结构化事件而不是裸异常。"""
    try:
        async for raw in async_translate(config):
            event = to_event(raw)
            if event is None:
                continue
            if event.type in PROGRESS_EVENT_TYPES:
                stage = event.payload.get("stage")
                if stage and stage not in state.seen_stages:
                    state.seen_stages.append(stage)
                state.last_stage = stage or state.last_stage
            elif event.type == EVENT_FINISH:
                state.finished = True
            elif event.type == EVENT_CANCELLED:
                state.cancelled = True
                state.cancel_event_sent = True
            elif event.type == EVENT_ERROR:
                state.error = event
            emit(event)
            if event.type in TERMINAL_EVENT_TYPES:
                # 引擎在终态事件之后仍会 await 内部 finish_event，实测可能长时间不返回
                # （仓库自带的 executor 适配器同样在收到终态事件后立即返回，见
                # babeldoc/tools/executor/babeldoc_adapter.py 的 _run_async_translate）。
                # 此时产物已全部落盘，这里结束消费，由父进程按事件判定结果。
                break
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 - 统一转成结构化错误回传
        logger.exception("引擎事件流异常终止")
        state.error = error_event(exc)
        emit(state.error)


async def run_job(
    request: EngineJobRequest,
    api: EngineApiConfig,
    emit,
    *,
    working_dir: Path | None = None,
    cancel_event=None,
    poll_interval: float = 0.2,
    cancel_grace_seconds: float = 20.0,
    exit_grace_seconds: float = 5.0,
) -> EngineRunState:
    """执行一次翻译任务，直到完成、取消或失败。

    取消语义：取消是“停止当前任务”，不保存检查点，也不产生成功标记。
    引擎内部的 ``async_translate()`` 会吞掉外部取消并转为协作式取消，因此这里
    主动取消消费任务，并以 :class:`EngineRunState` 判断最终结果。
    """
    output_dir = Path(request.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if working_dir is not None:
        working_dir.mkdir(parents=True, exist_ok=True)

    config = build_config(request, api, output_dir, working_dir)
    state = EngineRunState()
    task = asyncio.ensure_future(_consume(config, emit, state))

    while True:
        done, _pending = await asyncio.wait({task}, timeout=poll_interval)
        if done:
            break
        if cancel_event is not None and cancel_event.is_set():
            state.cancelled = True
            task.cancel()
            finished, _pending = await asyncio.wait(
                {task}, timeout=cancel_grace_seconds
            )
            if not finished:
                logger.warning(
                    "取消后引擎在 %.0f 秒内未退出，交由父进程终止工作进程",
                    cancel_grace_seconds,
                )
            break

    if task.done():
        try:
            await task
        except asyncio.CancelledError:
            state.cancelled = True
        except BaseException as exc:  # noqa: BLE001 - 统一转成结构化错误回传
            state.error = error_event(exc)
            emit(state.error)
    else:
        # 已经拿到终态事件但引擎尚未收尾：给一小段收尾时间，超时后交由父进程处理
        task.cancel()
        finished, _pending = await asyncio.wait({task}, timeout=exit_grace_seconds)
        if not finished:
            logger.warning(
                "引擎在终态事件后 %.0f 秒内仍未收尾，工作进程将按退出码结束",
                exit_grace_seconds,
            )

    if state.cancelled and not state.cancel_event_sent:
        emit(
            EngineEvent.of(
                EVENT_CANCELLED,
                code=ERROR_CANCELLED_BY_USER,
                message="任务已取消",
                stage=state.last_stage,
                stage_label=stage_label(state.last_stage),
            )
        )
    if not state.finished and not state.cancelled and state.error is None:
        emit(
            EngineEvent.of(
                EVENT_ERROR,
                code=ERROR_ENGINE_STOPPED,
                error_type="EngineStopped",
                message="引擎在未报告完成的情况下结束，可能被资源检查或外部信号中断",
                stage=state.last_stage,
                stage_label=stage_label(state.last_stage),
            )
        )
    return state
