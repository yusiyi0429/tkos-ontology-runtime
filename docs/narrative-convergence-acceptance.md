# Runtime 叙述与记忆收敛验收记录

本次在 `codex/runtime-memory-convergence` 完成 Clark 叙述兼容入口、治理事实读取和历史数据库迁移工具。代码验收基于 `d2d4b07` 加本轮工作区变更；报告中的 Git 元数据记录提交前的验收时点，当前提交与分支以 Git 为准。生产 Memory、AW、Clark 的配置与流量保持原状。

## 1. 叙述与治理事实

新增 `POST /v1/context-graph/narrative`，保留 Clark 原 NarrativeClient 消费的字段，追加版本、权限和来源投影。默认关闭；开启后仍须有效 Runtime 身份和域授权。历史语义背景需要独立 `read_legacy_context` 授权，不用共用 CEO 或模型 key 代替业务权限。

交付通过、Outcome 达成、MF 关闭分别读取。历史查询按有效时间与知晓时间选择治理事实；草案不替代已生效版本，读取不生成业务动作、回执或快照。模型可选，只压缩旧背景；根愿景和治理事实由确定性逻辑保留。完整协议见 [接口契约](narrative-convergence-contract.md)。

| 验证 | 结果与证据 |
| --- | --- |
| 完整 Runtime 独立验收 | `runtime-d9f7cc6f1c26`：20 组通过，218 项 pytest 通过、1 项跳过；包含 DRI v1 退回/v2 验收、Outcome/MF 独立判断、权限、幂等、Worker、持久化重启、独立恢复、迁移重放及 sdist/wheel 构建。 |
| 最终代码叙述专项 | `narrative-90de6049a8ea`：7 组通过；42 次 Narrative 请求均核对 scope 内相关表内容不变；6 个历史节点分别验证两个时间轴。 |
| Clark 原客户端 | 专项中直接执行原 `NarrativeClient` 与 undici HTTP，3 次解析通过；只收窄真实错误类的模块加载路径，无替代客户端或传输。 |
| 最终合并库真实读取 | `convergence-d31068bce435`：从完整两源合并库 32 个可用 confirmed 叶节点选取 10 题，10/10 旧检索核心和新 HTTP 的 Pack、来源、根愿景、原始背景一致；Clark 原客户端 20 次解析通过；33 张相关表内容不变。先前 `convergence-74faddfde9b8` 的 Memory 基底对照证据也保留。 |
| 真实模型连通性 | 同事提供的配置完成一次无企业数据的通用请求，响应非空；不代表公司叙述质量或真实 embedding 召回已验收。见 [连通性结果](acceptance/narrative-model-connectivity.json)。 |
| Linux/amd64 镜像 | API 与 Worker 镜像构建成功；容器模块 hash 与当前源码一致，UID 10001；API 实际连接本地 PostgreSQL，健康、响应 schema、授权 Narrative、无凭据拒绝和正文 hash 均通过。见 [镜像验证](acceptance/narrative-linux-amd64-image-smoke.json)。这是本机容器验证，未部署到远程目标。 |

完整验收记录位于本地 `artifacts/runtime-acceptance/`，该目录由 Git 忽略，不随源码推送；远程仓库保留本报告、脱敏迁移结果与可复跑脚本。218 项测试中唯一跳过的是旧 `test_viewer_rejects_non_human`，原因是独立测试库没有该测试所需的 `agent_service` 用户；新增叙述专项另行验证了身份与授权边界。

真实数据对照使用数据库内已有向量进行固定 HTTP 回放，以隔离模型波动。它证明恢复后的数据读取和客户端兼容性，不证明生产 embedding 配置、召回质量或压缩质量。专项测试使用隔离身份，不能替代真实员工 SSO 和任职映射验收。

## 2. 两源数据保全

已对两套生产数据库分别执行只读备份，原始 dump 保存在本地权限受控且 Git 忽略的目录，未输出企业正文或凭据。各源 53 张历史表（含非 public schema）恢复后行数与内容 hash 均一致。

独立复查发现 Memory 和 AW 是不同方向的超集：Memory 多 Working Memory 业务链；AW 多 `conversations` 104 行、`runs` 104 行、`run_events` 904 行、`evals_sync_state` 16 行。共享主键内容一致。补齐全表门槛后的 `convergence-e57e466e4099` 正确拒绝直接选源，并在迁移前停止；该失败证据保留。

最终合并演练 `convergence-d31068bce435` **通过**：从原始两份 dump 再恢复两个全新本地库，按固定 manifest 保留 Memory 的业务链，仅补入 AW 独有的 1,128 行运行历史。插入后、原插入计划重放后及 0015/0016/0017 升级后，53 张历史表均与两源完整并集精确匹配；重放新增 0 行。158 条外键全部 validated、孤儿 0，public owner 正确，应用角色对 27 张 legacy public 表仅有 SELECT 权限。

共享记录内容冲突、未知表和未验证外键均经过真实数据库负向注入，正确拒绝且回滚。迁移工具 20 项单元测试通过，独立审查又用另一套只读 SQL 重算全部表，确认精确并集、AW 记录保全、外键与应用权限。

迁移演练结束时新 gov 的 17 张表全部为空。其后专门为真实叙述对照新增了 6 类身份/权限记录，各 1 行；全部业务表仍为 0。该 AGENT 任职有效期严格为 86,400 秒，策略仅允许 `read` 和 `read_legacy_context`。重复建立身份被拒绝，凭据不覆盖；reader 工具的 36 项边界测试通过，并拒绝旧的单源恢复结果。独立只读 SQL 再次核验身份和权限，见 本地 `artifacts/runtime-acceptance/convergence-d31068bce435/local-reader-proof.json`。比较结束后自有 API/provider 进程均已停止。迁移时与读取验证时分别记录，不篡改原验收状态。

详见 [完整保全报告](../deploy/convergence/rehearsal-d31068bce435.json)、[逐表 manifest](../deploy/convergence/merge-manifest-v1.json) 与 [复跑方法](../deploy/convergence/README.md)。合并只保留旧历史；不把旧 semantic/wm 数据自动提升为新 gov 承诺、交付验收、Outcome 达成或 MF 关闭。

最终真实查询对照：本地 `artifacts/runtime-acceptance/convergence-d31068bce435/legacy-narrative-comparison.json`。源码、契约、工具与证据文件的对应关系见 [本轮交付清单](acceptance/narrative-convergence-manifest.json)。

## 3. 正式切换的边界

当前交付包含可审查代码、接口协议、自动化验收、Linux 镜像和本地迁移工具。数据库 dump 不等于外部原件/证据对象备份，真实来源文件字节、生产角色与服务配置备份仍须核验。生产写入入口、增量边界、正式读取身份及员工任职映射也须明确。

远程目标需要独立数据库/存储和验收，先做只读对照，再确定切写时点。切写前重新捕获两源增量并执行同样保全门槛；出现新表、内容冲突或不符合 manifest 的差异立即停止。新 Runtime 开始写入后，回滚必须保留新增事实、回执与外部 effect 状态，不能仅改回 URL。

具体执行顺序、保留旧服务及回滚要求见 [迁移与切换计划](../deploy/convergence/migration-plan.md)。本报告不构成生产切换记录。
