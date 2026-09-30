# World 0.2 freeze checkpoint

- 锁版日期：2026-09-30（#86；ADR-0009 的「锁版记录」）
- 时间（UTC）: 2026-09-30T09:15:07Z
- 钉定提交: `c26253433fec8f9ab12b5a7842c38c66552c8505`（分支 `world/0.2-lock`）。本页在其后的提交里写，没有改任何钉定文件
- profile：`urn:tkos:world` 修订 0.2.0，结构版本 `tkos.world-profile/0.2`，显示名 `World 0.2 business world model 2026-09-30`，`canonical_hash` `a0ab401d435353bc21748ba955cc5c1629e119b3533cb3494eaaa6dc0f9a57b6`
- 登记：`tkos.world-registry` 0.2.0，`status: frozen`
- 迁移：`0039_world_v02.sql` 冻结，之后不可重写
- 验收：**待验证门**（见最后一节）

## 钉定哈希

钉定链：`world_v02_profile.py` 的 `CONTRACT_SHA256` 与 `REGISTRY_SHA256` → `docs/contracts/world-profile-0.2.json`（两个哈希与 `canonical_hash`）→ 随包的登记副本（与登记逐字节相同）→ 0039 绑定门的 0.2 分支 → `scripts/render_world_v02_tables.py --check`（契约里由登记生成的 13 张表与登记一致）。

| 文件 | SHA256 |
|---|---|
| `docs/contracts/tkos-world-0.2.md` | `aac8b40d6f3310d55667dfc41f3225fb95f6649495c6c919f3a8ef694e7dc6ef` |
| `docs/contracts/world-registry-0.2.json` | `1d5731ce51596d932be1e4fdff8a6c62c9f5bd64b48f11aa7fc36081ac729d1d` |
| `docs/contracts/world-profile-0.2.json` | `2840ca2debaf50b69452c0c8a33ba94908c77db20dc0c1319615cb4801e4a1a9` |
| `docs/runtime-world-support-0.2.json` | `0ec325a1a1e3afc4a305b2f40d3457863b512831987160b394d9dc960865a389` |
| `src/memory_service_app/migrations/0039_world_v02.sql` | `5484d4be08a4050bd502088b18710574cbfd35cdbe889f07dbda013f448ddc1c` |
| `src/memory_service_runtime/governed/resources/world-registry-0.2.json` | `1d5731ce51596d932be1e4fdff8a6c62c9f5bd64b48f11aa7fc36081ac729d1d` |
| `src/memory_service_runtime/governed/world_v02_profile.py` | `1c0c783d1a597be70348264d2ff42830a56f182191e22071d629daaa3fb04617` |

## 待决项的最终落稿

契约第 19 节全文为准，这里只列结论。第 7 至 13 项 E&O 已于 2026-09-28 确认，其余 2026-09-30 定。

| 编号 | 事项 | 落稿 |
|-|-|-|
| 1 | 核心战役 | 方案 A，关注标记；方案 B 未采纳，留在附录 A 作说明 |
| 2 | Issue | 问题组件；升为业务对象未采纳 |
| 3 | 验证范围 | 不影响本契约 |
| 4 | Activity 去留 | 留，正式类型（最小任务单元），登记 `candidate: false` |
| 5 | 真实战略材料 | 不影响本契约 |
| 6 | 代记是否含建对象 | 不含；建对象不在本版的代记动作族里 |
| 7 | 组件 id | 写入者可以给，不给由服务生成 |
| 8 | 组件的责任人 | 计划条目可以带，只作记录 |
| 9 | 乱序 | 外部事件与状态刷新接受并标迟记；门事件与生命周期事件不接受补记 |
| 10 | 取消是否设门 | 不设门，由上一级责任人记 |
| 11 | 重开与退回 | 上一级责任人 |
| 12 | Activity 的指派者 | Task DRI |
| 13 | Agreement | 按 scope 与本轮指定判权 |
| 14 | 方法侧输入 | 按 Content Pact 首版替换（映射表），八个对齐点按默认做法 |
| 15 | 快照的存储 | 不改存储，语义按时间记录 |
| 16 | 上下文包 | 归入时间记录 |
| 17 | 已有 0.1 数据的 scope | 0.2 只在新建 scope 启用；world-lab 升级另立一步 |
| 18 | 预算与模型 | 预算默认 100000 字符，每个对象 10 条事件、近期 30 天；实验用模型不影响本契约 |

锁版前一并改完的：#85（术语表补 RU DRI、Mission DRI、Task DRI、取上下文角色、投影项；空块标准句改为「〈块名〉：暂无」）；Activity 转正（读投影 `business.candidate` 字段保留，各类型都为 false）。

## 划出去的事

以下不在本次锁版里，锁版也不等它们：

- **world-lab 升级到 0.2**：另立一步，由用户另批（待决 17）。world-lab 仍跑 0.1。
- **实验 B 与四种取法的结论**：两组实验照做，结论不再决定本版的对象结构（Activity 已定留）；实验报告里的触发条件只作以后版本调整对象结构的依据。
- **真实战略材料**：#67 替换实验 E 的战略材料，只改实验数据，不改契约与登记；实验 E 的标准答案要 E&O DRI 重新批准。

另有两份锁版前的录制物没有重录：`tests/fixtures/world_v02_transcribe/bundle.json`（实验 B 转写测试的读投影）与 `docs/world-v02-tianshu-examples.md`（给天枢的实测示例）里仍是旧的空块句、Activity 的 `candidate: true` 与旧的 profile `canonical_hash`。前者的测试不断言这些字段；后者 10 月 8 日在实例上重跑 examples 时重新生成。

## 验证数字

**待验证门**，由后续验证填。#86 要求的各项：

| 项 | 结果 |
|-|-|
| 无库全量（`-m "not db"`） | 待验证门 |
| `-m db` 全量 | 待验证门 |
| 0.2 独立验收矩阵（新库） | 待验证门 |
| 从 0.1 升级的回归（同库 0.1 验收） | 待验证门 |
| 离线包升级测试 | 待验证门 |
| method_v05 与 A1、A2、A3 独立矩阵 | 待验证门 |

验证门要注意：0.2 验收跑器还是锁版前的写法，`acceptance/world_v02/run.py` 把 `world_v02_accepted` 写死为 false、范围写「not frozen」，报告模板（`matrix.py`）也写「锁版前不冻结」。0.1 由 `acceptance/world_v01/summarize.py` 从通过的报告生成摘要与检查点，0.2 还没有对应的工具。
