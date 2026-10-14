"""缁嗙矑搴﹀墫鏋愰噰闆嗗惊鐜悇姝ラ鑰楁椂锛屽畾浣嶇湡姝ｇ殑鐡堕"""
import importlib.util
import os
import statistics
import sys
import threading
import time

sys.path.insert(0, r"D:\deepseek_harness")
for _s in ("stdout", "stderr"):
    st = getattr(sys, _s, None)
    if st is not None and hasattr(st, "reconfigure"):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import cv2
import numpy as np

B = r"D:\deepseek_harness"

print("=" * 74)
print("A) 绾憚鍍忓ご璇诲彇閫熺巼锛堜笉璺戜换浣曟ā鍨嬶級")
print("=" * 74)
for backend, name in ((cv2.CAP_DSHOW, "DSHOW"), (cv2.CAP_MSMF, "MSMF")):
    cap = cv2.VideoCapture(0, backend)
    if not cap.isOpened():
        print(f"  {name}: 鎵撲笉寮€")
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
    print(f"  {name}: {n} 甯?/ {dt:.1f}s = {n/dt:5.1f} fps")
    cap.release()

print()
print("=" * 74)
print("B) 鍚勬楠ゅ崟鐙€楁椂锛堢敤鐪熷疄妯″瀷锛?)
print("=" * 74)
spec = importlib.util.spec_from_file_location(
    "cam", r"D:\deepseek_harness\osc_heartrate_cam.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

run = m.load_facephys(os.path.join(B, "models", "FacePhys.onnx"))
det = cv2.FaceDetectorYN.create(
    os.path.join(B, "models", "face_detection_yunet_2023mar.onnx"),
    "", (320, 320), 0.6, 0.3, 5000)

cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
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

    # 浜鸿劯妫€娴嬶紙妯℃嫙姣?2 甯т竴娆★級
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
    small = cv2.resize(frame[sy:ey, sx:ex], (36, 36), interpolation=cv2.INTER_AREA)
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
        print(f"  {name}: 鏃犳暟鎹?)
        return 0.0
    print(f"  {name:22s} 骞冲潎 {statistics.mean(arr):7.2f} ms   "
          f"涓綅 {statistics.median(arr):7.2f}   鏈€澶?{max(arr):7.2f}")
    return statistics.mean(arr)

m_read = stat("cap.read() 璇诲抚", t_read)
m_det = stat("浜鸿劯妫€娴?姣?甯?", t_det)
m_crop = stat("瑁佸壀+棰勫鐞?, t_crop)
m_infer = stat("FacePhys 鎺ㄧ悊", t_infer)
print()
total = m_read + m_det + m_crop + m_infer
print(f"  鍚堣 {total:.2f} ms/甯? ->  鐞嗚涓婇檺 {1000/total:.1f} fps")
print(f"  鍏朵腑璇诲抚鍗?{m_read/total*100:.0f}%锛屾娴嬪崰 {m_det/total*100:.0f}%锛?
      f"鎺ㄧ悊鍗?{m_infer/total*100:.0f}%")

print()
print("=" * 74)
print("C) 鍗曠嫭娴?estimate_bpm 鑰楁椂锛堟瘡绉掕皟鐢ㄤ竴娆★級")
print("=" * 74)
vals = [float(np.sin(i / 5)) for i in range(900)]
ts = [i / 30.0 for i in range(900)]
t = []
for _ in range(10):
    t0 = time.perf_counter()
    m.estimate_bpm(vals, ts)
    t.append((time.perf_counter() - t0) * 1000)
print(f"  骞冲潎 {statistics.mean(t):.1f} ms   鏈€澶?{max(t):.1f} ms")
print(f"  锛堟瘡绉掍竴娆★紝鎽婂埌 15fps 涓婄害 {statistics.mean(t)/15:.1f} ms/甯э級")
