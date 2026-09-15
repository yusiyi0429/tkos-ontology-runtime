# Anchor 0.3 隔离验收

运行位置为 Runtime 独立工作区；Python 3.12+、uv，以及已配置的本机 PostgreSQL/MinIO。沿用 ../method_independent/README.md 的角色分离。database.py 当前明确绑定本次保留的 `tkos-clark-linked-20260914-postgres-1`；其他环境须先核对容器与端点，不得误用生产连接。

```sh
uv run python -m acceptance.anchors_v03.database --env-file .runtime-acceptance/lifecycle-v02-db/env.json --private .runtime-acceptance/anchors-v03-db --output .runtime-acceptance/anchors-v03-db-report
uv run python -m acceptance.anchors_v03.run --env-file .runtime-acceptance/anchors-v03-db/env.json --private .runtime-acceptance/anchors-v03-run/private --output .runtime-acceptance/anchors-v03-run/report
```

首次命令创建新数据库；不要覆盖已有私有 env 文件。迁移至 0025，再次迁移必须无新增。API 和迁移所有者角色分离；身份和授权仅在合成 scope 初始化，所有业务对象通过合法动作建立，SQL 用于结果核对。

runner 启动本轮 src 的 API 子进程，实际 HTTP/PG/MinIO，不使用旧容器 API。35 个检查包含 M1B、State、Architecture 原子变更、移交故障回滚、并发双击、撤权、重启、旧版共存和历史依据显式重开。故障注入仅在带私有 acceptance key 的测试进程启用。

私有目录含凭据、Context、请求和日志，禁止提交。仅发布审阅后的 summary.json。模型是受控输入，不能宣称真实模型或 Clark 浏览器通过。若改为容器联调，须从当前源码重新构建并记录源码／镜像／契约版本。
