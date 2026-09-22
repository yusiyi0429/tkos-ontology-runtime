# Method 0.5 独立 API 验收

合成人与受控 Agent 的真实 HTTP／PostgreSQL 验收，覆盖 0.5 相对 0.4 的全部差异：按范围确认的 Constraint 与跨域引用拒绝、LTCO 结论（首次确立 / 维持）、只有 DRI 的承诺与 Owner 生效记录、生成即正式的状态与下钻、CEO 确认复盘与下期 PCO 承接、公司集合视图与确认记录投影。另核对真实 0.5 对象的读取带 `method_v0_5` 解释状态，以及本 scope 每条绑定行都钉定 0.5 profile 身份、经由 0029 的绑定插入闸门写入。不证明真实模型行为，不代替 0.4 复跑。

前提：隔离 PG + MinIO、应用与迁移角色分离、仅本人可读的 `.runtime-acceptance/*/env.json`（不打印、不提交）。数据库须是新建的 `tkos_a1_method_*` 隔离库，并已迁移到当前 HEAD（含 `0029_method_v05.sql`）且授予应用角色运行时表权限；建库流程见 `acceptance/method_independent/README.md`。

```sh
.venv/bin/python -m acceptance.method_v05.run \
  --env-file .runtime-acceptance/method-local-database/env.json \
  --private .runtime-acceptance/method-v05-$(date +%Y%m%d-%H%M) \
  --output artifacts/runtime-acceptance/method-v05-$(date +%Y%m%d-%H%M)
```

`summary.json` 的 `runtime_method_v05_api_accepted` 只有在全部检查通过时才为 true。
