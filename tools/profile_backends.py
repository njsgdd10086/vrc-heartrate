"""对比 DSHOW / MSMF 的持续读取表现，重点看是否中途卡死

背景: cap.read() 实测占整条流水线约 85% 的时间，是预览卡顿的主因。
MSMF 开局常比 DSHOW 快，但可能中途停顿（内部线程读取 + 缓冲区）。
本脚本按秒统计，能看出停顿位置。
"""
import statistics
import sys
import time

for _s in ("stdout", "stderr"):
    st = getattr(sys, _s, None)
    if st is not None and hasattr(st, "reconfigure"):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import cv2

CAM = 0
DURATION = 8.0


def run_case(label, backend, threaded=None):
    """threaded: None=不动, True/False=设置 CAP_PROP_MSMF_DISABLE_THREADED_READS"""
    cap = cv2.VideoCapture(CAM, backend)
    if not cap.isOpened():
        print(f"\n{label}: 打不开")
        cap.release()
        return
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass

    flag_ok = None
    if threaded is not None:
        try:
            flag_ok = bool(cap.set(cv2.CAP_PROP_MSMF_DISABLE_THREADED_READS,
                                   1 if threaded else 0))
        except Exception:
            flag_ok = False

    for _ in range(10):
        cap.read()

    per_sec, n_sec, max_read, total = [], 0, 0.0, 0
    t_all = time.time()
    sec = t_all
    while time.time() - t_all < DURATION:
        t0 = time.perf_counter()
        r, fr = cap.read()
        dt = (time.perf_counter() - t0) * 1000
        max_read = max(max_read, dt)
        if r and fr is not None and fr.size > 0:
            total += 1
            n_sec += 1
        if time.time() - sec >= 1.0:
            per_sec.append(n_sec)
            n_sec = 0
            sec = time.time()
    elapsed = time.time() - t_all
    cap.release()

    fps = total / elapsed
    show = f"  平均 {fps:5.1f} fps   最长单次读 {max_read:7.1f} ms"
    if flag_ok is not None:
        show += f"   开关生效={flag_ok}"
    print(f"\n{label}:")
    print(show)
    print(f"  每秒帧数: {per_sec}")


print("=" * 74)
print(f"持续读取对比（每次 {DURATION:.0f} 秒）—— 关注每秒帧数是否稳定")
print("=" * 74)
run_case("DSHOW", cv2.CAP_DSHOW)
run_case("MSMF（默认，线程读取开）", cv2.CAP_MSMF)
run_case("MSMF + 关闭线程读取", cv2.CAP_MSMF, threaded=True)
run_case("MSMF + 线程读取开(=0)", cv2.CAP_MSMF, threaded=False)

print()
print("=" * 74)
print("说明")
print("=" * 74)
print("  若某后端的『每秒帧数』忽高忽低或出现 0，说明它会中途停顿，")
print("  不能只看开局速度就选它。")
