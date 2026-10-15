# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 njsgdd10086
#
# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，
# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。

"""发布 GitHub Release（用 Python 处理 UTF-8 与长文本，避开 PowerShell 的编码坑）

用法:
    python tools/gh_release.py --token-file _test/.gh_token.txt \
        --repo njsgdd10086/vrc-heartrate --tag v1.0.0 \
        --notes _release_notes.md --asset VRCHeartRate-v1.0.0-win64.zip
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"

def request(url, token, method="GET", data=None, ctype="application/json",
            raw=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "vrc-heartrate-release")
    if raw is not None:
        req.add_header("Content-Type", ctype)
        req.data = raw
    elif data is not None:
        req.add_header("Content-Type", ctype)
        req.data = json.dumps(data).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=1800) as resp:
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
    ap.add_argument("--tag", required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--notes", default=None, help="返回说明的 markdown 文件")
    ap.add_argument("--asset", default=None, help="要上传的文件")
    ap.add_argument("--asset-name", default=None)
    args = ap.parse_args()

    with open(args.token_file, "r", encoding="utf-8") as f:
        token = f.read().strip()

    if args.notes:
        with open(args.notes, "r", encoding="utf-8") as f:
            notes = f.read()
    else:
        notes = ""

    # 1) 取或建 release
    st, rel = request(f"{API}/repos/{args.repo}/releases/tags/{args.tag}", token)
    if st == 404:
        print(f"创建 release {args.tag} ...")
        st, rel = request(f"{API}/repos/{args.repo}/releases", token, "POST",
                          {"tag_name": args.tag, "target_commitish": "main",
                           "name": args.name or args.tag, "body": notes,
                           "draft": False, "prerelease": False})
        if st not in (200, 201):
            print(f"  失败 HTTP {st}: {rel.get('message')}")
            return 1
        print(f"  已创建: {rel['html_url']}")
    elif st == 200:
        print(f"release 已存在: {rel['html_url']}")
        # 更新说明
        st2, rel2 = request(f"{API}/repos/{args.repo}/releases/{rel['id']}",
                            token, "PATCH", {"body": notes,
                                             "name": args.name or rel["name"]})
        if st2 == 200:
            rel = rel2
            print(f"  已更新说明，长度 {len(rel.get('body') or '')} 字符")
        else:
            print(f"  更新说明失败 HTTP {st2}: {rel2.get('message')}")
    else:
        print(f"查询 release 失败 HTTP {st}: {rel.get('message')}")
        return 1

    print(f"\n说明长度: {len(rel.get('body') or '')} 字符")
    print(f"说明开头: {(rel.get('body') or '')[:40]!r}")

    # 2) 上传资产
    if args.asset:
        aname = args.asset_name or os.path.basename(args.asset)
        size = os.path.getsize(args.asset)
        print(f"\n上传 {aname} ({size / 1024 / 1024:.1f} MB) ...")
        # 先看同名资产是否已存在
        st, assets = request(
            f"{API}/repos/{args.repo}/releases/{rel['id']}/assets", token)
        if st == 200:
            for a in assets:
                if a["name"] == aname:
                    print(f"  同名资产已存在，先删除 (id={a['id']})")
                    request(f"{API}/repos/{args.repo}/releases/assets/{a['id']}",
                            token, "DELETE")
        upload = (f"https://uploads.github.com/repos/{args.repo}"
                  f"/releases/{rel['id']}/assets?name={aname}")
        t0 = time.time()
        with open(args.asset, "rb") as f:
            blob = f.read()
        st, res = request(upload, token, "POST", raw=blob,
                          ctype="application/zip")
        dt = time.time() - t0
        if st in (200, 201):
            print(f"  上传成功，耗时 {dt:.0f}s")
            print(f"  大小: {res['size'] / 1024 / 1024:.1f} MB")
            print(f"  下载: {res['browser_download_url']}")
        else:
            print(f"  上传失败 HTTP {st}: {res.get('message') or res}")
            return 1

    print("\n完成")
    return 0

if __name__ == "__main__":
    sys.exit(main())
