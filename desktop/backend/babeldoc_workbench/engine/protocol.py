"""主进程与工作进程之间的数据协议。

设计约束：

- 所有结构都能被 ``pickle``（多进程管道）与 JSON（后续 REST/SSE）安全处理。
- :class:`EngineJobRequest` 不携带明文 API Key；Key 由主进程在启动工作进程时
  通过 :class:`EngineApiConfig` 单独注入。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from typing import Any

# 事件类型
EVENT_STAGE_SUMMARY = "stage_summary"
EVENT_PROGRESS_START = "progress_start"
EVENT_PROGRESS_UPDATE = "progress_update"
EVENT_PROGRESS_END = "progress_end"
EVENT_FINISH = "finish"
EVENT_CANCELLED = "cancelled"
EVENT_ERROR = "error"

PROGRESS_EVENT_TYPES = frozenset(
    {EVENT_PROGRESS_START, EVENT_PROGRESS_UPDATE, EVENT_PROGRESS_END}
)

# 终态事件：收到其中之一即代表本次任务已有结论
TERMINAL_EVENT_TYPES = frozenset({EVENT_FINISH, EVENT_CANCELLED, EVENT_ERROR})

# 结构化错误码
ERROR_CANCELLED_BY_USER = "cancelled_by_user"
ERROR_ENGINE_FAILURE = "engine_failure"
ERROR_ENGINE_EXIT = "engine_exit"
ERROR_WORKER_CRASH = "worker_crash"
ERROR_ENGINE_STOPPED = "engine_stopped"

# 水印输出模式，取值与引擎的 ``WatermarkOutputMode`` 一致
WATERMARK_MODES = ("watermarked", "no_watermark", "both")
DEFAULT_WATERMARK_MODE = "watermarked"

# 默认参数，与引擎默认值保持一致
DEFAULT_LANG_IN = "en"
DEFAULT_LANG_OUT = "zh"
DEFAULT_QPS = 4
DEFAULT_REPORT_INTERVAL = 0.1


@dataclass(frozen=True)
class EngineApiConfig:
    """单次任务的 API 配置；只在启动工作进程时注入，不写入日志。"""

    model: str = ""
    base_url: str | None = None
    api_key: str | None = None
    reasoning: str | None = None
    thinking: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def redacted(self) -> dict[str, Any]:
        """用于日志的脱敏视图。"""
        return {
            "model": self.model,
            "base_url": self.base_url,
            "api_key": "<已设置>" if self.api_key else None,
            "reasoning": self.reasoning,
            "thinking": self.thinking,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> EngineApiConfig:
        return cls(**_filtered_kwargs(cls, data or {}))


@dataclass(frozen=True)
class EngineJobRequest:
    """一次翻译任务的引擎请求；字段均可序列化。"""

    job_id: str
    input_path: str
    output_dir: str
    lang_in: str = DEFAULT_LANG_IN
    lang_out: str = DEFAULT_LANG_OUT
    pages: str | None = None
    no_mono: bool = False
    no_dual: bool = False
    qps: int = DEFAULT_QPS
    pool_max_workers: int | None = None
    term_pool_max_workers: int | None = None
    report_interval: float = DEFAULT_REPORT_INTERVAL
    max_pages_per_part: int | None = None
    auto_extract_glossary: bool = True
    glossary_files: tuple[str, ...] = ()
    custom_system_prompt: str | None = None
    watermark_output_mode: str = DEFAULT_WATERMARK_MODE
    use_alternating_pages_dual: bool = False
    # 仅用于离线自检：跳过 LLM 翻译阶段，不发任何网络请求
    skip_translation: bool = False
    debug: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EngineJobRequest:
        payload: dict[str, Any] = dict(_filtered_kwargs(cls, data))
        if "glossary_files" in payload:
            payload["glossary_files"] = tuple(payload["glossary_files"] or ())
        missing = [
            name for name in ("job_id", "input_path", "output_dir") if not payload.get(name)
        ]
        if missing:
            raise ValueError(f"引擎请求缺少必填字段：{missing}")
        return cls(**payload)


@dataclass(frozen=True)
class EngineEvent:
    """工作进程回传给主进程的事件。``payload`` 必须可 JSON 序列化。"""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "payload": self.payload}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EngineEvent:
        return cls(type=str(data.get("type", "")), payload=dict(data.get("payload") or {}))

    @classmethod
    def of(cls, event_type: str, **payload: Any) -> EngineEvent:
        return cls(type=event_type, payload=dict(payload))

    def overall_progress(self) -> float | None:
        value = self.payload.get("overall_progress")
        return float(value) if isinstance(value, (int, float)) else None


def engine_error(code: str, message: str, **extra: Any) -> EngineEvent:
    """构造结构化错误事件。"""
    payload: dict[str, Any] = {"code": code, "message": message}
    payload.update({key: value for key, value in extra.items() if value is not None})
    return EngineEvent(type=EVENT_ERROR, payload=payload)


def _filtered_kwargs(cls: type, data: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {item.name for item in fields(cls)}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"未知字段：{sorted(unknown)}")
    return {key: value for key, value in data.items() if key in allowed}
