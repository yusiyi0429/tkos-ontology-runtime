# Anchor 0.3 隔离验收

运行位置为 Runtime 独立工作区；Python 3.12+、uv，以及已配置的本机 PostgreSQL/MinIO。沿用 ../method_independent/README.md 的角色分离。建库统一用 `acceptance/method_v05/database.py`：只接受隔离验收栈（`python3 acceptance/runtime/infra.py up`），迁移到当前源码的全部迁移。原先的建库脚本会把目标容器改写为 Clark 联动栈（54350）并断言旧的迁移清单，已删除。本 runner 在迁移到 HEAD 的库上尚未重新验证。

```sh
STAMP=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.method_v05.database create \
  --env-file .runtime-acceptance/env.json \
  --private .runtime-acceptance/anchors-v03-db-$STAMP \
  --output artifacts/runtime-acceptance/anchors-v03-db-$STAMP
.venv/bin/python -m acceptance.method_v05.database upgrade \
  --env-file .runtime-acceptance/anchors-v03-db-$STAMP/env.json \
  --source src \
  --output artifacts/runtime-acceptance/anchors-v03-db-$STAMP-upgrade
```

```sh
uv run python -m acceptance.anchors_v03.run --env-file .runtime-acceptance/anchors-v03-db-$STAMP/env.json --private .runtime-acceptance/anchors-v03-run/private --output .runtime-acceptance/anchors-v03-run/report
```

建库命令创建新数据库；不要覆盖已有私有 env 文件。再次迁移必须无新增。API 和迁移所有者角色分离；身份和授权仅在合成 scope 初始化，所有业务对象通过合法动作建立，SQL 用于结果核对。

runner 启动本轮 src 的 API 子进程，实际 HTTP/PG/MinIO，不使用旧容器 API。35 个检查包含 M1B、State、Architecture 原子变更、移交故障回滚、并发双击、撤权、重启、旧版共存和历史依据显式重开。故障注入仅在带私有 acceptance key 的测试进程启用。

私有目录含凭据、Context、请求和日志，禁止提交。仅发布审阅后的 summary.json。模型是受控输入，不能宣称真实模型或 Clark 浏览器通过。若改为容器联调，须从当前源码重新构建并记录源码／镜像／契约版本。
