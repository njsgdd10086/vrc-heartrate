# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 njsgdd10086
#
# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，
# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。

"""端到端确认: 后端选择、帧率、预览产出、各路径延迟分布

发布前跑一遍，确认没有性能回归。参考值（本机实测）:
    采集发布 ~30 fps    预览产出 ~13 fps
    文字路中位 ~1.5 ms  图像路中位 ~1.0 ms

用法:
    python tools/verify_pipeline.py [摄像头索引] [--vcam]
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
CAM_INDEX = int(args[0]) if args else 0
USE_VCAM = "--vcam" in sys.argv

spec = importlib.util.spec_from_file_location(
    "cam", os.path.join(ROOT, "osc_heartrate_cam.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ui = m.HeartRateWebUI(m.find_model("facephys"), m.find_model("yunet"))
ui.load_models()

print("=" * 74)
print(f"启动（摄像头 #{CAM_INDEX}，虚拟摄像头={'开' if USE_VCAM else '关'}）")
print("=" * 74)
t0 = time.time()
ui.start_capture(CAM_INDEX, want_vcam=USE_VCAM)
print(f"  start_capture 返回耗时 {time.time() - t0:.1f}s")
for _ in range(150):
    st = ui.cam.ui_stats()
    if st.get("cam_opened") and st.get("frames_read", 0) > 60:
        break
    time.sleep(0.1)
print(f"  后端: {ui.cam.cam_backend}")
if USE_VCAM:
    print(f"  虚拟摄像头: {ui.cam.vcam_device or '（未启用）'}")
print()
print("  预热 5 秒...")
time.sleep(5)

print()
print("=" * 74)
print("稳态帧率")
print("=" * 74)
s1, p1 = ui.cam._pv_raw_seq, ui.cam._preview_seq
t0 = time.time()
time.sleep(5.0)
dt = time.time() - t0
s2, p2 = ui.cam._pv_raw_seq, ui.cam._preview_seq
st = ui.cam.ui_stats()
print(f"  采集发布    {(s2 - s1) / dt:6.1f} fps   (自报 {st['fps']:.1f} fps)")
print(f"  预览产出    {(p2 - p1) / dt:6.1f} fps   (设限 {m.PREVIEW_FPS})")
print(f"  已读 {st['frames_read']} 帧 / 失败 {st['frames_failed']}")

print()
print("=" * 74)
print("各路径延迟分布（关注中位数，均值会被离群值污染）")
print("=" * 74)


def dist(label, fn, n=25, gap=0.25):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            fn()
        except Exception:
            pass
        ts.append((time.perf_counter() - t0) * 1000)
        time.sleep(gap)
    s = sorted(ts)
    print(f"  {label:20s} 中位 {statistics.median(s):7.2f} ms   "
          f"P90 {s[int(len(s) * 0.9)]:7.2f}   最大 {s[-1]:8.2f}")


dist("文字路 one_frame(F)", lambda: ui.one_frame(False))
dist("图像路 one_frame(T)", lambda: ui.one_frame(True), gap=0.5)
dist("jpeg_frame()", lambda: ui.cam.jpeg_frame())
dist("ui_stats()", lambda: ui.cam.ui_stats())
dist("波形图(含缓存)",
     lambda: m.HeartRateWebUI._wave_cached(
         ui.cam, ui.cam.ui_stats().get("trace") or []))

print()
print("=" * 74)
print("预览线程内部统计")
print("=" * 74)
c = ui.cam
print(f"  绘制 {c._pv_rendered} 次   空转 {c._pv_idle}   异常 {c._pv_render_err}")
if c._pv_rendered:
    print(f"  平均绘制 {c._pv_render_ms / c._pv_rendered:.3f} ms")
print(f"  虚拟摄像头丢帧 {c._vcam_dropped}")

ui.stop_capture()
print()
print("已停止")
