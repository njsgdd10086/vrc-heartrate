# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 njsgdd10086
#
# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，
# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。

"""启动部署好的 exe，读取它的输出，判断运行的是哪个版本的代码"""
import os
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

DEST = r"C:\Users\76540\Desktop\软件收纳\VR\摄像头心率VRChat"
EXE = os.path.join(DEST, "VRCHeartRateWeb.exe")

print(f"启动 {EXE}")
print("（等待 90 秒，观察输出）")
print()

p = subprocess.Popen([EXE, "--no-browser"],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     text=True, encoding="utf-8", errors="replace",
                     bufsize=1, cwd=DEST)

lines = []
t0 = time.time()
try:
    while time.time() - t0 < 90:
        line = p.stdout.readline()
        if not line:
            if p.poll() is not None:
                break
            continue
        line = line.rstrip()
        if re.search(r"VCAMDS|obsensor|cap_ffmpeg|^I2026|OnReadSample|"
                     r"cap_msmf|cap\.cpp|WARN:0|setPreferableTarget",
                     line):
            continue
        lines.append(line)
        print("  " + line)
finally:
    p.terminate()
    try:
        p.wait(timeout=10)
    except Exception:
        p.kill()

print()
print("=" * 70)
print("版本判定")
print("=" * 70)
joined = "\n".join(lines)
checks = [
    ("新版: 每个后端独立超时", "该后端阻塞", True),
    ("新版: 后端候选逐个尝试", "探测可读但采纳前验证失败", False),
    ("旧版: 整索引统一超时", "设备可能正被其他程序占用，已跳过", False),
    ("旧版: 4 秒超时", "探测超时（4秒）", False),
    ("枚举汇总行", "摄像头枚举完成", True),
]
for label, needle, want in checks:
    hit = needle in joined
    mark = "命中" if hit else "未命中"
    verdict = ""
    if want and hit:
        verdict = "  <- 符合新版"
    elif want and not hit:
        verdict = "  <- 缺失！"
    elif not want and hit:
        verdict = "  <- 仍是旧版！"
    print(f"  {label:24s} {mark}{verdict}")
print()
print("关键输出行:")
for ln in lines:
    if "枚举" in ln or "摄像头列表" in ln or "超时" in ln or "阻塞" in ln:
        print("   ", ln)
