"""组装便携目录：把 PyInstaller 产物整理成可解压即用的交付目录。

用法::

    .\\.venv\\Scripts\\python.exe desktop\\packaging\\build_portable.py
    .\\.venv\\Scripts\\python.exe desktop\\packaging\\build_portable.py --resources <离线资源包>

产物：``release\\BabelDOC-portable\\``，含
``BabelDOC.exe``、``_internal\\``、``frontend_dist\\``、``使用说明.md`` 与 ``校验值.sha256``。
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = REPO_ROOT / "dist" / "BabelDOC"
RELEASE_DIR = REPO_ROOT / "release"
PACKAGING_DIR = Path(__file__).resolve().parent
DOCS = ("使用说明.md", "源码构建.md", "验收记录.md")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="组装便携目录")
    parser.add_argument("--output", default=str(RELEASE_DIR / "BabelDOC-portable"))
    parser.add_argument(
        "--resources",
        default=None,
        help="可选的离线资源包（zip 或目录），会复制到 resources\\ 下",
    )
    return parser.parse_args()


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def directory_size(path: Path) -> int:
    return sum(
        item.stat().st_size for item in path.rglob("*") if item.is_file()
    )


def main() -> int:
    args = parse_args()
    if not (DIST_DIR / "BabelDOC.exe").is_file():
        print("❌ 找不到 PyInstaller 产物，请先执行：")
        print(
            r"   .\.venv\Scripts\python.exe -m PyInstaller desktop\packaging\babeldoc.spec "
            r"--noconfirm --distpath dist --workpath .tmp\pyi"
        )
        return 2

    target = Path(args.output)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(DIST_DIR, target)

    # 前端产物放到顶层，便于用户查看与替换；应用会优先从 _internal 读取，其次读顶层
    internal_frontend = target / "_internal" / "frontend_dist"
    top_frontend = target / "frontend_dist"
    if internal_frontend.is_dir() and not top_frontend.exists():
        shutil.move(str(internal_frontend), str(top_frontend))
    elif internal_frontend.is_dir():
        shutil.rmtree(internal_frontend)

    for name in DOCS:
        source = PACKAGING_DIR / name
        if source.is_file():
            shutil.copyfile(source, target / name)

    if args.resources:
        source = Path(args.resources).expanduser()
        if not source.exists():
            print(f"❌ 找不到资源包：{source}")
            return 2
        resources_dir = target / "resources"
        resources_dir.mkdir(exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, resources_dir / source.name)
        else:
            shutil.copyfile(source, resources_dir / source.name)

    exe = target / "BabelDOC.exe"
    checksum = f"{sha256_of_file(exe)}  BabelDOC.exe\n"
    (target / "校验值.sha256").write_text(checksum, encoding="utf-8")

    size_mb = directory_size(target) / 1024 / 1024
    files = sum(1 for item in target.rglob("*") if item.is_file())
    print("便携目录已生成")
    print("-" * 60)
    print(f"路径：{target}")
    print(f"体积：{size_mb:.1f} MB（{files} 个文件）")
    print(f"BabelDOC.exe 校验值：{checksum.split()[0][:16]}…")
    print(f"前端产物：{'frontend_dist 存在' if (target / 'frontend_dist').is_dir() else '缺失'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
