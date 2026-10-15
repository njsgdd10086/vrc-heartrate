# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 njsgdd10086
#
# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，
# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。

"""细粒度剖析采集循环各步骤耗时，定位真正的瓶颈

实测结论（供参考）: cap.read() 往往占整条流水线约 85%，
人脸检测约 6%，推理约 9%。所以帧率不够时先查读帧/后端，再查别处。

用法:
    python tools/profile_capture.py [摄像头索引]
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

print("=" * 74)
print("A) 纯摄像头读取速率（不跑任何模型）")
print("=" * 74)
for backend, name in ((cv2.CAP_DSHOW, "DSHOW"), (cv2.CAP_MSMF, "MSMF")):
    cap = cv2.VideoCapture(CAM_INDEX, backend)
    if not cap.isOpened():
        print(f"  {name}: 打不开")
        cap.release()
        continue
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    for _ in range(10):
        cap.read()
    n = 0
    t0 = time.time()
    while time.time() - t0 < 3.0:
        r, fr = cap.read()
        if r and fr is not None:
            n += 1
    dt = time.time() - t0
    print(f"  {name}: {n} 帧 / {dt:.1f}s = {n / dt:5.1f} fps")
    cap.release()

print()
print("=" * 74)
print("B) 各步骤单独耗时（用真实模型）")
print("=" * 74)
spec = importlib.util.spec_from_file_location(
    "cam", os.path.join(ROOT, "osc_heartrate_cam.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

run = m.load_facephys(m.find_model("facephys"))
det = cv2.FaceDetectorYN.create(m.opencv_safe_path(m.find_model("yunet")),
                                "", (320, 320), 0.6, 0.3, 5000)

cap = cv2.VideoCapture(CAM_INDEX, cv2.CAP_DSHOW)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
for _ in range(10):
    cap.read()

t_read, t_det, t_crop, t_infer = [], [], [], []
state = {k: v.copy() for k, v in run.init_state.items()}
last_box = None

for i in range(60):
    t0 = time.perf_counter()
    ok, frame = cap.read()
    t1 = time.perf_counter()
    if not ok or frame is None:
        continue
    h, w = frame.shape[:2]

    if i % 2 == 0 or last_box is None:
        det.setInputSize((w, h))
        _, faces = det.detect(frame)
        if faces is not None and len(faces) > 0:
            f = max(faces, key=lambda r: r[2] * r[3])
            bh2 = f[3] * 1.2
            last_box = (f[0], f[1] - bh2 * 0.2, f[2], bh2)
    t2 = time.perf_counter()

    if last_box is None:
        continue
    bx, by, bw, bh = last_box
    sx = max(0, int(bx)); sy = max(0, int(by))
    ex = min(w, int(bx + bw)); ey = min(h, int(by + bh))
    if ex - sx < 8 or ey - sy < 8:
        continue
    small = cv2.resize(frame[sy:ey, sx:ex], (36, 36),
                       interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    t3 = time.perf_counter()

    r, state = run(rgb, state, dt=np.float32(1 / 30))
    t4 = time.perf_counter()

    t_read.append((t1 - t0) * 1000)
    t_det.append((t2 - t1) * 1000)
    t_crop.append((t3 - t2) * 1000)
    t_infer.append((t4 - t3) * 1000)

cap.release()


def stat(name, arr):
    if not arr:
        print(f"  {name}: 无数据")
        return 0.0
    print(f"  {name:22s} 平均 {statistics.mean(arr):7.2f} ms   "
          f"中位 {statistics.median(arr):7.2f}   最大 {max(arr):7.2f}")
    return statistics.mean(arr)


m_read = stat("cap.read() 读帧", t_read)
m_det = stat("人脸检测(每2帧)", t_det)
m_crop = stat("裁剪+预处理", t_crop)
m_infer = stat("FacePhys 推理", t_infer)
print()
total = m_read + m_det + m_crop + m_infer
if total > 0:
    print(f"  合计 {total:.2f} ms/帧  ->  理论上限 {1000 / total:.1f} fps")
    print(f"  读帧占 {m_read / total * 100:.0f}%，检测占 {m_det / total * 100:.0f}%，"
          f"推理占 {m_infer / total * 100:.0f}%")

print()
print("=" * 74)
print("C) estimate_bpm 耗时（每秒调用一次）")
print("=" * 74)
vals = [float(np.sin(i / 5)) for i in range(900)]
ts = [i / 30.0 for i in range(900)]
t = []
for _ in range(10):
    t0 = time.perf_counter()
    m.estimate_bpm(vals, ts)
    t.append((time.perf_counter() - t0) * 1000)
print(f"  平均 {statistics.mean(t):.1f} ms   最大 {max(t):.1f} ms")
