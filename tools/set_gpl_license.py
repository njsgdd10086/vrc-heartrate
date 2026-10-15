#!/usr/bin/env python3

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 njsgdd10086
#
# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，
# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。

"""把项目的 LICENSE 换成 GPL-3.0 全文（含项目版权声明与说明）

为什么用脚本而不是手写: GPL 全文约 35 KB，手抄容易出错或漏条款。
这里从 gnu.org 官方地址取，并校验关键段落是否齐全。
"""
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, "LICENSE")
URL = "https://www.gnu.org/licenses/gpl-3.0.txt"

# 优先用本机已有的权威副本（Windows 上装了 Git for Windows 就一定有，
# 来自 MSYS2 的 GCC 许可证目录）。网络受限时这条路更可靠。
LOCAL_CANDIDATES = [
    r"C:\Program Files\Git\mingw64\share\licenses\gcc-libs\COPYING3",
    r"C:\Program Files\Git\mingw64\share\licenses\libiconv\COPYING",
    r"C:\Program Files\Git\usr\share\licenses\libassuan\COPYING",
    r"C:\Program Files (x86)\Git\mingw64\share\licenses\gcc-libs\COPYING3",
    "/usr/share/common-licenses/GPL-3",
    "/usr/share/licenses/common/GPL3/license.txt",
]

# LICENSE 必须是**纯** GPL-3.0 全文，前面不能加任何自定义文字。
#
# 原因: GitHub 的许可证识别（licensee 库）按标准结构做模糊匹配。实测在
# LICENSE 开头加了版权声明与中文说明后，仓库侧边栏显示的许可证变成
# "NOASSERTION / Other" 而不是 GPL-3.0 —— 因为开头多了一段它无法归类的文本。
# 所以版权声明与"为什么选 GPL-3.0"的说明放在 COPYRIGHT.md，LICENSE 保持纯净。
HEADER = ""

# 必须存在的关键段落（用来确认全文完整、没被截断）
REQUIRED = [
    "GNU GENERAL PUBLIC LICENSE",
    "Version 3, 29 June 2007",
    "0. Definitions.",
    "1. Source Code.",
    "2. Basic Permissions.",
    "7. Additional Terms.",
    "8. Termination.",
    "11. Patents.",
    "13. Use with the GNU Affero General Public License.",
    "15. Disclaimer of Warranty.",
    "17. Interpretation of Sections 15 and 16.",
    "END OF TERMS AND CONDITIONS",
    "How to Apply These Terms to Your New Programs",
]

def load_text():
    """取 GPL-3.0 全文：先试本机副本，再试网络下载"""
    for p in LOCAL_CANDIDATES:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                if all(s in text for s in REQUIRED):
                    print(f"使用本机副本: {p}")
                    return text
                print(f"本机副本不完整，跳过: {p}")
            except Exception as e:
                print(f"读取失败 {p}: {e}")

    print(f"本机没有可用副本，尝试下载 {URL}")
    try:
        with urllib.request.urlopen(URL, timeout=90) as r:
            return r.read().decode("utf-8")
    except Exception as e:
        print(f"下载失败: {type(e).__name__}: {e}")
        return None

def main():
    text = load_text()
    if text is None:
        print()
        print("无法获取 GPL-3.0 全文，请手动处理：")
        print(f"  1) 打开 {URL}")
        print(f"  2) 全选复制，保存为 {DEST}（UTF-8）")
        print(f"  3) 在文件开头附上 tools/set_gpl_license.py 里的 HEADER")
        return 1

    print(f"  取得 {len(text)} 字符")

    missing = [s for s in REQUIRED if s not in text]
    if missing:
        print("  全文校验失败，缺少以下段落:")
        for m in missing:
            print(f"    - {m}")
        return 1
    print("  全文校验通过（关键段落齐全）")

    content = HEADER + text
    with open(DEST, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

    print(f"已写入 {DEST}")
    print(f"  总长度 {len(content)} 字符")
    return 0

if __name__ == "__main__":
    sys.exit(main())
