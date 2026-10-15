# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 njsgdd10086
#
# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，
# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。

"""实测摄像头能否被多个客户端同时占用

同时还会枚举系统里的虚拟摄像头（OBS 等），虚拟摄像头是解决
"面补和心率抢摄像头"的关键 —— 如果 OBS 虚拟摄像头可用，
就可以让心率程序读虚拟摄像头、面补读真实摄像头。
"""
import os
import re
import subprocess
import sys
import threading
import time

for _s in ("stdout", "stderr"):
    st = getattr(sys, _s, None)
    if st is not None and hasattr(st, "reconfigure"):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import cv2

print("=" * 72)
print("1) 系统已注册的摄像头设备（含虚拟摄像头）")
print("=" * 72)
# 通过 PnP 查摄像头设备
ps = (
    "Get-PnpDevice -Class Camera,Image -ErrorAction SilentlyContinue | "
    "Select-Object Status,FriendlyName,InstanceId | ConvertTo-Json -Compress"
)
try:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, timeout=40,
                         encoding="utf-8", errors="replace")
    import json
    devs = json.loads(out.stdout or "[]")
    if isinstance(devs, dict):
        devs = [devs]
    for d in devs:
        name = d.get("FriendlyName", "?")
        iid = d.get("InstanceId", "")
        kind = ""
        if iid.startswith("SWD\\") or "ROOT\\" in iid.upper():
            kind = "  <== 软件/虚拟设备"
        if re.search(r"obs|virtual|virtualcam|nvidia|manycam|droidcam|snap", name, re.I):
            kind = "  <== 疑似虚拟摄像头"
        print(f"  [{d.get('Status')}] {name}{kind}")
        print(f"        {iid}")
except Exception as e:
    print(f"  查询失败: {e}")

print()
print("=" * 72)
print("2) OpenCV 能打开的索引，以及它们的分辨率/名称")
print("=" * 72)
indices = []
for i in range(8):
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    if cap.isOpened():
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        ok = False
        for _ in range(5):
            r, fr = cap.read()
            if r and fr is not None and float(fr.std()) > 1:
                ok = True
                break
        print(f"  索引 {i}: 可打开 {w}x{h}  有效帧={ok}")
        if ok:
            indices.append(i)
    cap.release()
print(f"  可用于测试的索引: {indices}")

print()
print("=" * 72)
print("3) 多客户端并发测试（关键）")
print("=" * 72)

def open_once(idx, backend, tag, results, hold=3.0):
    """打开摄像头并持续读帧，把结果写入 results"""
    cap = cv2.VideoCapture(idx, backend)
    if not cap.isOpened():
        results[tag] = ("打不开", 0)
        cap.release()
        return
    good = 0
    t0 = time.time()
    while time.time() - t0 < hold:
        r, fr = cap.read()
        if r and fr is not None and float(fr.std()) > 1:
            good += 1
    results[tag] = ("打开成功", good)
    cap.release()

for idx in indices[:2]:
    print(f"\n  --- 摄像头 {idx} ---")
    # 先单客户端基线
    base = {}
    open_once(idx, cv2.CAP_DSHOW, "base", base, hold=2.0)
    print(f"    单客户端: {base.get('base')}")
    # 再两个客户端同时
    res = {}
    threads = [
        threading.Thread(target=open_once, args=(idx, cv2.CAP_DSHOW, "A", res, 3.0)),
        threading.Thread(target=open_once, args=(idx, cv2.CAP_DSHOW, "B", res, 3.0)),
    ]
    for t in threads:
        t.start()
        time.sleep(0.15)        # 稍微错开，模拟真实场景
    for t in threads:
        t.join()
    a = res.get("A", ("?", 0))
    b = res.get("B", ("?", 0))
    print(f"    客户端A: {a}    客户端B: {b}")
    if a[1] > 0 and b[1] > 0:
        print("    >>> 支持多客户端同时访问！可以直接共用这颗摄像头")
    else:
        print("    >>> 不支持并发 —— 需要虚拟摄像头方案")

print()
print("=" * 72)
print("4) OBS 是否安装（OBS 虚拟摄像头是最常用的解决方案）")
print("=" * 72)
obs_paths = [
    r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
    r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe",
]
found = [p for p in obs_paths if os.path.isfile(p)]
if found:
    print(f"  已安装: {found[0]}")
    dll = os.path.join(os.path.dirname(found[0]), "obs-virtualcam.dll")
    print(f"  虚拟摄像头模块: {'存在' if os.path.isfile(dll) else '未找到'}")
else:
    print("  未在默认路径找到 OBS")
