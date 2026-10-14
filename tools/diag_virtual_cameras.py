"""找到 NVIDIA Broadcast / OBS 等虚拟摄像头的 OpenCV 索引，并测试能否正常取帧"""
import re
import subprocess
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

# DirectShow 设备枚举顺序 == OpenCV 的索引顺序，所以列出名称就能对上索引
print("=" * 74)
print("DirectShow 视频输入设备（顺序即 OpenCV 索引顺序）")
print("=" * 74)
ps = r"""
$clsid = '{860BB310-5D01-11d0-BD3B-00A0C911CE86}'
Get-ChildItem "HKLM:\SOFTWARE\Classes\CLSID\$clsid\Instance" -ErrorAction SilentlyContinue |
  Sort-Object PSChildName | ForEach-Object {
    $n = (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).FriendlyName
    if ($n) { "{0}|{1}" -f $_.PSChildName, $n }
  }
"""
try:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, timeout=40,
                         encoding="utf-8", errors="replace")
    lines = [l.strip() for l in (out.stdout or "").splitlines() if "|" in l]
    for i, l in enumerate(lines):
        guid, name = l.split("|", 1)
        mark = ""
        if re.search(r"nvidia|broadcast", name, re.I):
            mark = "   <== NVIDIA Broadcast 虚拟摄像头"
        elif re.search(r"obs", name, re.I):
            mark = "   <== OBS 虚拟摄像头"
        elif re.search(r"warudo", name, re.I):
            mark = "   <== Warudo 虚拟摄像头"
        print(f"  {name}{mark}")
except Exception as e:
    print(f"  枚举失败: {e}")

print()
print("=" * 74)
print("逐个索引测试实际取帧能力")
print("=" * 74)
for i in range(10):
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        continue
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    for _ in range(8):
        cap.read()
    good = 0
    last = None
    t0 = time.time()
    while time.time() - t0 < 1.2:
        r, fr = cap.read()
        if r and fr is not None and float(fr.std()) > 1:
            good += 1
            last = fr
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = good / max(time.time() - t0, 1e-6)
    sat = ""
    if last is not None:
        hsv = cv2.cvtColor(last, cv2.COLOR_BGR2HSV)
        sat = f" 饱和度={hsv[:,:,1].mean():5.1f}"
        cv2.imwrite(rf"D:\deepseek_harness\_idx{i}.png", last)
    print(f"  索引 {i}: {w}x{h}  有效帧={good:3d}  {fps:5.1f} fps{sat}")
    cap.release()
