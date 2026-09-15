# Lifecycle 0.2 independent acceptance

从仓库根目录运行。复用现有 localhost:54350 PostgreSQL 与 localhost:54351 MinIO；不重建/替换 Clark，不新增容器。APP 与 MIGRATION 身份必须分离，凭据仅从权限受限的私有 env.json 读取。

```sh
uv run python -m acceptance.lifecycle_v02.database \
  --env-file .runtime-acceptance/clark-linked-db/env.json \
  --private .runtime-acceptance/lifecycle-new-db \
  --output .runtime-acceptance/lifecycle-new-db-report

uv run python -m acceptance.lifecycle_v02.run \
  --env-file .runtime-acceptance/lifecycle-new-db/env.json \
  --private .runtime-acceptance/lifecycle-new-run \
  --output .runtime-acceptance/lifecycle-new-report
```

每次使用新路径。建库脚本在现有指定容器内新建随机数据库，不清空已有数据库，不修改现有容器。业务数据经合法 HTTP 动作产生；SQL 仅建立隔离身份/政策夹具、运行追加迁移及物理核对结果。

验收覆盖两个协议版本并存、完整深入研究链、轻量材料准入、评论关闭竞争、候选写入回滚、重复提交、正式 Mission、独立事实聚合、研究 Context 与快照、截止后的迟到提交、显式重开、API 重启、跨版本引用拒绝、撤权与协议冻结。已发布的 0.1 独立验收另行运行，不能用这个增量报告替代完整发布验收。

输出保留在私有目录，审查后仅提取 summary。受控产物用于验证 Runtime，不代表真实模型质量、Clark 页面接线或浏览器闭环。已保存的旧工作区和旧数据库保留供核查。
