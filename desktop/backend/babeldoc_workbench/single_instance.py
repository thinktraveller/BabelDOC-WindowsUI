"""单实例控制：重复启动时不再开第二个窗口。"""

from __future__ import annotations

import sys
from pathlib import Path

MUTEX_NAME = "Local\\BabelDOC-Workbench-SingleInstance"
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9


class SingleInstance:
    """基于命名互斥体的单实例锁；非 Windows 回退为锁文件。"""

    def __init__(self, name: str = MUTEX_NAME, lock_dir: Path | None = None):
        self.name = name
        self.lock_dir = Path(lock_dir) if lock_dir is not None else None
        self._handle = None
        self._lock_file = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None or self._lock_file is not None

    def acquire(self) -> bool:
        if self.acquired:
            return True
        if sys.platform.startswith("win"):
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateMutexW(None, False, self.name)
            if not handle:
                return False
            if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                return False
            self._handle = handle
            return True
        if self.lock_dir is None:
            return True
        lock_file = self.lock_dir / "workbench.lock"
        try:
            self._lock_file = lock_file.open("x", encoding="utf-8")
        except FileExistsError:
            return False
        return True

    def release(self) -> None:
        if self._handle is not None:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._lock_file is not None:
            path = Path(self._lock_file.name)
            try:
                self._lock_file.close()
            finally:
                self._lock_file = None
                path.unlink(missing_ok=True)


def activate_existing_window(title: str) -> bool:
    """尝试把已有实例的窗口调到前台；找不到窗口时返回 False。"""
    if not sys.platform.startswith("win"):
        return False
    import ctypes

    user32 = ctypes.windll.user32
    handle = user32.FindWindowW(None, title)
    if not handle:
        return False
    user32.ShowWindow(handle, SW_RESTORE)
    user32.SetForegroundWindow(handle)
    return True
