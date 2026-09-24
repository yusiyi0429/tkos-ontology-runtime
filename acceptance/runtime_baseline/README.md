# Runtime 容量与恢复基线

按 [docs/runtime-capacity-baseline.md](../../docs/runtime-capacity-baseline.md) 的阈值 T1–T8 测量，在本机隔离栈上运行：

```bash
python3 acceptance/runtime/infra.py up
python3 acceptance/runtime/infra.py run -- .venv/bin/python acceptance/runtime_baseline/run.py --variant V1 --summary /path/to/summary.json
```

- **配置**：`--variant` 可重复；V0 关掉锁与语句超时，V1 用默认值。
- **每种配置的准备**：新播种两家合成公司，另起一个测试专用 API（`acceptance/runtime/server.py`）。它能在拿到 scope 栅栏之后把一个事务挂起，用来模拟慢事务。
- **T5 的慢存储**：在对象存储前放一个本地转发，每个响应分块晚 2 秒送回。
- **输出**：完整结果写在 `artifacts/runtime-acceptance/<run>/baseline.json`；`--summary` 另写一份只有数字与通过与否的摘要。凭证只在 `.runtime-acceptance/<run>/`。
- **运行时长**：跑器会占满一家公司的连接与栅栏，完整一轮每种配置约 3 分钟。不要与别的完整验收并行。
