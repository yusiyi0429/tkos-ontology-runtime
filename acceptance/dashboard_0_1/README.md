# 看板 0.1 真实数据只读验收

对**显式隔离的** Method 0.1 数据集（例如升级演练 clone）执行 `tkos.dashboard/0.1`
读取模型，并以数据库前后表计数 oracle 证明浏览不写入。该 runner 以只读方式运行，
不会改动原始运行库。

```sh
uv run python -m acceptance.dashboard_0_1.run \
  --env-file <private clone env.json> \
  --identity-file <private identities.json> \
  --actor ceo \
  --output .runtime-acceptance/dashboard-0-1-report
```

- `--env-file`：包含 `APP_DATABASE_URL` 的隔离库私有 env（必须是 loopback 且库名以
  `tkos_a1_` 开头）。
- `--identity-file`：合成身份 JSON（`actors.<actor>.token`）；token 仅在进程内使用，
  不打印、不复制进报告。
- 报告 `summary.json` 只含检查名、读取数量与布尔结果，不含对象 ID。

覆盖：Strategy 选择与分组可用性、各分组与详情读取、`protocol`/精确 revision/hash、
类型化 downstream 双向核对（对象自己的 basis 引用必须出现在目标对象的 downstream）、
负责人任命状态显式、列表责任投影排除参与人、owner 筛选与记录责任一致、cursor 绑定、同屏依据合并视图不混入其他战略域、浏览前后表计数一致。真实 0.1 数据上应为
`Strategy→LTCO/PCO`、`LTCO→PCO`、`PCO→Mission`；没有 Architecture/State/Problem/
Fact/Review 时对应分组显式不可用，不补造。
