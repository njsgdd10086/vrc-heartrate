#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 njsgdd10086
#
# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，
# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。

"""用 GitHub REST API 提交文件（无法直连 github.com 时的备用通道）

背景: 某些网络环境下 `git push`（走 github.com:443）会被阻断，而
api.github.com 仍可用。此时可以用 REST API 逐文件提交，绕过 git 传输协议。

原理: Contents API 每写一个文件就产生一次提交。为避免"每个文件一次提交"
把历史搞乱，这里改走 Git Data API:
    1) 取当前 commit 的 tree
    2) 把要改的文件写成 blob
    3) 建新 tree（替换对应路径）
    4) 建新 commit（parent = 当前 HEAD）
    5) 更新分支引用
一次调用完成一个"提交"，与 git commit 等价。

用法:
    python tools/gh_api_commit.py --token-file _test/.gh_token.txt \\
        --repo owner/name --message-file msg.txt [--branch main] \\
        [--dry-run] PATH [PATH ...]

注意: 只提交列出的文件，不会碰仓库里其他内容。
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"


def call(url, token, method="GET", payload=None, timeout=120):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "vrc-heartrate-api-commit")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(payload).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message-file", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args()

    with open(args.token_file, "r", encoding="utf-8") as f:
        token = f.read().strip()
    with open(args.message_file, "r", encoding="utf-8") as f:
        message = f.read().strip()

    repo = args.repo
    branch = args.branch

    # 1) 当前分支指向的 commit
    st, ref = call(f"{API}/repos/{repo}/git/ref/heads/{branch}", token)
    if st != 200:
        print(f"取分支失败 HTTP {st}: {ref.get('message')}")
        return 1
    head_sha = ref["object"]["sha"]
    print(f"当前 {branch} HEAD: {head_sha[:7]}")

    st, commit = call(f"{API}/repos/{repo}/git/commits/{head_sha}", token)
    if st != 200:
        print(f"取 commit 失败 HTTP {st}")
        return 1
    base_tree = commit["tree"]["sha"]

    # 2) 逐文件上传 blob，收集 tree 条目
    entries = []
    for rel in args.paths:
        if not os.path.isfile(rel):
            print(f"  ✗ 文件不存在: {rel}")
            return 1
        with open(rel, "rb") as f:
            raw = f.read()
        # Git 仓库里统一用 LF，避免 CRLF 污染
        if rel.endswith((".py", ".spec", ".md", ".txt", ".gitignore")) or \
                os.path.basename(rel) in ("LICENSE",):
            raw = raw.replace(b"\r\n", b"\n")
        b64 = base64.b64encode(raw).decode("ascii")
        if args.dry_run:
            print(f"  [dry-run] {rel}  {len(raw)} 字节")
            entries.append({"path": rel, "mode": "100644",
                            "type": "blob", "sha": "0" * 40})
            continue
        st, blob = call(f"{API}/repos/{repo}/git/blobs", token, "POST",
                        {"content": b64, "encoding": "base64"})
        if st not in (200, 201):
            print(f"  ✗ 上传 blob 失败 {rel} HTTP {st}: {blob.get('message')}")
            return 1
        print(f"  ✓ {rel}  blob {blob['sha'][:7]}  {len(raw)} 字节")
        entries.append({"path": rel, "mode": "100644",
                        "type": "blob", "sha": blob["sha"]})

    if args.dry_run:
        print("\ndry-run 结束，未做任何修改")
        return 0

    # 3) 新 tree（基于原 tree，替换列出的路径）
    st, tree = call(f"{API}/repos/{repo}/git/trees", token, "POST",
                    {"base_tree": base_tree, "tree": entries})
    if st not in (200, 201):
        print(f"建 tree 失败 HTTP {st}: {tree.get('message')}")
        return 1
    print(f"新 tree: {tree['sha'][:7]}")

    # 4) 新 commit
    st, newc = call(f"{API}/repos/{repo}/git/commits", token, "POST",
                    {"message": message, "tree": tree["sha"],
                     "parents": [head_sha]})
    if st not in (200, 201):
        print(f"建 commit 失败 HTTP {st}: {newc.get('message')}")
        return 1
    print(f"新 commit: {newc['sha'][:7]}")

    # 5) 更新分支引用
    st, upd = call(f"{API}/repos/{repo}/git/refs/heads/{branch}", token,
                   "PATCH", {"sha": newc["sha"], "force": False})
    if st != 200:
        print(f"更新分支失败 HTTP {st}: {upd.get('message')}")
        print(f"  手动更新: git update-ref refs/heads/{branch} {newc['sha']}")
        return 1
    print(f"✓ {branch} 已更新为 {newc['sha'][:7]}")
    print(f"  https://github.com/{repo}/commit/{newc['sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
