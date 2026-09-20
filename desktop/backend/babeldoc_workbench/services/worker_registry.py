"""工作进程登记表：进程启动时登记 PID，退出时注销。

用途：应用被强制结束后，重启时能精确找出上次残留的工作进程，而不是靠进程名猜测。
"""

from __future__ import annotations

import json
import logging

from babeldoc_workbench.models import AppSetting

logger = logging.getLogger(__name__)

REGISTRY_KEY = "active_worker_pids"


def _read() -> list[int]:
    row = AppSetting.get_or_none(AppSetting.key == REGISTRY_KEY)
    if row is None or not row.value:
        return []
    try:
        data = json.loads(row.value)
    except json.JSONDecodeError:
        return []
    return [int(item) for item in data if str(item).isdigit()]


def _write(pids: list[int]) -> None:
    value = json.dumps(sorted(set(pids)))
    row, created = AppSetting.get_or_create(key=REGISTRY_KEY, defaults={"value": value})
    if not created:
        row.value = value
        row.save()


def register(pid: int | None) -> None:
    if not pid:
        return
    pids = _read()
    pids.append(int(pid))
    _write(pids)


def unregister(pid: int | None) -> None:
    if not pid:
        return
    _write([item for item in _read() if item != int(pid)])


def list_pids() -> list[int]:
    return _read()


def clear() -> None:
    _write([])
