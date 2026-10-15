"""诊断摄像头枚举问题：逐个索引、逐个后端、逐个分辨率地测，定位卡在哪

枚举只返回 1 个摄像头，但设备管理器里有 3 个物理摄像头 + 多个虚拟摄像头。
本脚本按最小步骤测，并把每步耗时打出来。
"""
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

import cv2

spec = importlib.util.spec_from_file_location(
    "cam", r"D:\deepseek_harness\osc_heartrate_cam.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print("=" * 76)
print("系统里注册的摄像头")
print("=" * 76)
names = m.get_camera_names()
for i, n in enumerate(names):
    print(f"  [{i}] {n}")

print()
print("=" * 76)
print("逐个索引 × 后端 测试（每项最多等 6 秒）")
print("=" * 76)
print(f"  {'索引':<5}{'后端':<8}{'结果':<34}{'耗时'}")
print("  " + "-" * 70)

results = {}


def try_one(idx, backend_name, backend):
    """返回 (成功?, 描述, 耗时秒)"""
    t0 = time.time()
    cap = cv2.VideoCapture(idx, backend)
    opened = cap.isOpened()
    if not opened:
        try:
            cap.release()
        except Exception:
            pass
        return False, "打不开", time.time() - t0
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    good = 0
    total = 0
    for _ in range(8):
        ok, fr = cap.read()
        total += 1
        if ok and fr is not None and fr.size > 0 and float(fr.std()) > 1:
            good += 1
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    try:
        cap.release()
    except Exception:
        pass
    dt = time.time() - t0
    if good == 0:
        return False, f"打开但 0/{total} 有效帧 ({w}x{h})", dt
    return True, f"{w}x{h} {good}/{total} 有效帧", dt


lock = threading.Lock()


def run(idx, bname, backend):
    key = (idx, bname)
    try:
        ok, desc, dt = try_one(idx, bname, backend)
    except Exception as e:
        ok, desc, dt = False, f"异常 {type(e).__name__}", 0.0
    with lock:
        results[key] = (ok, desc, dt)
        flag = "OK " if ok else "   "
        print(f"  {flag}#{idx:<3}{bname:<8}{desc:<34}{dt:5.1f}s", flush=True)


for idx in range(6):
    for bname, backend in (("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW)):
        th = threading.Thread(target=run, args=(idx, bname, backend), daemon=True)
        th.start()
        th.join(6.0)
        if th.is_alive():
            with lock:
                results[(idx, bname)] = (False, "超时(>6s，可能阻塞)", 6.0)
            print(f"     #{idx:<3}{bname:<8}{'超时 >6s（阻塞）':<34}{'6.0s':>5}", flush=True)

print()
print("=" * 76)
print("汇总")
print("=" * 76)
ok_list = [k for k, v in results.items() if v[0]]
print(f"  可用 (索引, 后端): {ok_list if ok_list else '无'}")
print()
print("  按索引:")
for idx in range(6):
    ms = results.get((idx, "MSMF"))
    ds = results.get((idx, "DSHOW"))
    nm = names[idx] if idx < len(names) else "?"
    print(f"    #{idx}  {nm}")
    print(f"        MSMF : {'OK  ' if ms and ms[0] else '失败'} {ms[1] if ms else '-'}")
    print(f"        DSHOW: {'OK  ' if ds and ds[0] else '失败'} {ds[1] if ds else '-'}")
