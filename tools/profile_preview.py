"""閫愭璁℃椂锛屽畾浣嶉瑙堝崱椤跨殑鐪熸鐡堕

鍒嗗埆娴嬶細
  1) 閲囬泦绾跨▼鐨勬帹鐞嗗抚鐜囷紙搴?25~30 fps锛?  2) 棰勮绾跨▼鐨勭粯鍒堕鐜囷紙搴?15 fps锛?  3) jpeg_frame() 鐨勮€楁椂锛圲I 姣忔鍙栧浘鐨勬垚鏈級
  4) 娉㈠舰鍥剧粯鍒惰€楁椂
  5) 妯℃嫙 Gradio 杞鐨勭鍒扮寤惰繜
"""
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

spec = importlib.util.spec_from_file_location(
    "cam", r"D:\deepseek_harness\osc_heartrate_cam.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

B = r"D:\deepseek_harness"
ui = m.HeartRateWebUI(os.path.join(B, "models", "FacePhys.onnx"),
                      os.path.join(B, "models", "face_detection_yunet_2023mar.onnx"))
ui.load_models()

print("=" * 70)
print("鍚姩閲囬泦...")
print("=" * 70)
ui.start_capture(0, want_vcam=False)

# 绛夋憚鍍忓ご灏辩华
for _ in range(100):
    st = ui.cam.ui_stats()
    if st.get("cam_opened") and st.get("frames_read", 0) > 5:
        break
    time.sleep(0.1)
print(f"鎽勫儚澶村氨缁? {ui.cam.cam_backend}")

print()
print("=" * 70)
print("1) 閲囬泦/鎺ㄧ悊甯х巼")
print("=" * 70)
seq0 = ui.cam._pv_raw_seq
t0 = time.time()
time.sleep(3.0)
dt = time.time() - t0
seq1 = ui.cam._pv_raw_seq
raw_fps = (seq1 - seq0) / dt
print(f"  鍙戝竷鍘熷甯? {(seq1-seq0)} 甯?/ {dt:.1f}s = {raw_fps:.1f} fps")
st = ui.cam.ui_stats()
print(f"  閲囬泦绾跨▼鑷姤 fps: {st['fps']:.1f}")

print()
print("=" * 70)
print("2) 棰勮缁樺埗绾跨▼棰戠巼锛坃render_preview 浜у嚭锛?)
print("=" * 70)
with ui.cam._preview_lock:
    p0 = ui.cam._preview_seq
time.sleep(3.0)
with ui.cam._preview_lock:
    p1 = ui.cam._preview_seq
print(f"  棰勮浜у嚭: {(p1-p0)} 甯?/ 3.0s = {(p1-p0)/3.0:.1f} fps  (璁鹃檺 {m.PREVIEW_FPS})")

print()
print("=" * 70)
print("3) jpeg_frame() 鑰楁椂锛圲I 姣忔鍙栧浘锛屽惈缂╂斁+RGB杞崲锛?)
print("=" * 70)
ts = []
for _ in range(20):
    t0 = time.perf_counter()
    img = ui.cam.jpeg_frame()
    ts.append((time.perf_counter() - t0) * 1000)
    time.sleep(0.02)
print(f"  褰㈢姸: {img.shape if img is not None else None}")
print(f"  骞冲潎 {statistics.mean(ts):.2f} ms   涓綅 {statistics.median(ts):.2f} ms   "
      f"鏈€澶?{max(ts):.2f} ms")

print()
print("=" * 70)
print("4) 娉㈠舰鍥剧粯鍒惰€楁椂")
print("=" * 70)
tr = (ui.cam.ui_stats().get("trace") or [])
if len(tr) < 10:
    tr = [float(i % 7) for i in range(300)]
ts = []
for _ in range(20):
    t0 = time.perf_counter()
    m.HeartRateWebUI._render_wave_image(tr)
    ts.append((time.perf_counter() - t0) * 1000)
print(f"  骞冲潎 {statistics.mean(ts):.2f} ms   鏈€澶?{max(ts):.2f} ms")

print()
print("=" * 70)
print("5) 妯℃嫙 Gradio 杞: 鍥惧儚 + 鏂囧瓧涓よ矾浜ゆ浛璋冪敤")
print("=" * 70)
# 鏂囧瓧璺紙0.2s 涓€娆★級
t_txt = []
for _ in range(25):
    t0 = time.perf_counter()
    try:
        ui.one_frame(False)
    except Exception:
        pass
    t_txt.append((time.perf_counter() - t0) * 1000)
    time.sleep(0.2)
print(f"  鏂囧瓧璺?one_frame(False): 骞冲潎 {statistics.mean(t_txt):.2f} ms  "
      f"鏈€澶?{max(t_txt):.2f} ms")

# 鍥惧儚璺紙0.5s 涓€娆★級
t_img = []
skip = 0
for _ in range(8):
    t0 = time.perf_counter()
    try:
        r = ui.one_frame(True)
        if r is None or (hasattr(r, "__class__") and "skip" in str(type(r)).lower()):
            skip += 1
    except Exception as e:
        print("   寮傚父:", e)
    t_img.append((time.perf_counter() - t0) * 1000)
    time.sleep(0.5)
print(f"  鍥惧儚璺?one_frame(True):  骞冲潎 {statistics.mean(t_img):.2f} ms  "
      f"鏈€澶?{max(t_img):.2f} ms")
print(f"  琚噸鍏ヤ繚鎶よ烦杩囩殑娆℃暟: {skip}   (绱璺宠繃 {ui._poll_skipped})")

print()
print("=" * 70)
print("6) 缁撹")
print("=" * 70)
print(f"  鍙戝竷鍘熷甯?     {raw_fps:5.1f} fps")
print(f"  棰勮缁樺埗浜у嚭    {(p1-p0)/3.0:5.1f} fps")
print(f"  UI 鍙栧浘鑰楁椂     {statistics.mean(ts):5.2f} ms")
print(f"  鍥惧儚璺€昏€楁椂    {statistics.mean(t_img):5.2f} ms")
ui.stop_capture()
print()
print("宸插仠姝?)
