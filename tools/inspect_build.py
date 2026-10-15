# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 njsgdd10086
#
# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，
# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。

"""从打包好的 exe 里提取 osc_heartrate_cam 的源码，确认打包的是哪个版本

PyInstaller onedir 把纯 Python 模块编译进 PYZ-00.pyz。这里直接解析 PYZ
（本质是 zlib 压缩的条目集合），把目标模块的字节码找出来，
再从中提取可读字符串做版本判断。
"""
import marshal
import os
import re
import sys
import zlib

for _s in ("stdout", "stderr"):
    st = getattr(sys, _s, None)
    if st is not None and hasattr(st, "reconfigure"):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

BASE = sys.argv[1] if len(sys.argv) > 1 else \
    r"C:\Users\76540\Desktop\软件收纳\VR\摄像头心率VRChat\_internal"

pyz = None
for root, _dirs, files in os.walk(BASE):
    for f in files:
        if f.lower().startswith("pyz") and f.lower().endswith(".pyz"):
            pyz = os.path.join(root, f)
            break
    if pyz:
        break

print(f"PYZ 文件: {pyz}")
if not pyz:
    print("找不到 PYZ，可能不是 onedir 布局")
    sys.exit(1)

data = open(pyz, "rb").read()
print(f"PYZ 大小: {len(data) / 1024 / 1024:.1f} MB")

def parse_pyz(buf):
    """解析 PYZ: magic(4) + pyc头 + marshalled TOC"""
    # PyInstaller PYZ: b'PYZ\0' + magic(4) + TOC 偏移
    assert buf[:4] == b"PYZ\0", f"magic 不符: {buf[:8]!r}"
    toc_off = int.from_bytes(buf[8:12], "big")
    toc = marshal.loads(buf[toc_off:])
    return toc

try:
    toc = parse_pyz(data)
    print(f"PYZ 条目数: {len(toc)}")
except Exception as e:
    print(f"解析 TOC 失败: {e}")
    sys.exit(1)

# TOC 是 {name: (typecode, offset, length)}，实际数据在 offset 处 zlib 压缩
target = None
for name in toc:
    if "osc_heartrate_cam" in str(name):
        target = name
        break
print(f"目标模块: {target}")
if not target:
    print("PYZ 里没有 osc_heartrate_cam（可能在 base_library 或作为脚本打包）")
    sys.exit(1)

entry = toc[target]
print(f"TOC 条目: {entry}")
typecode, offset, length = entry
blob = data[offset:offset + length]
try:
    raw = zlib.decompress(blob)
except Exception:
    raw = blob
print(f"解压后 {len(raw)} 字节")

# 从字节码里提取字符串常量，用来判断版本
strings = set()
try:
    code = marshal.loads(raw[16:])   # 跳过 pyc 头
except Exception:
    try:
        code = marshal.loads(raw)
    except Exception as e:
        print(f"反序列化失败: {e}")
        sys.exit(1)

def walk(co):
    for c in co.co_consts:
        if isinstance(c, str):
            strings.add(c)
        elif hasattr(c, "co_consts"):
            walk(c)

walk(code)
print(f"提取到 {len(strings)} 个字符串常量")

checks = [
    ("新版: 该后端阻塞", "该后端阻塞"),
    ("新版: 超时(>", "超时(>"),
    ("旧版: 已跳过", "探测超时"),
    ("超时常量 PROBE", "PROBE_TIMEOUT_SEC"),
    ("per-backend 超时说明", "_run_with_timeout"),
    ("下拉框: 用户已选", "_user_picked"),
    ("build 等枚举", "wait_for_enumeration"),
]
print()
print("=== 版本特征 ===")
for label, needle in checks:
    hit = any(needle in s for s in strings)
    print(f"  {'✓' if hit else '✗'} {label}  ({needle!r})")
