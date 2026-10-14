#!/usr/bin/env python3
"""下载运行所需的模型权重

两个模型都来自公开仓库，脚本会自动下载并校验大小。
重复运行是安全的：文件已存在且大小正确时会跳过。

用法:
    python tools/download_models.py            # 下载缺失的
    python tools/download_models.py --force    # 强制重新下载
"""
import argparse
import os
import shutil
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (目标路径, 来源 URL 列表(依次尝试), 期望大小(字节, None 表示只校验非空),
#  说明)
MODELS = [
    (
        os.path.join(ROOT, "models", "face_detection_yunet_2023mar.onnx"),
        [
            # OpenCV Zoo 用 Git LFS 存放大文件，raw 链接拿到的是 LFS 指针，
            # 所以优先走 LFS API，失败再退回 git clone
            "LFS:opencv/opencv_zoo:models/face_detection_yunet/"
            "face_detection_yunet_2023mar.onnx",
        ],
        232589,
        "YuNet 人脸检测模型 (OpenCV Zoo)",
    ),
    (
        os.path.join(ROOT, "models", "FacePhys.onnx"),
        ["GIT:https://github.com/KegangWangCCNU/FacePhys-Release.git"],
        3159827,
        "FacePhys rPPG 模型",
    ),
    (
        os.path.join(ROOT, "models", "FacePhys-LICENSE.txt"),
        ["GITLIC:https://github.com/KegangWangCCNU/FacePhys-Release.git"
         "|LICENSE"],
        None,
        "FacePhys 许可证（含隐私保护附加条款，再分发必须保留）",
    ),
]

GIT_LFS_ENDPOINT = "https://github.com/{repo}.git/info/lfs/objects/batch"


def human(n):
    return f"{n / 1024 / 1024:.1f} MB" if n and n > 1024 * 1024 else f"{n} B"


def ok(path, expect):
    """文件已存在且大小合理"""
    if not os.path.isfile(path):
        return False
    size = os.path.getsize(path)
    if expect:
        return size == expect
    return size > 1024


def download_via_git_clone(repo, dest_file, tmp_root, src_rel=None):
    """整体 clone 仓库，然后取出需要的文件（用于 GitHub LFS 文件）

    src_rel: 文件在仓库中的相对路径。给定时精确取该文件（找不到就报错，
    不做模糊匹配）；未给定时按同名文件全局搜索。
    许可证这类文件必须用 src_rel 精确指定 —— 仓库里可能有多个同名文件。
    """
    tmp = os.path.join(tmp_root, "_clone")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"    git clone {repo} ...")
    r = subprocess.run(["git", "clone", "--depth", "1", repo, tmp],
                       capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        print(f"    clone 失败: {(r.stderr or '').strip()[:200]}")
        return False

    hit = None
    if src_rel:
        cand = os.path.join(tmp, src_rel.replace("/", os.sep))
        if os.path.isfile(cand):
            hit = cand
        else:
            print(f"    仓库里没找到 {src_rel}")
    if hit is None and not src_rel:
        target = os.path.basename(dest_file)
        for dirpath, _dirs, files in os.walk(tmp):
            if target in files:
                hit = os.path.join(dirpath, target)
                break
    if hit is None:
        if not src_rel:
            print(f"    仓库里没找到 {os.path.basename(dest_file)}")
        shutil.rmtree(tmp, ignore_errors=True)
        return False

    os.makedirs(os.path.dirname(dest_file), exist_ok=True)
    shutil.copyfile(hit, dest_file)
    shutil.rmtree(tmp, ignore_errors=True)
    return True


def download_via_lfs(spec, dest_file):
    """通过 Git LFS API 取单个文件（用于 OpenCV Zoo）"""
    import json
    try:
        _, repo, path = spec.split(":", 2)
    except ValueError:
        return False
    # 先拿 LFS 指针里的 oid/size
    raw = (f"https://raw.githubusercontent.com/{repo}/main/{path}")
    try:
        with urllib.request.urlopen(raw, timeout=60) as resp:
            head = resp.read(400).decode("utf-8", "replace")
    except Exception as e:
        print(f"    读取 LFS 指针失败: {e}")
        return False
    import re
    m_oid = re.search(r"oid sha256:([0-9a-f]{64})", head)
    m_size = re.search(r"size (\d+)", head)
    if not m_oid or not m_size:
        print("    该文件不是 LFS 指针，改为直接下载")
        return download_via_url(raw, dest_file)
    oid, size = m_oid.group(1), int(m_size.group(1))
    body = json.dumps({
        "operation": "download", "transfers": ["basic"],
        "objects": [{"oid": oid, "size": size}],
    }).encode()
    req = urllib.request.Request(
        GIT_LFS_ENDPOINT.format(repo=repo), data=body,
        headers={"Accept": "application/vnd.git-lfs+json",
                 "Content-Type": "application/vnd.git-lfs+json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            info = json.loads(resp.read().decode())
        href = info["objects"][0]["actions"]["download"]["href"]
    except Exception as e:
        print(f"    LFS 协商失败: {e}")
        return False
    return download_via_url(href, dest_file)


def download_via_url(url, dest_file):
    os.makedirs(os.path.dirname(dest_file), exist_ok=True)
    tmp = dest_file + ".part"
    try:
        with urllib.request.urlopen(url, timeout=300) as resp, \
                open(tmp, "wb") as f:
            shutil.copyfileobj(resp, f)
        os.replace(tmp, dest_file)
        return True
    except Exception as e:
        print(f"    下载失败: {e}")
        if os.path.isfile(tmp):
            os.remove(tmp)
        return False


def main():
    ap = argparse.ArgumentParser(description="下载模型权重")
    ap.add_argument("--force", action="store_true", help="强制重新下载")
    args = ap.parse_args()

    tmp_root = os.path.join(ROOT, "_dl_tmp")
    os.makedirs(tmp_root, exist_ok=True)
    failed = []

    for dest, sources, expect, desc in MODELS:
        rel = os.path.relpath(dest, ROOT)
        print(f"\n[{desc}]")
        print(f"  目标: {rel}")
        if not args.force and ok(dest, expect):
            print(f"  已存在且大小正确 ({human(os.path.getsize(dest))})，跳过")
            continue
        got = False
        for src in sources:
            if src.startswith("GITLIC:"):
                spec_ = src[7:]
                repo, _, rel = spec_.partition("|")
                got = download_via_git_clone(repo, dest, tmp_root,
                                             src_rel=rel or None)
            elif src.startswith("LFS:"):
                got = download_via_lfs(src[4:], dest)
            elif src.startswith("GIT:"):
                got = download_via_git_clone(src[4:], dest, tmp_root)
            else:
                got = download_via_url(src, dest)
            if got:
                break
        if got and os.path.isfile(dest):
            size = os.path.getsize(dest)
            if expect and size != expect:
                print(f"  ⚠ 下载完成但大小不符: 期望 {expect} 实际 {size}")
                failed.append(rel)
            else:
                print(f"  ✓ 完成 ({human(size)})")
        else:
            print(f"  ✗ 失败")
            failed.append(rel)

    shutil.rmtree(tmp_root, ignore_errors=True)

    print()
    if failed:
        print("以下文件未能获取，请手动下载:")
        for f in failed:
            print(f"  - {f}")
        print()
        print("手动下载地址:")
        print("  YuNet:    https://github.com/opencv/opencv_zoo/tree/main/"
              "models/face_detection_yunet")
        print("  FacePhys: https://github.com/KegangWangCCNU/FacePhys-Release")
        return 1
    print("全部就绪。运行:  python osc_heartrate_cam.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
