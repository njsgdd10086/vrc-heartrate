#!/usr/bin/env python3
"""给仓库内所有源文件加 SPDX 许可头（GPL-3.0-or-later）

GPL 实践要求源文件明确标注许可证。SPDX 标识符是当下最通行的写法，
被 GitHub、REUSE 等工具识别。

会正确处理:
  - shebang（#!/usr/bin/env python3）必须留在第一行
  - 编码声明（# -*- coding: utf-8 -*-）必须留在前两行
  - 已有 SPDX 头的文件跳过（幂等，可重复运行）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SPDX = [
    "# SPDX-License-Identifier: GPL-3.0-or-later",
    "# Copyright (C) 2026 njsgdd10086",
    "#",
    "# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，版权与说明见 COPYRIGHT.md，",
    "# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。",
]

# 要处理的文件（相对仓库根目录）
TARGETS = [
    "osc_heartrate_cam.py",
    "osc_heartrate_ble.py",
    "facephys.py",
    "tools/build_web.spec",
    "tools/diag_camera_features.py",
    "tools/diag_camera_sharing.py",
    "tools/diag_color_check.py",
    "tools/diag_enum.py",
    "tools/diag_index_mapping.py",
    "tools/diag_pyvirtualcam.py",
    "tools/diag_thread_state.py",
    "tools/diag_virtual_cameras.py",
    "tools/download_models.py",
    "tools/fix_msvc_dlls.py",
    "tools/gh_release.py",
    "tools/inspect_build.py",
    "tools/profile_backends.py",
    "tools/profile_capture.py",
    "tools/profile_preview.py",
    "tools/profile_render.py",
    "tools/set_gpl_license.py",
    "tools/verify_build_version.py",
    "tools/verify_pipeline.py",
]


def add_header(path):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")

    if any("SPDX-License-Identifier" in ln for ln in lines[:6]):
        return "skip", "已有 SPDX 头"

    # 找出插入位置：跳过 shebang 和编码声明
    i = 0
    if i < len(lines) and lines[i].startswith("#!"):
        i += 1
    # 编码声明必须在第 1 或第 2 行
    if i < 2 and i < len(lines) and "coding" in lines[i] and lines[i].startswith("#"):
        i += 1

    new = lines[:i] + [""] + SPDX + [""] + lines[i:]
    # 去掉可能出现的连续空行
    out = []
    for ln in new:
        if out and ln == "" and out[-1] == "":
            continue
        out.append(ln)

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))
    return "ok", f"在行 {i + 1} 处插入"


def main():
    done = skipped = failed = 0
    for rel in TARGETS:
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            print(f"  跳过（不存在）: {rel}")
            failed += 1
            continue
        try:
            status, msg = add_header(p)
        except Exception as e:
            print(f"  ✗ {rel}: {type(e).__name__}: {e}")
            failed += 1
            continue
        if status == "skip":
            print(f"  - {rel}: {msg}")
            skipped += 1
        else:
            print(f"  ✓ {rel}: {msg}")
            done += 1
    print()
    print(f"已添加 {done} 个，跳过 {skipped} 个，失败 {failed} 个")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
