# 集众家所长的EPG

## 使用
https://github.com/mytv-android/myEPG/raw/refs/heads/master/output/epg.xml
https://github.com/mytv-android/myEPG/raw/refs/heads/master/output/epg.gz

或者

https://gitee.com/mytv-android/myepg/raw/master/output/epg.xml
https://gitee.com/mytv-android/myepg/raw/master/output/epg.gz

## Gitee 镜像的体积维护

GitHub Action 每次都是 amend 成单 commit 后强推，再同步到 Gitee。强推会让上一份
快照（epg.xml 约 30 MB + epg.gz 约 2.3 MB）在 Gitee 侧变成 unreachable 对象一直占着
磁盘，堆到超过仓库容量上限后连推送都会失败。

Gitee **没有开放 GC 的接口**：v5（177 个）和 v8（371 个，仅企业版）的全量 OpenAPI 里
都没有 gc 相关端点，网页上那个「存储库 GC」按钮走的是需要登录态的内部路由
`POST /{owner}/{repo}/git_gc`，不接受 `access_token`，社区版 API 也读不到仓库体积。
所以能用 token 做到真正回收磁盘的只有 Gitee 帮助文档里写的那招——清空仓库再推送。

`scripts/gitee_slim.py` 就是把这件事接进了 workflow（[.github/workflows/main.yml](.github/workflows/main.yml)）：

1. 强推同步到 Gitee **之前**，按天调度调用 `PUT /v5/repos/{owner}/{repo}/clear` 清空代码；
2. hub-mirror-action 紧接着把单 commit 的新历史推回去，仓库回到一份快照的大小；
3. 推完再 `--verify` 一次，确认 `output/epg.xml` 真的在 master 上，避免清空了却没推成功。

清空只清代码，不动 Issues / Wiki 和其他设置（Gitee 设置页原文），本仓库没用 Gitee
Pages，所以没有额外影响。默认每 2 天清一次，改 `main.yml` 里的 `SLIM_EVERY_DAYS`
即可，`1` 表示每次都清。

已经超限了想立刻清一次：

```bash
GITEE_TOKEN=你的令牌 python3 scripts/gitee_slim.py --force
```

然后手动跑一次 workflow 把新快照推回去。
