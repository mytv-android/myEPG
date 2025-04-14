#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gitee 镜像仓库自动瘦身（清空 + 重新推送）。

为什么需要它
------------
workflow 里每次都是 amend 成单 commit 后 `git push --force`，再由
hub-mirror-action 强推同步到 Gitee。强推会让上一个 commit 的整份快照
（output/epg.xml 约 30 MB + epg.gz 约 2.3 MB）在 Gitee 侧变成 unreachable
对象，一直占着磁盘，堆到超过仓库容量上限后连推送都会失败。

Gitee 没有开放 GC 接口
----------------------
v5（177 个接口）和 v8（371 个接口，仅企业版）的全量 OpenAPI 里都没有任何
gc / garbage / 瘦身相关的端点；网页上那个「存储库 GC」按钮走的是需要登录态
的内部路由 POST /{owner}/{repo}/git_gc，不接受 access_token。社区版 API 也
读不到仓库体积（repo_size 只有企业版才有）。

所以能用 token 做到真正回收磁盘的，只有 Gitee 自己帮助文档里写的那一招：
「短时间内多次强推瘦身，GC 可能无法满足清理短期对象数据的诉求，可在备份
仓库后清空仓库再推送。」

本脚本做的事
------------
按天调度调用 PUT /v5/repos/{owner}/{repo}/clear 把 Gitee 上的代码清空，
紧接着的 hub-mirror-action 会把单 commit 的新历史推回去，仓库回到一份快照的
大小。清空只清代码、不动 Issues / Wiki 和其他设置（Gitee 设置页原文），
本仓库没用 Gitee Pages，所以没有额外影响。

用法
----
    # GitHub Actions 里由 workflow 调用，通常不用手动跑
    GITEE_TOKEN=xxx python3 scripts/gitee_slim.py

    # 已经超限了，想立刻清一次
    GITEE_TOKEN=xxx python3 scripts/gitee_slim.py --force

    # 只看会不会清、不发请求
    GITEE_TOKEN=xxx python3 scripts/gitee_slim.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://gitee.com/api/v5"
UA = "myEPG-gitee-slim/1.0"
DEFAULT_OWNER = "mytv-android"
DEFAULT_REPO = "myEPG"
VERIFY_PATH = "output/epg.xml"  # 清空后必须能被重新推送回来的文件


def log(msg: str) -> None:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def _api(method: str, path: str, token: str, params: dict | None = None, timeout: int = 120):
    """发一次 Gitee OpenAPI 请求，返回 (状态码, 响应体文本)。token 只走 query，不会被打日志。"""
    query = urllib.parse.urlencode({**(params or {}), "access_token": token})
    req = urllib.request.Request(
        f"{API}{path}?{query}",
        method=method,
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        return -1, f"网络错误: {e.reason}"


def days_since_epoch(now: dt.datetime) -> int:
    return int(now.replace(tzinfo=dt.timezone.utc).timestamp() // 86400)


def should_slim(every: int, now: dt.datetime) -> bool:
    """每 every 天清一次。用 UTC 天数取模，不依赖任何额外状态文件。"""
    if every <= 1:
        return True
    return days_since_epoch(now) % every == 0


def clear_repo(owner: str, repo: str, token: str) -> tuple[bool, str]:
    status, body = _api("PUT", f"/repos/{owner}/{repo}/clear", token)
    if status == 204:
        return True, "已清空（HTTP 204）"
    if status == 401:
        return False, "token 无效或已过期（HTTP 401）"
    if status == 403:
        return False, "token 没有该仓库的管理权限（HTTP 403）"
    if status == 404:
        return False, f"仓库 {owner}/{repo} 不存在（HTTP 404）"
    return False, f"清空失败（HTTP {status}）: {body[:200]}"


def verify_mirror(owner: str, repo: str, token: str) -> tuple[bool, str, bool]:
    """确认清空之后代码确实被推回来了，避免仓库被清空却同步失败、下载链接挂掉。

    返回 (是否通过, 说明, 是否致命)。只有明确查不到文件（404）才算致命；
    网络抖动或 Gitee 5xx 时查不出结论，只警告不阻断，免得误伤正常的同步。
    """
    status, body = _api("GET", f"/repos/{owner}/{repo}/contents/{VERIFY_PATH}", token, {"ref": "master"})
    if status == 200:
        try:
            size = json.loads(body).get("size")
        except ValueError:
            size = None
        detail = f"{VERIFY_PATH} 在 master 上，大小 {size} 字节" if size else f"{VERIFY_PATH} 在 master 上"
        return True, detail, False
    if status == 404:
        return False, f"master 上找不到 {VERIFY_PATH}，同步可能失败了", True
    if status < 0:
        return False, f"校验请求没发出去：{body}", False
    return False, f"校验接口返回 HTTP {status}: {body[:200]}", False


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log(f"环境变量 {name}={raw!r} 不是数字，按默认值 {default} 处理")
        return default


def main(argv: list[str] | None = None) -> int:
    # Windows 默认 GBK 控制台会把中文日志打成乱码
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(
        description="Gitee 没有开放 GC 接口，用「清空仓库 + 重新推送」等效回收强推产生的垃圾对象。",
    )
    parser.add_argument("--owner", default=os.environ.get("GITEE_OWNER", DEFAULT_OWNER))
    parser.add_argument("--repo", default=os.environ.get("GITEE_REPO", DEFAULT_REPO))
    parser.add_argument("--token", default=os.environ.get("GITEE_TOKEN", ""),
                        help="Gitee 私人令牌，默认读环境变量 GITEE_TOKEN")
    parser.add_argument("--every", type=int, default=_env_int("SLIM_EVERY_DAYS", 2),
                        help="每几天清一次，默认 2；1 表示每次都清")
    parser.add_argument("--force", action="store_true", help="不管调度，立刻清一次")
    parser.add_argument("--verify", action="store_true", help="只校验镜像是否完好，不清空")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要做什么，不发请求")
    args = parser.parse_args(argv)

    if not args.token:
        log("没有 GITEE_TOKEN，跳过（本地没配 token 时这是正常的）")
        return 0

    if args.verify:
        ok, msg, fatal = verify_mirror(args.owner, args.repo, args.token)
        log(f"校验 {'通过' if ok else '未通过'}: {msg}")
        return 1 if (fatal and not ok) else 0

    if not args.force and not should_slim(args.every, dt.datetime.now(dt.timezone.utc)):
        log(f"今天不是清空日（每 {args.every} 天一次），跳过")
        return 0

    log(f"调度命中：清空 gitee.com/{args.owner}/{args.repo} 的代码，等 hub-mirror-action 推回新快照")
    if args.dry_run:
        log("dry-run，不发请求")
        return 0

    ok, msg = clear_repo(args.owner, args.repo, args.token)
    log(f"清空结果：{msg}")
    if not ok:
        # 清空失败只是体积继续涨，不影响本次同步，所以不当成致命错误
        log("警告：清空没成功，本次仍然继续同步，仓库体积会继续涨")
    return 0


if __name__ == "__main__":
    sys.exit(main())
