"""妫€鏌ユ崲鐢?MSMF 鍚庯紝绋嬪簭鍐呴儴鍝釜绾跨▼鍗′綇浜?""
import importlib.util
import os
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

spec = importlib.util.spec_from_file_location(
    "cam", r"D:\deepseek_harness\osc_heartrate_cam.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

B = r"D:\deepseek_harness"
ui = m.HeartRateWebUI(os.path.join(B, "models", "FacePhys.onnx"),
                      os.path.join(B, "models", "face_detection_yunet_2023mar.onnx"))
ui.load_models()
print("鍚姩閲囬泦锛堜笉鍚敤铏氭嫙鎽勫儚澶达紝鎺掗櫎 vcam 骞叉壈锛?..")
t0 = time.time()
ui.start_capture(0, want_vcam=False)
print(f"start_capture 杩斿洖锛岃€楁椂 {time.time()-t0:.1f}s")
print(f"  鍚庣: {ui.cam.cam_backend}")

print()
print("=" * 74)
print("绾跨▼鐘舵€?)
print("=" * 74)

def dump(tag):
    print(f"\n--- {tag} ---")
    for th in threading.enumerate():
        print(f"  {th.name:12s} alive={th.is_alive()} daemon={th.daemon}")
    c = ui.cam
    if c is not None:
        print(f"  cam.stop_flag      = {c.stop_flag.is_set()}")
        print(f"  cam.cam_opened     = {c.cam_opened}")
        print(f"  cam.frames_read    = {c.frames_read}")
        print(f"  cam.frames_failed  = {c.frames_failed}")
        print(f"  cam.nan_frames     = {c.nan_frames}")
        print(f"  _pv_raw_seq        = {c._pv_raw_seq}")
        with c._preview_lock:
            print(f"  _preview_seq       = {c._preview_seq}")
            print(f"  _ui_frame is None  = {c._ui_frame is None}")
        print(f"  _index_changed     = {c._index_changed.is_set()}")
        print(f"  cam 鑷姤 fps       = {c.fps:.1f}")
        print(f"  cam.last_error     = {c.last_error}")
        print(f"  cam.switch_error   = {c.switch_error}")

dump("鍒氬惎鍔?)

print()
print("绛夊緟 6 绉掑悗澶嶆煡...")
time.sleep(6)
dump("6 绉掑悗")

# 鐩存帴鐪嬮瑙堢嚎绋嬫槸鍚﹀湪鎺ㄨ繘
s1 = ui.cam._pv_raw_seq
p1 = ui.cam._preview_seq
time.sleep(3)
s2 = ui.cam._pv_raw_seq
p2 = ui.cam._preview_seq
print()
print("=" * 74)
print(f"3 绉掑唴 _pv_raw_seq: {s1} -> {s2}  ({(s2-s1)/3:.1f} 娆?绉?")
print(f"3 绉掑唴 _preview_seq: {p1} -> {p2}  ({(p2-p1)/3:.1f} 娆?绉?")
print()
print("=" * 74)
print("棰勮绾跨▼鍐呴儴缁熻")
print("=" * 74)
cc = ui.cam
print(f"  瀹為檯缁樺埗娆℃暟   _pv_rendered   = {cc._pv_rendered}")
print(f"  绌鸿浆娆℃暟       _pv_idle       = {cc._pv_idle}")
print(f"  缁樺埗鑰楁椂绱   _pv_render_ms  = {cc._pv_render_ms:.1f} ms")
print(f"  缁樺埗寮傚父娆℃暟   _pv_render_err = {cc._pv_render_err}")
if cc._pv_rendered:
    print(f"  骞冲潎姣忔缁樺埗   {cc._pv_render_ms/cc._pv_rendered:.3f} ms")
ui.stop_capture()
print()
print("宸插仠姝?)
