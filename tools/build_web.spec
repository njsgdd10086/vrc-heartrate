# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置: 摄像头心率 → VRChat (Gradio 网页版)

用法（在仓库根目录执行）:
    python -m PyInstaller --distpath dist --workpath build \
      --noconfirm tools/build_web.spec
    python tools/fix_msvc_dlls.py dist/VRCHeartRateWeb      # 必须，见 docs/BUILD.md

要点:
  1) 模型文件和 gradio 前端资源必须作为 data 收集，否则运行时报缺文件
  2) onnxruntime 的 DLL 必须完整，且打包后要用 fix_msvc_dlls.py 修正
     MSVC 运行时（否则报 "DLL 初始化例程失败"，原因见 docs/BUILD.md 坑 1）
  3) 排除 torch/tensorflow 等无关大依赖，显著减小体积
  4) pyvirtualcam 是 GPL-2.0 —— 要以 MIT 分发就删掉第 3c 段
"""
import os
import re
from PyInstaller.utils.hooks import (collect_data_files, collect_submodules,
                                     collect_dynamic_libs)

# 仓库根目录 = 本 spec 所在目录的上一层
# （用相对定位，避免写死路径导致别人克隆到别处就构建失败）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))

datas = []
binaries = []
hiddenimports = []

# --- 1) 模型文件 ---
# 项目自包含: models/ 下两个模型即可，不再依赖 FacePhys-Release 目录
datas += [(os.path.join(ROOT, "models", "FacePhys.onnx"), "models")]
datas += [(os.path.join(ROOT, "models", "face_detection_yunet_2023mar.onnx"),
           "models")]

# --- 2) gradio 前端资源（templates 里有 700+ 个文件，缺一个页面就白屏）---
datas += collect_data_files("gradio", includes=["templates/**",
                                                "themes/**",
                                                "_frontend_code/**",
                                                "*.json",
                                                "*.txt"])
hiddenimports += collect_submodules("gradio")

# --- 2b) gradio_client: gradio.components 会导入它，且它自带 types.json，
#          漏掉会报 FileNotFoundError: gradio_client\types.json ---
datas += collect_data_files("gradio_client")
hiddenimports += collect_submodules("gradio_client")

# --- 3) onnxruntime: DLL 必须用 collect_dynamic_libs 收（数据文件收不到 DLL）---
datas += collect_data_files("onnxruntime")
binaries += collect_dynamic_libs("onnxruntime")
hiddenimports += ["onnxruntime", "onnxruntime.capi",
                  "onnxruntime.capi._pybind_state",
                  "onnxruntime.capi.onnxruntime_pybind11_state"]

# --- 3b) 修正 MSVC 运行时 DLL ---
# 实测踩坑: PyInstaller 的依赖分析会从无关目录（本例是
# C:\Program Files\Java\jdk-1.8\bin）抓一份老旧的 msvcp140.dll 打进包根目录。
# 程序启动时这份旧 DLL 先被加载，而它缺少 onnxruntime 需要的
# api-ms-win-core-path 转发，导致:
#     ImportError: DLL load failed while importing
#     onnxruntime_pybind11_state: 动态链接库(DLL)初始化例程失败
# 而且报错指向 .pyd，极易误判。修法: 把所有 MSVC 运行时副本替换成
# 系统 System32 里的正确版本（随 VC++ 2015-2022 运行库提供）。
# 匹配规则: msvcp140.dll / MSVCP140_1.dll / msvcp140-<hash>.dll 都要抓到，
# 因为 pandas、matplotlib 会各带一份哈希后缀的副本。
_MSVC_RE = re.compile(
    r"^(msvcp140|vcruntime140|concrt140)(_\d+)?(-[0-9a-f]{16,})?\.dll$", re.I)
_MSVC_CANON = re.compile(r"^(msvcp140|vcruntime140|concrt140)(_\d+)?", re.I)
_SYS32 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32")


def _msvc_key(dest):
    """把 '_1'、'-hash' 等归一成真正的 DLL 名（用于去 System32 取正确版本）"""
    b = os.path.basename(dest)
    m = _MSVC_CANON.match(b)
    if not m:
        return None
    return f"{m.group(1).lower()}{m.group(2) or ''}.dll"


_removed, _kept = [], []
for _b in binaries:
    if _MSVC_RE.match(os.path.basename(_b[0])):
        _removed.append((os.path.basename(_b[0]), _b[1]))
    else:
        _kept.append(_b)
binaries = _kept

if _removed:
    print(f"[spec] 移除 {len(_removed)} 个来源不可靠的 MSVC 运行时副本:")
    for _n, _src in _removed:
        print(f"[spec]    {_n}  <-  {_src}")

_added = set()
for _n, _src in _removed:
    _k = _msvc_key(_n)
    if not _k or _k in _added:
        continue
    _p = os.path.join(_SYS32, _k)
    if os.path.isfile(_p):
        binaries.append((_p, "."))
        _added.add(_k)
        print(f"[spec] 改用系统版本: {_k}")

# --- 3c) pyvirtualcam: 虚拟摄像头输出 ---
# 它自带原生 DLL（Windows 上是 OBS 虚拟摄像头协议的实现），
# 不收集的话运行时会报找不到后端。
# 注意: pyvirtualcam 是 GPL-2.0。要以 MIT 分发就删掉这一段，
#       核心测心率功能不依赖它（代码里是延迟导入）。
datas += collect_data_files("pyvirtualcam")
binaries += collect_dynamic_libs("pyvirtualcam")
hiddenimports += ["pyvirtualcam", "pyvirtualcam.camera"]

# 本项目自己的模块（facephys.py 是动态导入的，必须显式声明）
hiddenimports += ["facephys"]

# --- 4) scipy 子模块（用到 signal / interpolate / fft）---
hiddenimports += ["scipy.signal", "scipy.interpolate", "scipy.fft",
                  "scipy.special", "scipy._lib.messagestream"]

# --- 5) 其他运行时依赖 ---
hiddenimports += ["uvicorn", "uvicorn.logging", "uvicorn.loops",
                  "uvicorn.loops.auto", "uvicorn.protocols",
                  "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
                  "uvicorn.protocols.websockets",
                  "uvicorn.protocols.websockets.auto",
                  "uvicorn.lifespan", "uvicorn.lifespan.on",
                  "websockets", "aiofiles", "anyio", "httpx", "orjson",
                  "markupsafe", "jinja2", "PIL",
                  # gr.LinePlot（BPM 波形图）依赖 matplotlib，gradio/utils.py
                  # 在导入期就会用到它
                  "matplotlib", "matplotlib.pyplot", "matplotlib.figure",
                  "matplotlib.backends.backend_agg"]

# --- 6) gradio 的第三方依赖树：逐个试太慢，直接全量收集 ---
# 教训: gradio 各组件会在导入期就 import 一堆库（matplotlib / pandas /
# gradio_client ...），漏一个就在启动时报 ModuleNotFoundError。
# 这里把它们整棵依赖树收进来，只靠 EXCLUDES 砍掉确定不需要的重型框架。
for _pkg in ("pandas", "numpy", "scipy", "PIL", "matplotlib", "gradio_client",
             "fastapi", "starlette", "pydantic", "anyio", "httpx", "uvicorn",
             "websockets", "aiofiles", "jinja2", "markupsafe", "orjson",
             "python_multipart", "rich", "typer", "click", "packaging",
             "semantic_version", "tomlkit", "yaml", "fsspec", "huggingface_hub",
             "safehttpx", "groovy", "ffmpy", "pydub", "audioop"):
    try:
        hiddenimports += collect_submodules(_pkg)
    except Exception:
        pass

EXCLUDES = [
    # 只排除与本程序无关的重型框架
    "torch", "torchvision", "torchaudio",
    "tensorflow", "keras", "jax", "jaxlib",
    "transformers", "datasets", "accelerate",
    "numba", "llvmlite",
    "IPython", "notebook", "jupyter", "nbformat", "nbconvert",
    "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "tensorboard", "onnx", "sympy", "networkx",
]

a = Analysis(
    [os.path.join(ROOT, "osc_heartrate_cam.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,       # onedir 模式：二进制交给 COLLECT 管理
    name="VRCHeartRateWeb",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

# 用 onedir 而不是 onefile。
# 原因: onefile 会把所有 DLL 平铺到同一个临时目录，onnxruntime 的
# onnxruntime_pybind11_state 与其它包的同名库冲突，实测报
# "DLL load failed ... 动态链接库(DLL)初始化例程失败"。
# onedir 保留各包的目录结构，DLL 互不干扰；而且启动无需解压上百 MB，更快。
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VRCHeartRateWeb",
)
