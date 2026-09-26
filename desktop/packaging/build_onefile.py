r"""构建含运行时、前端、模型和字体的单文件 Windows EXE。

从仓库根目录执行：
    .\.venv\Scripts\python.exe desktop\packaging\build_onefile.py

资源包暂存到被 Git 忽略的 .tmp/；交付物仅为 release/BabelDOC-WindowsUI.exe。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGING = Path(__file__).resolve().parent
ASSETS_DIR = ROOT / ".tmp" / "onefile-assets"
OUTPUT = ROOT / "release" / "BabelDOC-WindowsUI.exe"


def main() -> int:
    if not (ROOT / "desktop" / "frontend" / "dist" / "index.html").is_file():
        raise FileNotFoundError("前端尚未构建：请先在 desktop/frontend 运行 npm run build")

    from babeldoc.assets.assets import (
        generate_offline_assets_package,
        get_offline_assets_tag,
    )

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    archive = ASSETS_DIR / f"offline_assets_{get_offline_assets_tag()}.zip"
    generate_offline_assets_package(ASSETS_DIR)
    if not archive.is_file():
        raise FileNotFoundError(f"资源包未生成：{archive}")

    environment = os.environ.copy()
    environment["BABELDOC_ONEFILE_ASSET_ARCHIVE"] = str(archive)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(PACKAGING / "babeldoc_onefile.spec"),
            "--noconfirm",
            "--distpath",
            str(ROOT / "release"),
            "--workpath",
            str(ROOT / ".tmp" / "pyi-onefile-full"),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    with OUTPUT.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    print(f"单文件 EXE：{OUTPUT}")
    print(f"大小：{OUTPUT.stat().st_size / 1024 / 1024:.1f} MiB")
    print(f"SHA256：{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
