"""步骤 5 测试用的工作进程替身。

真实引擎的运行结果单独记录；这里的替身只用于验证队列、状态机与取消语义。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from babeldoc_workbench.services.worker_runner import WorkerSummary


class ConcurrencyTracker:
    """记录同时运行的任务数，用于验证“同一时刻只有一个任务在运行”。"""

    def __init__(self) -> None:
        self.current = 0
        self.max_seen = 0
        self.order: list[str] = []
        self._lock = threading.Lock()

    def enter(self, job_id: str) -> None:
        with self._lock:
            self.current += 1
            self.max_seen = max(self.max_seen, self.current)
            self.order.append(job_id)

    def leave(self) -> None:
        with self._lock:
            self.current -= 1


def make_fake_runner(
    *,
    tracker: ConcurrencyTracker | None = None,
    step_delay: float = 0.05,
    write_outputs: bool = True,
    fail: dict[str, Any] | None = None,
    finish: bool = True,
    wait_for_cancel: bool = False,
    block_until_cancel: bool = False,
    block_seconds: float = 20.0,
) -> Callable[..., WorkerSummary]:
    """构造一个行为可控的 runner，签名与 :func:`run_worker_process` 一致。"""

    def runner(
        *,
        request_dict: dict[str, Any],
        api_dict: dict[str, Any],
        on_event: Callable[[dict[str, Any]], None] | None = None,
        cancel_event=None,
        cancel_timeout: float = 10.0,
        terminate_timeout: float = 5.0,
        process_name: str = "fake-worker",
        on_started=None,
        on_finished=None,
    ) -> WorkerSummary:
        if on_started is not None:
            on_started(0)
        summary = WorkerSummary()
        job_id = str(request_dict.get("job_id"))
        output_dir = Path(request_dict["output_dir"])
        if tracker is not None:
            tracker.enter(job_id)
        try:
            if block_until_cancel:
                # 先报一次进度，然后一直等到取消请求（用于确定性的取消测试）
                if on_event:
                    on_event(
                        {
                            "type": "progress_update",
                            "payload": {
                                "stage": "Parse Page Layout",
                                "stage_label": "分析页面版面",
                                "stage_progress": 30.0,
                                "overall_progress": 30.0,
                            },
                        }
                    )
                summary.stage = "Parse Page Layout"
                summary.progress = 30.0
                deadline = time.monotonic() + block_seconds
                while time.monotonic() < deadline:
                    if cancel_event is not None and cancel_event.is_set():
                        summary.cancelled = True
                        return summary
                    time.sleep(0.05)

            stages = [
                ("Parse PDF and Create Intermediate Representation", 10.0),
                ("Parse Page Layout", 40.0),
                ("Translate Paragraphs", 80.0),
            ]
            for index, (stage, progress) in enumerate(stages):
                if cancel_event is not None and cancel_event.is_set():
                    summary.cancelled = True
                    if on_event:
                        on_event(
                            {
                                "type": "cancelled",
                                "payload": {
                                    "code": "cancelled_by_user",
                                    "message": "任务已取消",
                                    "stage": stage,
                                },
                            }
                        )
                    return summary
                if on_event:
                    on_event(
                        {
                            "type": "progress_update",
                            "payload": {
                                "stage": stage,
                                "stage_label": stage,
                                "stage_progress": progress,
                                "overall_progress": progress,
                            },
                        }
                    )
                summary.stage = stage
                summary.progress = progress
                time.sleep(step_delay)
                if wait_for_cancel and index == len(stages) - 1:
                    deadline = time.monotonic() + 10.0
                    while time.monotonic() < deadline:
                        if cancel_event is not None and cancel_event.is_set():
                            summary.cancelled = True
                            return summary
                        time.sleep(0.05)

            if cancel_event is not None and cancel_event.is_set():
                summary.cancelled = True
                return summary

            if fail:
                summary.error = fail
                if on_event:
                    on_event({"type": "error", "payload": fail})
                return summary

            if write_outputs:
                output_dir.mkdir(parents=True, exist_ok=True)
                mono = output_dir / "sample.zh.mono.pdf"
                dual = output_dir / "sample.zh.dual.pdf"
                glossary = output_dir / "sample.zh.glossary.csv"
                mono.write_bytes(b"%PDF-1.4 fake mono")
                dual.write_bytes(b"%PDF-1.4 fake dual")
                glossary.write_text("source,target,tgt_lng\n", encoding="utf-8")

            summary.finished = finish
            summary.finish = {
                "mono_pdf_path": str(output_dir / "sample.zh.mono.pdf"),
                "dual_pdf_path": str(output_dir / "sample.zh.dual.pdf"),
            }
            if on_event:
                on_event({"type": "finish", "payload": summary.finish})
            return summary
        finally:
            if tracker is not None:
                tracker.leave()
            if on_finished is not None:
                on_finished(0)

    return runner
