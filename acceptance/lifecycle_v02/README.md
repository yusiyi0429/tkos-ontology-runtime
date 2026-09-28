# Lifecycle 0.2 independent acceptance

从仓库根目录运行。使用隔离验收栈，不连 54350/54351 的 Clark 联动栈。建库统一用 `acceptance/method_v05/database.py`：只接受隔离验收栈（`python3 acceptance/runtime/infra.py up`），迁移到当前源码的全部迁移。原先的建库脚本会把目标容器改写为 Clark 联动栈（54350）并断言旧的迁移清单，已删除。本 runner 在迁移到 HEAD 的库上尚未重新验证。APP 与 MIGRATION 身份必须分离，凭据仅从权限受限的私有 env.json 读取。

```sh
STAMP=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.method_v05.database create \
  --env-file .runtime-acceptance/env.json \
  --private .runtime-acceptance/lifecycle-v02-db-$STAMP \
  --output artifacts/runtime-acceptance/lifecycle-v02-db-$STAMP
.venv/bin/python -m acceptance.method_v05.database upgrade \
  --env-file .runtime-acceptance/lifecycle-v02-db-$STAMP/env.json \
  --source src \
  --output artifacts/runtime-acceptance/lifecycle-v02-db-$STAMP-upgrade
```

```sh
uv run python -m acceptance.lifecycle_v02.run \
  --env-file .runtime-acceptance/lifecycle-v02-db-$STAMP/env.json \
  --private .runtime-acceptance/lifecycle-new-run \
  --output .runtime-acceptance/lifecycle-new-report
```

每次使用新路径。建库脚本在隔离验收容器内新建随机数据库，不清空已有数据库，不修改现有容器。业务数据经合法 HTTP 动作产生；SQL 仅建立隔离身份/政策夹具、运行追加迁移及物理核对结果。

验收覆盖两个协议版本并存、完整深入研究链、轻量材料准入、评论关闭竞争、候选写入回滚、重复提交、正式 Mission、独立事实聚合、研究 Context 与快照、截止后的迟到提交、显式重开、API 重启、跨版本引用拒绝、撤权与协议冻结。已发布的 0.1 独立验收另行运行，不能用这个增量报告替代完整发布验收。

输出保留在私有目录，审查后仅提取 summary。受控产物用于验证 Runtime，不代表真实模型质量、Clark 页面接线或浏览器闭环。已保存的旧工作区和旧数据库保留供核查。
