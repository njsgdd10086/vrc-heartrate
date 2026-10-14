"""绔埌绔‘璁? 鍚庣閫夋嫨銆佸抚鐜囥€侀瑙堜骇鍑恒€佸悇璺緞寤惰繜鍒嗗竷"""
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

print("=" * 74)
print("鍚姩锛堟ā鎷熺湡瀹炰娇鐢? 鍚敤铏氭嫙鎽勫儚澶?+ 鍚勫弬鏁伴粯璁わ級")
print("=" * 74)
t0 = time.time()
ui.start_capture(0, want_vcam=True)
print(f"  start_capture 杩斿洖鑰楁椂 {time.time()-t0:.1f}s")
for _ in range(150):
    st = ui.cam.ui_stats()
    if st.get("cam_opened") and st.get("frames_read", 0) > 60:
        break
    time.sleep(0.1)
print(f"  鍚庣: {ui.cam.cam_backend}")
print(f"  铏氭嫙鎽勫儚澶? {ui.cam.vcam_device}")
print()
print("  棰勭儹 5 绉掞紙璁╅噰闆嗕笌棰勮杩涘叆绋虫€侊級...")
time.sleep(5)

print()
print("=" * 74)
print("绋虫€佸抚鐜?)
print("=" * 74)
s1, p1 = ui.cam._pv_raw_seq, ui.cam._preview_seq
t0 = time.time()
time.sleep(5.0)
dt = time.time() - t0
s2, p2 = ui.cam._pv_raw_seq, ui.cam._preview_seq
st = ui.cam.ui_stats()
print(f"  閲囬泦鍙戝竷    {(s2-s1)/dt:6.1f} fps   (鑷姤 {st['fps']:.1f} fps)")
print(f"  棰勮浜у嚭    {(p2-p1)/dt:6.1f} fps   (璁鹃檺 {m.PREVIEW_FPS})")
print(f"  宸茶 {st['frames_read']} 甯?/ 澶辫触 {st['frames_failed']}")

print()
print("=" * 74)
print("鍚勮矾寰勫欢杩熷垎甯冿紙鍏虫敞涓綅鏁帮紝鍧囧€间細琚缇ゅ€兼薄鏌擄級")
print("=" * 74)

def dist(label, fn, n=30, gap=0.25):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
        time.sleep(gap)
    s = sorted(ts)
    print(f"  {label:22s} 涓綅 {statistics.median(s):7.2f} ms   "
          f"P90 {s[int(len(s)*0.9)]:7.2f}   鏈€澶?{s[-1]:8.2f}")

dist("鏂囧瓧璺?one_frame(F)", lambda: ui.one_frame(False))
dist("鍥惧儚璺?one_frame(T)", lambda: ui.one_frame(True), gap=0.5)
dist("jpeg_frame()", lambda: ui.cam.jpeg_frame())
dist("ui_stats()", lambda: ui.cam.ui_stats())
dist("娉㈠舰鍥?鍚紦瀛?",
     lambda: m.HeartRateWebUI._wave_cached(ui.cam, ui.cam.ui_stats().get("trace") or []))

print()
print("=" * 74)
print("棰勮绾跨▼鍐呴儴缁熻")
print("=" * 74)
c = ui.cam
print(f"  缁樺埗娆℃暟 {c._pv_rendered}   绌鸿浆 {c._pv_idle}   寮傚父 {c._pv_render_err}")
if c._pv_rendered:
    print(f"  骞冲潎缁樺埗 {c._pv_render_ms/c._pv_rendered:.3f} ms")
print(f"  铏氭嫙鎽勫儚澶翠涪甯?{c._vcam_dropped}")

ui.stop_capture()
print()
print("宸插仠姝?)
