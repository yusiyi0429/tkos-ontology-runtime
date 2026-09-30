# World 0.2 freeze checkpoint

- 锁版日期：2026-09-30（#86；ADR-0009 的「锁版记录」）
- 时间（UTC）: 2026-09-30T09:15:07Z
- 钉定提交: `c26253433fec8f9ab12b5a7842c38c66552c8505`（分支 `world/0.2-lock`）。本页在其后的提交里写，没有改任何钉定文件
- profile：`urn:tkos:world` 修订 0.2.0，结构版本 `tkos.world-profile/0.2`，显示名 `World 0.2 business world model 2026-09-30`，`canonical_hash` `a0ab401d435353bc21748ba955cc5c1629e119b3533cb3494eaaa6dc0f9a57b6`
- 登记：`tkos.world-registry` 0.2.0，`status: frozen`
- 迁移：`0039_world_v02.sql` 冻结，之后不可重写
- 验收：验证门 2026-09-30 跑完，各项全部通过（见最后一节）；0.2 独立矩阵钉在 `7cda4d5`（分支 `world/0.2-gate`，只比 `a790d19` 多了验收跑器的钉提交开关，钉定文件逐字节未变）写出 `world_v02_accepted: true`

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

2026-09-30 在本机跑完，分支 `world/0.2-gate`。验收用的源码是提交 `7cda4d548075f80b47892db6784a9973fcc31612`（`7cda4d5`）：它在 `a790d19` 之上只改了 0.2 验收跑器（`acceptance/world_v02/` 与它的无库测试，加 `--commit`），上面七个钉定文件的 SHA256 与本页一致，0.2 的报告也逐个记下了这七个哈希（`frozen_files`）。所有库都在隔离验收栈（`tkos-ontology-runtime-acceptance`，PostgreSQL 127.0.0.1:55212、MinIO 55213）上新建，没有连 54350/54351 的 Clark 联动栈。原始报告在本机 `artifacts/runtime-acceptance/` 与 `.runtime-acceptance/` 下，不入库。

| 项 | 库 | 总数 | 通过 | 失败 | 跳过 | 说明 |
|-|-|-|-|-|-|-|
| a. 无库全量（`uv run pytest tests -q -m "not db"`） | 无 | 4533 | 4533 | 0 | 0 | 另有 142 条 db 用例按标记不选（见 b）；`dashboard_dist` 资产校验在其中 |
| b. `-m db` 全量，应用角色（`-m "db and not owner"`） | `tkos_a1_method_139fa69eee8347d6` | 100 | 100 | 0 | 0 | 经 `infra.py run`；默认库没有重建，见下 |
| b. `-m db` 全量，迁移所有者（`-m owner`） | 同上 | 42 | 42 | 0 | 0 | 经 `infra.py run --migration`；100＋42 正好是 a 里不选的 142 条 |
| c. 0.2 独立矩阵（新库，`--commit 7cda4d5`） | `tkos_a1_method_5539c8883db549b9` | 871 | 871 | 0 | 0 | 25 个场景全跑完；矩阵 445 格：覆盖 440、不适用 5、未覆盖 0；`world_v02_accepted: true` |
| d1. 同库回归（README 原样）：0.1 | `tkos_a1_world_9734236e593847c8` | 248 | 248 | 0 | 0 | 基线 0029 后一次升级到本分支（0030–0039），再跑 0.1：12 组全过、四个环境门槛全过；开发运行没钉提交，`world_api_accepted` 按设计为 false |
| d1. 同库回归：0.2（`--commit 7cda4d5`） | 同上 | 871 | 871 | 0 | 0 | 迁移组核对库里 125 条 0.1 事件都没有 0.2 字段；`world_v02_accepted: true` |
| d2. 从 0.1 升级：0.1 | `tkos_a1_world_f0e1477fc8ce408c` | 248 | 248 | 0 | 0 | 基线 0029 → 升级到 main `deb0d12`（0030–0038）→ 用 main 的源码与验收代码跑 0.1：12 组、四个门槛全过（开发运行，不写通过） |
| d2. 从 0.1 升级：只应用 0039，再跑 0.2（`--commit 7cda4d5`） | 同上 | 871 | 871 | 0 | 0 | 升级只应用 `0039_world_v02.sql`，重复为空；0.1 验收留下的 125 条事件在升级后读作 0.1、没有 0.2 字段；`world_v02_accepted: true` |
| e. 离线包升级（v0.5.0 → 本分支，arm64） | 一次性 compose 栈 | 14 | 14 | 0 | 0 | 见下 |
| f. method_v05 独立矩阵 | `tkos_a1_method_487fe1ef72cb44fc` | 27 | 27 | 0 | 0 | `runtime_method_v05_api_accepted: true` |
| f. A1 独立矩阵（无库部分） | 无 | 35＋34＋12 | 全过 | 0 | 完整 runner 未跑 | 旧源序列化 golden 35/35、验收器自检 34/34、CLI 适配自检 12/12；完整 runner 未跑，原因见下 |
| f. A2、A3 独立矩阵 | — | — | — | — | 未跑 | 原因见下 |
| g. 看板（`npm test`、`npm run typecheck`） | 无 | 214（26 个文件） | 214 | 0 | 0 | 类型检查退出码 0 |

**默认库没有重建。** 隔离栈的共享库 `tkos_runtime_acceptance` 仍记着锁版前的 0039（SHA256 前缀 `b2b4d5e3`，锁版的是 `5484d4be`），`infra.py migrate` 与 `up` 在它上面会报「已应用的迁移文件被改动过」。`infra.py` 只有 up/start/stop/status/migrate/attach/run，没有删库重建的办法（`attach` 只给 CI 的一次性 PostgreSQL 用），按要求没有手工删库。b 改在同一隔离栈上用 `acceptance/method_v05/database.py` 新建的库跑：授权取自发布规则，与 `infra.py` 的授权同源；在 `infra.py run` 的子进程里把 `DATABASE_URL` 换成新库（`--migration` 那一轮换成新库的所有者），`test_narrative_legacy` 用 `TKOS_LEGACY_ACCEPTANCE_DATABASE` 指到这个库。默认库要不要重建、怎么重建，留给人定。

**e 的做法与 14 项。** 构建：本分支 `7cda4d5` 的 wheel 加上 9/25 已下载、逐个对过锁文件哈希的第三方 wheel，`docker buildx` 构建 Runtime API 与 Worker 的 arm64 镜像（标签 `gate-7cda4d5-arm64`，OCI 版本 0.5.0、修订 `7cda4d5`）。没有用 `prepare_images.py`，因为它会把镜像打成 `v0.5.0-arm64`、覆盖本机的发布镜像；也没有打离线包、没有上传或发版。本分支的 `deploy/offline-release/` 与 v0.5.0 逐字节相同，三个基础镜像的 digest 也相同。步骤与 v0.5.0 的「从 v0.4.0 升级」一致：
1. v0.5.0 的部署文件与镜像，空卷跑 `start-offline.sh`，健康；迁移 32 个、到 0038。
2. 在 v0.5.0 上用发布镜像的 `bootstrap.seed_scope` 播种 scope、上传一份证据（返回 `version_id`）；另用 v0.5.0 自带的 world-lab 脚本装 world 0.1、签凭证，冒烟 10 项全过（CEO 建 Company），库里有 world 事件。
3. 换本分支的镜像与部署文件，沿用同一 env、同一密钥与数据卷重跑 `start-offline.sh`：健康，只应用了 `0039_world_v02.sql`，迁移 32 → 33。
4. 升级后 `db-admin verify`：80 表、51 张 gov 表 FORCE RLS、应用角色无特权。
5. API、Worker 在新镜像上 running 且 healthy，PostgreSQL、MinIO 仍是 v0.5.0 的基础镜像。
6. 升级前的对象、证据原始字节（逐字节相同）与回执（`committed`）用升级前签发的凭证仍可读。
另外三项：world 0.1 的 Company 与事件用升级前的凭证可读，事件行都读作 `tkos.world/0.1`；用升级前的凭证再跑一遍冒烟，9 项全过（Company 已存在，建对象那一步按脚本改为取已有的）；新镜像里 `/opt/tkos/docs` 的 0.2 契约、登记、profile 与支持登记的 SHA256 与本页钉定的一致。第 6 项第一次核对失败，是测试脚本把回执响应（`{receipt, effects}`）当成了回执本身，改正后重跑通过，不是产品问题。临时栈、数据卷与目录已清理。只在 arm64 上做了，amd64 没做。

**A1、A2、A3 没有完整重跑的原因（读代码判断，未实跑）。**
- A1：完整 runner 要 2026-09-10 那一轮的输入：导出的旧源码（`/tmp/tkos-a1-legacy-3cd9109d` 已不在）、迁移到 0017 的旧历史库与 preupgrade 证据、worker-r2 切换报告、带 P1 的最终 wheel、仓外的契约包文件；README 说明它由 root 按轮次执行、是那一轮的执行记录。只跑了不访问数据库的三项。
- A2、A3：建库工具 `composition_a2_independent/database.py`、`execution_a3_independent/database.py` 把端口写死为 54350，现在那是 Clark 联动栈，按规定不连；两个跑器只接受 `tkos_a1_a2_*`、`tkos_a1_a2_a3_*` 库，method_v05 建的库用不了。即使建得出库，`composition_a2_independent/storage.py` 的 `catalog` 要求应用角色恰好只在 2026-09-10 那份表清单上有 UPDATE，而现在的发布授权还给 Method 等后来的表 UPDATE，会失败。这两套矩阵实际固定在各自 9/10、9/11 的运行上，要在 HEAD 上重跑，得先把建库改成 `method_v05` 那样按隔离栈端口核对、并更新表清单，这是另一张票的事。
- `method_independent`：README 写明固定在 2026-09-11 那次运行、HEAD 上不能原样复跑，照它跳过。

**没有验证的边界。** 以上都只在本机隔离验收栈与一次性 compose 栈上跑，没有在 world-02、world-lab 或生产上跑；合入 main、发版 v0.6.0 与 10/8 world-02 重建都还没做。0.1 两次都是开发运行（没钉提交），通过的是它的 248 项矩阵与四个门槛，不是一次新的 0.1 冻结验收。并发写、进程重启、故障注入、真实模型与看板页面见 0.2 报告的「未验证」，本次没有另外驱动。
