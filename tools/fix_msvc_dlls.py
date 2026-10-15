# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 njsgdd10086
#
# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，
# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。

"""统一包内 MSVC 运行时 DLL 的版本（不删除任何副本）

问题根源: PyInstaller 的依赖分析从无关目录抓到了错版本。实测它把
  C:\\Program Files\\Java\\jdk-1.8\\bin\\msvcp140.dll
打进 `_internal\\msvcp140.dll`。这份旧 DLL 会优先于系统版本被加载，而它缺少
onnxruntime 需要的 api-ms-win-core-path 转发，于是:
    ImportError: DLL load failed while importing onnxruntime_pybind11_state:
    动态链接库(DLL)初始化例程失败
报错指向 .pyd，极易误判为 onnxruntime 自身问题。

修复策略（重要）:
  各包（matplotlib/pandas/sklearn）的 .libs 目录里是自己构建的私有副本，
  名字带哈希后缀。**绝不能删除它们** —— 实测删掉后 matplotlib 会报
  "DLL load failed while importing _path"。正确做法是把每个副本原地替换成
  同一份正确的 msvcp140，使全包版本一致。

用法: python tools/fix_msvc_dlls.py [dist目录]
      （不传参数时默认处理 仓库根/dist/VRCHeartRateWeb）
"""
import os
import re
import shutil
import sys

# 默认目标 = 仓库根目录下的 dist/VRCHeartRateWeb（相对定位，不写死路径）
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DIST = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    _ROOT, "dist", "VRCHeartRateWeb")
SYS32 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32")

# 只统一这几个核心运行时；msvcp140_2 等按需保持原样
CORE = ["msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"]
RE = re.compile(r"^(msvcp140|vcruntime140)(_\d+)?(-[0-9a-f]{16,})?\.dll$", re.I)
CANON = re.compile(r"^(msvcp140|vcruntime140)(_\d+)?", re.I)

def canon(name):
    m = CANON.match(name)
    if not m:
        return None
    return f"{m.group(1).lower()}{m.group(2) or ''}.dll"

def ver_tuple(path):
    """取文件版本号，失败返回全 0"""
    try:
        v = shutil.os.stat(path)
        # 用 PowerShell 取版本太慢，这里用文件名+大小做粗判不够准，
        # 直接读 PE 资源版本（用 pefile，PyInstaller 已附带）
        import pefile
        pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        best = (0, 0, 0, 0)
        for fi in getattr(pe, "FileInfo", []) or []:
            for entry in fi:
                if getattr(entry, "Key", b"") == b"StringFileInfo":
                    for st in entry.StringTable:
                        raw = st.entries.get(b"FileVersion", b"")
                        nums = re.findall(rb"\d+", raw)
                        if len(nums) >= 3:
                            t = tuple(int(x) for x in nums[:4])
                            if t > best:
                                best = t
        pe.close()
        return best
    except Exception:
        return (0, 0, 0, 0)

print(f"目标: {DIST}")
if not os.path.isdir(DIST):
    print("目录不存在")
    sys.exit(1)

# 1) 找包内所有相关副本，记录它们所在目录
targets = []          # (路径, 目录)
for root, _d, files in os.walk(DIST):
    for f in files:
        if RE.match(f):
            targets.append((os.path.join(root, f), root))

print(f"\n包内共 {len(targets)} 个 MSVC 运行时副本:")
for p, _r in targets:
    print(f"  {os.path.relpath(p, DIST):60s} 版本 {ver_tuple(p)}")

# 2) 为每个规范化名挑出"最佳来源":优先 System32，其次包内版本号最高者
best_src = {}
for name in CORE:
    sp = os.path.join(SYS32, name)
    if os.path.isfile(sp):
        best_src[name] = (sp, ver_tuple(sp), "System32")

for p, _r in targets:
    k = canon(os.path.basename(p))
    if not k or k not in CORE:
        continue
    vt = ver_tuple(p)
    cur = best_src.get(k)
    if cur is None or vt > cur[1]:
        best_src[k] = (p, vt, "包内")

print("\n选定来源:")
for k, (p, v, src) in best_src.items():
    print(f"  {k:22s} <- {src}  版本 {v}")

# 3) 原地覆盖所有副本
print("\n统一版本:")
n = 0
for p, _r in targets:
    k = canon(os.path.basename(p))
    if not k or k not in best_src:
        continue
    src = best_src[k][0]
    if os.path.abspath(src) == os.path.abspath(p):
        continue
    try:
        shutil.copy2(src, p)
        n += 1
        print(f"  覆盖 {os.path.relpath(p, DIST)}  版本 {ver_tuple(p)}")
    except Exception as e:
        print(f"  失败 {os.path.relpath(p, DIST)}: {e}")

print(f"\n共覆盖 {n} 个副本（未删除任何文件）")
