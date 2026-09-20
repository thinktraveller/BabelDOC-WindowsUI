# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 目录模式打包配置（步骤 2 门槛验证用）。

构建命令（仓库根目录执行）::

    .\.venv\Scripts\python.exe -m PyInstaller desktop\packaging\babeldoc.spec ^
        --noconfirm --distpath dist --workpath .tmp\pyi
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

SPEC_DIR = os.path.abspath(SPECPATH)  # noqa: F821 - PyInstaller 注入
PROJECT_ROOT = os.path.abspath(os.path.join(SPEC_DIR, "..", ".."))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "desktop", "backend")
FRONTEND_DIST = os.path.join(PROJECT_ROOT, "desktop", "frontend", "dist")
ENTRY_SCRIPT = os.path.join(BACKEND_DIR, "babeldoc_workbench", "main.py")

datas: list = []
binaries: list = []
hiddenimports: list = []

# 引擎与关键原生依赖：显式收集，避免动态加载的 DLL/数据文件被漏掉
for package in ("babeldoc", "onnxruntime", "tiktoken", "pymupdf", "cv2", "hyperscan"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

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

# 前端产物在步骤 6 才会出现；存在时才打入，避免早期构建失败
if os.path.isdir(FRONTEND_DIST):
    datas.append((FRONTEND_DIST, "frontend_dist"))

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
    [],
    exclude_binaries=True,
    name="BabelDOC",
    console=CONSOLE_BUILD,
    disable_windowed_traceback=False,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="BabelDOC",
)
