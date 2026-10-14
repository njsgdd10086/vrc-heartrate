"""测试 IR Camera（笔记本内置）能否用于心率测量

如果可用，就可以: 面补用 4M HD Camera，心率用 IR Camera，互不抢占。
"""
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
import numpy as np

YUNET = r"D:\deepseek_harness\models\face_detection_yunet_2023mar.onnx"

# 先看每个索引的实际画面特征，判断哪个是 IR（红外画面通常是灰阶/偏暗）
print("=" * 72)
print("各摄像头画面特征（IR 摄像头通常是灰阶或明显偏暗）")
print("=" * 72)
det = cv2.FaceDetectorYN.create(YUNET, "", (320, 320), 0.5, 0.3, 5000)
save = {}
for idx in (0, 1, 6, 7):
    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print(f"  索引 {idx}: 打不开")
        continue
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    for _ in range(10):
        cap.read()
    frame = None
    for _ in range(10):
        r, fr = cap.read()
        if r and fr is not None:
            frame = fr
            break
    if frame is None:
        print(f"  索引 {idx}: 读不到帧")
        cap.release()
        continue
    b, g, r_ = (float(frame[:, :, i].mean()) for i in range(3))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # 通道差异越小越可能是红外（红外画面近似灰阶）
    chroma = float(np.mean([abs(b - g), abs(g - r_), abs(b - r_)]))
    # 人脸检测
    det.setInputSize((frame.shape[1], frame.shape[0]))
    _, faces = det.detect(frame)
    nf = 0 if faces is None else len(faces)
    print(f"  索引 {idx}: 亮度 B={b:5.1f} G={g:5.1f} R={r_:5.1f}  "
          f"通道差={chroma:5.2f}  灰度std={gray.std():5.1f}  检出人脸={nf}")
    save[idx] = frame.copy()
    cap.release()

for idx, fr in save.items():
    cv2.imwrite(rf"D:\deepseek_harness\_cam{idx}.png", fr)
print()
print("各摄像头第一帧已保存（_camN.png），便于人工确认哪个是 IR")

print()
print("=" * 72)
print("提示")
print("=" * 72)
print("  IR 摄像头画面近似灰阶（通道差很小）。rPPG 依赖皮肤颜色变化，")
print("  纯红外画面通常**无法**测心率。若 IR 画面其实是彩色 USB 摄像头，")
print("  则完全可用。看上面的通道差即可判断。")
