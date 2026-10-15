# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 njsgdd10086
#
# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，
# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。

"""检查采集链路的线程与状态，定位"画面不动"这类卡死问题

排查思路: 采集、预览绘制、OSC 发送分别在独立线程里。界面冻住时，
先确认是哪个线程停了、计数器是否还在增长，再针对性修。

用法:
    python tools/diag_thread_state.py [摄像头索引]
"""
import importlib.util
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _s in ("stdout", "stderr"):
    _st = getattr(sys, _s, None)
    if _st is not None and hasattr(_st, "reconfigure"):
        try:
            _st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAM_INDEX = int(sys.argv[1]) if len(sys.argv) > 1 else 0

spec = importlib.util.spec_from_file_location(
    "cam", os.path.join(ROOT, "osc_heartrate_cam.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ui = m.HeartRateWebUI(m.find_model("facephys"), m.find_model("yunet"))
ui.load_models()

print(f"启动采集（摄像头 #{CAM_INDEX}，不启用虚拟摄像头以排除干扰）...")
t0 = time.time()
ui.start_capture(CAM_INDEX, want_vcam=False)
print(f"start_capture 返回，耗时 {time.time() - t0:.1f}s")
print(f"  后端: {ui.cam.cam_backend}")


def dump(tag):
    print(f"\n--- {tag} ---")
    for th in threading.enumerate():
        print(f"  {th.name:12s} alive={th.is_alive()} daemon={th.daemon}")
    c = ui.cam
    if c is None:
        print("  cam 已释放")
        return
    print(f"  cam.stop_flag      = {c.stop_flag.is_set()}")
    print(f"  cam.cam_opened     = {c.cam_opened}")
    print(f"  cam.frames_read    = {c.frames_read}")
    print(f"  cam.frames_failed  = {c.frames_failed}")
    print(f"  cam.nan_frames     = {c.nan_frames}")
    print(f"  _pv_raw_seq        = {c._pv_raw_seq}")
    with c._preview_lock:
        print(f"  _preview_seq       = {c._preview_seq}")
        print(f"  _ui_frame is None  = {c._ui_frame is None}")
    print(f"  cam 自报 fps       = {c.fps:.1f}")
    print(f"  cam.last_error     = {c.last_error}")


dump("刚启动")
print()
print("等待 6 秒后复查...")
time.sleep(6)
dump("6 秒后")

s1, p1 = ui.cam._pv_raw_seq, ui.cam._preview_seq
time.sleep(3)
s2, p2 = ui.cam._pv_raw_seq, ui.cam._preview_seq
print()
print("=" * 70)
print(f"3 秒内 _pv_raw_seq : {s1} -> {s2}  ({(s2 - s1) / 3:.1f} 次/秒)")
print(f"3 秒内 _preview_seq: {p1} -> {p2}  ({(p2 - p1) / 3:.1f} 次/秒)")
print()
print("预览线程内部统计")
c = ui.cam
print(f"  实际绘制次数 _pv_rendered   = {c._pv_rendered}")
print(f"  空转次数     _pv_idle       = {c._pv_idle}")
print(f"  绘制异常     _pv_render_err = {c._pv_render_err}")
if c._pv_rendered:
    print(f"  平均绘制耗时 {c._pv_render_ms / c._pv_rendered:.3f} ms")

ui.stop_capture()
print()
print("已停止")
