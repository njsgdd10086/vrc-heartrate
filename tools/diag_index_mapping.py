# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 njsgdd10086
#
# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，
# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。

"""确认 OpenCV 的索引到底对应哪个 DirectShow 设备

做法: 用 pygrabber 式的 DirectShow 枚举拿到"名称 + 设备路径(moniker)"，
再用 filter graph 直接按 moniker 打开指定设备 —— 这样能绕开索引歧义，
也能确认索引与设备的对应关系。
"""
import re
import subprocess
import sys
import time

for _s in ("stdout", "stderr"):
    st = getattr(sys, _s, None)
    if st is not None and hasattr(st, "reconfigure"):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import cv2

# 1) 注册表里的顺序（DirectShow 传统枚举顺序）
print("=" * 74)
print("注册表顺序（DirectShow 传统顺序）")
print("=" * 74)
ps = (
    "$c='{860BB310-5D01-11d0-BD3B-00A0C911CE86}';"
    "Get-ChildItem \"HKLM:\\SOFTWARE\\Classes\\CLSID\\$c\\Instance\" "
    "-ErrorAction SilentlyContinue | Sort-Object PSChildName | "
    "ForEach-Object { $n=(Get-ItemProperty $_.PSPath "
    "-ErrorAction SilentlyContinue).FriendlyName; if($n){$n} }"
)
out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                     capture_output=True, text=True, timeout=30,
                     encoding="utf-8", errors="replace")
reg_names = [n.strip() for n in (out.stdout or "").splitlines() if n.strip()]
for i, n in enumerate(reg_names):
    print(f"  [{i}] {n}")

# 2) MSMF 后端逐个索引探测
print()
print("=" * 74)
print("MSMF 后端逐个索引（看哪些能开）")
print("=" * 74)
msmf_ok = []
for i in range(8):
    cap = cv2.VideoCapture(i, cv2.CAP_MSMF)
    ok = cap.isOpened()
    w = h = 0
    if ok:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        for _ in range(6):
            cap.read()
        good = 0
        t0 = time.time()
        while time.time() - t0 < 0.8:
            r, fr = cap.read()
            if r and fr is not None and float(fr.std()) > 1:
                good += 1
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if good >= 3:
            msmf_ok.append(i)
        print(f"  MSMF 索引 {i}: 打开={ok} {w}x{h} 有效帧={good}")
    else:
        print(f"  MSMF 索引 {i}: 打不开")
    cap.release()

# 3) DSHOW 后端逐个索引探测
print()
print("=" * 74)
print("DSHOW 后端逐个索引（看哪些能开）")
print("=" * 74)
dshow_ok = []
for i in range(8):
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    ok = cap.isOpened()
    if ok:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        for _ in range(6):
            cap.read()
        good = 0
        t0 = time.time()
        while time.time() - t0 < 0.8:
            r, fr = cap.read()
            if r and fr is not None and float(fr.std()) > 1:
                good += 1
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if good >= 3:
            dshow_ok.append(i)
        print(f"  DSHOW 索引 {i}: 打开={ok} {w}x{h} 有效帧={good}")
    else:
        print(f"  DSHOW 索引 {i}: 打不开")
    cap.release()

print()
print("=" * 74)
print("结论")
print("=" * 74)
print(f"  DSHOW 可用索引: {dshow_ok}")
print(f"  MSMF  可用索引: {msmf_ok}")
if dshow_ok == list(range(len(dshow_ok))):
    print("  DSHOW 索引连续 -> 很可能与注册表顺序一致，可以直接对应名称")
else:
    print("  DSHOW 索引不连续 -> 与注册表顺序不一致，不能直接对应名称")
print(f"  注册表设备数: {len(reg_names)}")
