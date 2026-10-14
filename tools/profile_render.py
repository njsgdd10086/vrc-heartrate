"""鐩存帴娴?_render_preview 鐨勭湡瀹炶€楁椂锛屼互鍙婂悇瀛愭楠ゅ崰姣?""
import importlib.util
import os
import statistics
import sys
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

spec = importlib.util.spec_from_file_location(
    "cam", r"D:\deepseek_harness\osc_heartrate_cam.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

B = r"D:\deepseek_harness"
ui = m.HeartRateWebUI(os.path.join(B, "models", "FacePhys.onnx"),
                      os.path.join(B, "models", "face_detection_yunet_2023mar.onnx"))
ui.load_models()
ui.start_capture(0, want_vcam=False)
for _ in range(80):
    if ui.cam.ui_stats().get("cam_opened") and ui.cam.frames_read > 5:
        break
    time.sleep(0.1)
print(f"灏辩华: {ui.cam.cam_backend}")
print()

c = ui.cam
print("=" * 74)
print("1) _render_preview 鏁翠綋鑰楁椂锛堢敤鐪熷疄甯э級")
print("=" * 74)
ts = []
for _ in range(30):
    t0 = time.perf_counter()
    c._render_preview()
    ts.append((time.perf_counter() - t0) * 1000)
    time.sleep(0.02)
print(f"  骞冲潎 {statistics.mean(ts):7.2f} ms   涓綅 {statistics.median(ts):7.2f}   "
      f"鏈€澶?{max(ts):7.2f}")
print(f"  -> 涓婇檺 {1000/statistics.mean(ts):.1f} fps")

print()
print("=" * 74)
print("2) 鎷嗚В鍚勫瓙姝ラ")
print("=" * 74)
frame = c._pv_raw if c._pv_raw is not None else np.zeros((480, 640, 3), np.uint8)
small = c._pv_crop if c._pv_crop is not None else np.zeros((36, 36, 3), np.uint8)
box = c._pv_box or (100, 100, 300, 400)
sx, sy, ex, ey = box
tr = c._pv_trace or [float(i % 7) for i in range(300)]

def timeit(label, fn, n=40):
    arr = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        arr.append((time.perf_counter() - t0) * 1000)
    print(f"  {label:34s} {statistics.mean(arr):7.3f} ms  (max {max(arr):7.2f})")
    return statistics.mean(arr)

t_copy = timeit("frame.copy()", lambda: frame.copy())
t_rect = timeit("cv2.rectangle", lambda: cv2.rectangle(frame.copy(), (sx, sy), (ex, ey), (0, 255, 0), 2))
t_small = timeit("thumbnail resize 36->108",
                 lambda: cv2.resize(small, (108, 108), interpolation=cv2.INTER_NEAREST))
t_put = timeit("cv2.putText x3", lambda: [cv2.putText(frame, "test", (12, 40),
               cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3) for _ in range(3)])
def wave():
    if len(tr) < 10:
        return
    tw, th = 250, 70
    x0, y0 = frame.shape[1] - tw - 12, 12
    a = np.asarray(tr, dtype=np.float32)
    a = a[np.isfinite(a)]
    a = a - a.min()
    if a.max() > 1e-6:
        a = a / a.max()
    pts = np.array([(x0 + int(i * (tw - 1) / (len(a) - 1)),
                     y0 + th - 4 - int(v * (th - 8))) for i, v in enumerate(a)],
                   dtype=np.int32)
    cv2.polylines(frame, [pts], False, (0, 255, 128), 1)
t_wave = timeit("娉㈠舰 polylines (300鐐?", wave)
t_cvt = timeit("cvtColor BGR->RGB", lambda: cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
t_jpg = timeit("jpeg_frame() 鍙栧浘", lambda: c.jpeg_frame())

print()
print(f"  鍙瀛愭楠ゅ悎璁?{t_copy + t_rect + t_small + t_put + t_wave + t_cvt:.2f} ms")
print(f"  鑰?_render_preview 瀹炴祴 {statistics.mean(ts):.2f} ms")

ui.stop_capture()
print()
print("宸插仠姝?)
