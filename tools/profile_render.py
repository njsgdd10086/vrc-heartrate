# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 njsgdd10086
#
# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，
# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。

"""剖析预览绘制各步骤的耗时

背景: 曾误判"画面卡顿是绘制太慢"，实测各步骤都在毫秒级，
真正的瓶颈是 cap.read()（占流水线约 85%）。这个脚本用来快速验证
"瓶颈到底在哪一步"，避免凭直觉优化错方向。

用法:
    python tools/profile_render.py [摄像头索引]
"""
import importlib.util
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _s in ("stdout", "stderr"):
    _st = getattr(sys, _s, None)
    if _st is not None and hasattr(_st, "reconfigure"):
        try:
            _st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAM_INDEX = int(sys.argv[1]) if len(sys.argv) > 1 else 0

spec = importlib.util.spec_from_file_location(
    "cam", os.path.join(ROOT, "osc_heartrate_cam.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ui = m.HeartRateWebUI(m.find_model("facephys"), m.find_model("yunet"))
ui.load_models()
ui.start_capture(CAM_INDEX, want_vcam=False)
for _ in range(120):
    if ui.cam.ui_stats().get("cam_opened") and ui.cam.frames_read > 5:
        break
    time.sleep(0.1)
print(f"就绪: {ui.cam.cam_backend}")
print()

c = ui.cam
print("=" * 74)
print("1) _render_preview 整体耗时（用真实帧）")
print("=" * 74)
ts = []
for _ in range(30):
    t0 = time.perf_counter()
    c._render_preview()
    ts.append((time.perf_counter() - t0) * 1000)
    time.sleep(0.02)
print(f"  平均 {statistics.mean(ts):7.2f} ms   中位 {statistics.median(ts):7.2f}   "
      f"最大 {max(ts):7.2f}")
if statistics.mean(ts) > 0:
    print(f"  -> 上限 {1000 / statistics.mean(ts):.0f} fps")

print()
print("=" * 74)
print("2) 拆解各子步骤")
print("=" * 74)
frame = c._pv_raw if c._pv_raw is not None else np.zeros((480, 640, 3), np.uint8)
small = c._pv_crop if c._pv_crop is not None else np.zeros((36, 36, 3), np.uint8)
sx, sy, ex, ey = c._pv_box or (100, 100, 300, 400)
tr = c._pv_trace or [float(i % 7) for i in range(300)]


def timeit(label, fn, n=40):
    arr = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        arr.append((time.perf_counter() - t0) * 1000)
    print(f"  {label:32s} {statistics.mean(arr):7.3f} ms  (最大 {max(arr):7.2f})")
    return statistics.mean(arr)


timeit("frame.copy()", lambda: frame.copy())
timeit("cv2.rectangle",
       lambda: cv2.rectangle(frame.copy(), (sx, sy), (ex, ey), (0, 255, 0), 2))
timeit("缩略图 resize 36->108",
       lambda: cv2.resize(small, (108, 108), interpolation=cv2.INTER_NEAREST))
timeit("cv2.putText x3",
       lambda: [cv2.putText(frame, "t", (12, 40), cv2.FONT_HERSHEY_SIMPLEX,
                            1.2, (0, 255, 0), 3) for _ in range(3)])


def wave():
    if len(tr) < 10:
        return
    tw, th = 250, 70
    x0, y0 = frame.shape[1] - tw - 12, 12
    a = np.asarray(tr, dtype=np.float32)
    a = a[np.isfinite(a)]
    if len(a) < 2:
        return
    a = a - a.min()
    if a.max() > 1e-6:
        a = a / a.max()
    pts = np.array([(x0 + int(i * (tw - 1) / (len(a) - 1)),
                     y0 + th - 4 - int(v * (th - 8))) for i, v in enumerate(a)],
                   dtype=np.int32)
    cv2.polylines(frame, [pts], False, (0, 255, 128), 1)


timeit("波形 polylines", wave)
timeit("cvtColor BGR->RGB", lambda: cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
timeit("jpeg_frame() 取图", lambda: c.jpeg_frame())

print()
print("=" * 74)
print("3) 对比: 真正的瓶颈在哪")
print("=" * 74)
t = []
for _ in range(30):
    t0 = time.perf_counter()
    c.cap.read()
    t.append((time.perf_counter() - t0) * 1000)
mark = timeit("cap.read() 读帧", lambda: None, n=1)
print(f"  {'cap.read() 实测':32s} {statistics.mean(t):7.2f} ms  "
      f"(中位 {statistics.median(t):.2f})")
print()
print("  说明: 读帧通常比上面所有绘制步骤加起来还慢一个数量级。")
print("        画面卡顿时优先查读帧与后端选择，而不是绘制。")

ui.stop_capture()
print()
print("已停止")
