# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller 单文件打包配置。

构建命令（仓库根目录执行）::

    .\.venv\Scripts\python.exe -m PyInstaller desktop\packaging\babeldoc_onefile.spec ^
        --noconfirm --distpath release --workpath .tmp\pyi-onefile
"""

import os

from PyInstaller.utils.hooks import (
    collect_all,
    collect_delvewheel_libs_directory,
    collect_submodules,
    copy_metadata,
)

SPEC_DIR = os.path.abspath(SPECPATH)  # noqa: F821 - PyInstaller 注入
PROJECT_ROOT = os.path.abspath(os.path.join(SPEC_DIR, "..", ".."))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "desktop", "backend")
FRONTEND_DIST = os.path.join(PROJECT_ROOT, "desktop", "frontend", "dist")
ENTRY_SCRIPT = os.path.join(BACKEND_DIR, "babeldoc_workbench", "main.py")
# 原项目 logo：docs/images/babeldoc-small-logo-with-transparent-background.png
# 此 ICO 由该图生成 16–256 px 图层，用于 Windows EXE 资源。
APP_ICON = os.path.join(SPEC_DIR, "assets", "babeldoc.ico")

datas: list = []
binaries: list = []
hiddenimports: list = []

# 引擎与关键原生依赖：显式收集，避免动态加载的 DLL/数据文件被漏掉
for package in ("babeldoc", "onnxruntime", "tiktoken", "pymupdf", "cv2", "hyperscan"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# hyperscan 的 Windows wheel 把 _hs_ext 依赖的 DLL 放在包目录旁的
# hyperscan.libs；collect_all("hyperscan") 不会扫描这个同级目录。
datas, binaries = collect_delvewheel_libs_directory(
    "hyperscan", datas=datas, binaries=binaries
)

hiddenimports += collect_submodules("babeldoc.docvision")
# 工作进程入口通过 multiprocessing 目标函数引用，静态分析看不到，必须显式收集
hiddenimports += collect_submodules("babeldoc_workbench")

# tiktoken 通过 entry point 发现 tiktoken_ext.openai_public，冻结后必须带上
# dist-info（供 importlib.metadata 读取入口点）与插件包本身
try:
    datas += copy_metadata("tiktoken")
    plugin_datas, plugin_binaries, plugin_hidden = collect_all("tiktoken_ext")
    datas += plugin_datas
    binaries += plugin_binaries
    hiddenimports += plugin_hidden
except Exception:
    pass

# keyring 通过 entry point 发现 Windows 凭据后端，冻结后同样需要 dist-info
try:
    datas += copy_metadata("keyring")
    keyring_datas, keyring_binaries, keyring_hidden = collect_all("keyring")
    datas += keyring_datas
    binaries += keyring_binaries
    hiddenimports += keyring_hidden
except Exception:
    pass

# 其他含原生扩展的依赖，显式收集以降低漏 DLL 的概率
for package in (
    "Levenshtein",
    "uharfbuzz",
    "freetype",
    "pyzstd",
    "rtree",
    "sklearn",
    # bitstring 通过 bitstore_bitarray 之类子模块延迟导入，冻结后必须显式收集
    "bitstring",
    "xsdata",
    "charset_normalizer",
    "chardet",
    "msgpack",
    "regex",
):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
    except Exception:
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# 前端资源须随单文件 EXE 一同打入，解包后由 _MEIPASS 路径读取。
if os.path.isdir(FRONTEND_DIST):
    datas.append((FRONTEND_DIST, "frontend_dist"))
else:
    raise FileNotFoundError(f"前端尚未构建：{FRONTEND_DIST}")

# 完整离线版由构建命令指定经引擎校验的模型/字体资源包。
# 不指定时仍可构建较小的联网首次准备版。
asset_archive = os.environ.get("BABELDOC_ONEFILE_ASSET_ARCHIVE")
if asset_archive:
    asset_archive = os.path.abspath(asset_archive)
    if not os.path.isfile(asset_archive) or not os.path.basename(asset_archive).startswith(
        "offline_assets_"
    ) or not asset_archive.endswith(".zip"):
        raise FileNotFoundError(f"无效的离线资源包：{asset_archive}")
    datas.append((asset_archive, "offline_assets"))

a = Analysis(  # noqa: F821 - PyInstaller 注入
    [ENTRY_SCRIPT],
    pathex=[BACKEND_DIR],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

# 排查打包问题时设置环境变量 BABELDOC_CONSOLE_BUILD=1 可看到原生库加载错误
CONSOLE_BUILD = os.environ.get("BABELDOC_CONSOLE_BUILD") == "1"

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BabelDOC-WindowsUI",
    icon=APP_ICON,
    console=CONSOLE_BUILD,
    disable_windowed_traceback=False,
    strip=False,
    upx=False,
)
