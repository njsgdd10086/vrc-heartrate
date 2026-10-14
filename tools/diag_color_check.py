"""直接测量已保存的图像，确认其颜色特征（判断是否红外）"""
import sys

for _s in ("stdout", "stderr"):
    st = getattr(sys, _s, None)
    if st is not None and hasattr(st, "reconfigure"):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import cv2
import numpy as np

for idx in (0, 1, 7):
    p = rf"D:\deepseek_harness\_cam{idx}.png"
    img = cv2.imread(p)
    if img is None:
        print(f"索引 {idx}: 读不到 {p}")
        continue
    b, g, r = (img[:, :, i].astype(np.float32) for i in range(3))
    # 逐像素通道差，而不是先算均值再相减（后者会被平均抵消）
    d_bg = float(np.mean(np.abs(b - g)))
    d_gr = float(np.mean(np.abs(g - r)))
    d_br = float(np.mean(np.abs(b - r)))
    print(f"索引 {idx}  ({img.shape[1]}x{img.shape[0]})")
    print(f"    均值      B={b.mean():6.1f}  G={g.mean():6.1f}  R={r.mean():6.1f}")
    print(f"    逐像素通道差  |B-G|={d_bg:6.2f}  |G-R|={d_gr:6.2f}  |B-R|={d_br:6.2f}")
    # 饱和度：0 = 完全灰阶
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sat = float(hsv[:, :, 1].mean())
    print(f"    HSV 饱和度均值 = {sat:6.2f}   (>25 基本可确定是彩色摄像头)")
    # 判断
    if sat < 8:
        print("    => 几乎无色彩，疑似红外/灰阶")
    else:
        print("    => 有明确色彩，可用于 rPPG")
    print()
