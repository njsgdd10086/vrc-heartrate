# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 njsgdd10086
#
# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，
# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。

"""逐段计时，定位预览链路的真正瓶颈

分别测:
  1) 采集线程的发布帧率（应接近摄像头帧率）
  2) 预览线程的绘制产出（上限为 PREVIEW_FPS）
  3) jpeg_frame() 耗时（UI 每次取图的成本）
  4) 波形图绘制耗时
  5) 模拟 Gradio 两路轮询的端到端延迟

重要: 看**中位数**，不要只看均值 —— 均值会被偶发的长尾（如 GC）
严重污染，曾因此误判 `one_frame` 慢到 91ms，实际中位数只有 1ms。

用法:
    python tools/profile_preview.py [摄像头索引]
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
CAM_INDEX = int(sys.argv[1]) if len(sys.argv) > 1 else 0

spec = importlib.util.spec_from_file_location(
    "cam", os.path.join(ROOT, "osc_heartrate_cam.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ui = m.HeartRateWebUI(m.find_model("facephys"), m.find_model("yunet"))
ui.load_models()

print("=" * 70)
print("启动采集...")
print("=" * 70)
ui.start_capture(CAM_INDEX, want_vcam=False)
for _ in range(150):
    st = ui.cam.ui_stats()
    if st.get("cam_opened") and st.get("frames_read", 0) > 5:
        break
    time.sleep(0.1)
print(f"摄像头就绪: {ui.cam.cam_backend}")
print("预热 5 秒进入稳态...")
time.sleep(5)

print()
print("=" * 70)
print("稳态帧率")
print("=" * 70)
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
print("=" * 70)
print("各路径延迟分布（看中位数）")
print("=" * 70)


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
    print(f"  {label:20s} 中位 {statistics.median(s):7.2f}   "
          f"P90 {s[int(len(s) * 0.9)]:7.2f}   最大 {s[-1]:8.2f}   "
          f"均值 {statistics.mean(s):7.2f} ms")


dist("文字路 one_frame(F)", lambda: ui.one_frame(False))
dist("图像路 one_frame(T)", lambda: ui.one_frame(True), gap=0.5)
dist("jpeg_frame()", lambda: ui.cam.jpeg_frame())
dist("ui_stats()", lambda: ui.cam.ui_stats())
dist("波形图(含缓存)",
     lambda: m.HeartRateWebUI._wave_cached(
         ui.cam, ui.cam.ui_stats().get("trace") or []))

print()
print("=" * 70)
print("预览线程内部统计")
print("=" * 70)
c = ui.cam
print(f"  绘制 {c._pv_rendered} 次   空转 {c._pv_idle}   异常 {c._pv_render_err}")
if c._pv_rendered:
    print(f"  平均绘制 {c._pv_render_ms / c._pv_rendered:.3f} ms")

ui.stop_capture()
print()
print("已停止")
