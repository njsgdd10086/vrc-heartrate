"""测试 pyvirtualcam 能否在不启动 OBS 的情况下创建虚拟摄像头"""
import sys
import time

for _s in ("stdout", "stderr"):
    st = getattr(sys, _s, None)
    if st is not None and hasattr(st, "reconfigure"):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import numpy as np
import pyvirtualcam

print("=" * 72)
print("1) 列出可用后端")
print("=" * 72)
try:
    devs = pyvirtualcam.Camera.devices()
    print(f"  已注册的虚拟摄像头: {devs if devs else '(无)'}")
except Exception as e:
    print(f"  devices() 不可用: {type(e).__name__}: {e}")

print()
print("=" * 72)
print("2) 尝试创建虚拟摄像头（关键：OBS 未运行）")
print("=" * 72)
W, H, FPS = 640, 480, 30
cam = None
try:
    cam = pyvirtualcam.Camera(width=W, height=H, fps=FPS,
                              fmt=pyvirtualcam.PixelFormat.BGR, print_fps=False)
    print(f"  ✓ 创建成功！")
    print(f"    设备名 : {cam.device}")
    print(f"    后端   : {cam.backend}")
    print(f"    分辨率 : {cam.width}x{cam.height} @ {cam.fps}fps")
except Exception as e:
    import traceback
    print(f"  ✗ 创建失败: {type(e).__name__}: {e}")
    traceback.print_exc()

print()
print("=" * 72)
print("3) 写入测试画面并检查能否被 OpenCV 作为摄像头读取")
print("=" * 72)
if cam is not None:
    import cv2
    import threading

    stop = threading.Event()
    n = [0]

    def feed():
        t = 0
        while not stop.is_set():
            # 画一个滚动的色块，便于确认画面在动
            frame = np.zeros((H, W, 3), dtype=np.uint8)
            x = (t * 8) % W
            cv2.rectangle(frame, (x, 100), (x + 120, 380), (60, 200, 255), -1)
            cv2.putText(frame, f"TEST {t}", (20, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
            cam.send(frame)
            cam.sleep_until_next_frame()
            n[0] += 1
            t += 1

    th = threading.Thread(target=feed, daemon=True)
    th.start()
    time.sleep(1.5)
    print(f"  已写入 {n[0]} 帧，现在尝试用 OpenCV 读取...")

    # 遍历索引找虚拟摄像头
    found = None
    for i in range(10):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            for _ in range(8):
                r, fr = cap.read()
            ok = 0
            for _ in range(10):
                r, fr = cap.read()
                if r and fr is not None and float(fr.std()) > 3:
                    ok += 1
            if ok >= 5:
                # 看是不是我们写入的内容（有白色文字）
                gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
                bright = float((gray > 220).mean())
                print(f"    索引 {i}: 有画面  std={fr.std():.1f}  高亮像素占比={bright*100:.2f}%")
                if bright > 0.005:
                    print(f"    >>> 索引 {i} 很可能就是我们的虚拟摄像头！")
                    found = i
            cap.release()
            if found is not None:
                break
        else:
            cap.release()

    if found is None:
        print("    未在索引 0~9 找到含测试画面的设备")
        print("    （可能虚拟摄像头在更高索引，或需要重启程序才可见）")

    stop.set()
    th.join(timeout=2)
    cam.close()
    print()
    print("  虚拟摄像头已关闭")
else:
    print("  跳过（未创建成功）")
