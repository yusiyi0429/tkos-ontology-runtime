# Runtime Anchor 0.3 本地验收报告

日期：2026-09-15。工作区：`codex/clark-method-workspace-runtime`，基线 `1fb3226`，复用原有未发布 0.2 与场景增量。本轮没有修改 Clark、主工作区或既有容器。

## 验证结果

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| 0.3 独立 HTTP / PostgreSQL / MinIO 增量 | 35/35 | Architecture、State、Problem、Agent 立项及原子移交、旧战略显式重开、M1B、权限与恢复 |
| 0.2 独立回归 | 22/22 | 原生命周期链、关窗竞争、候选原子性、旧版共存与恢复 |
| Python 回归 | 699 passed，1 skipped | tests 排除 migrations 与 narrative_legacy；含新增 14 项 0.3 测试 |
| legacy 集成单独执行 | 14 passed | 隔离数据库，初始化与请求角色分离 |
| Workbench Node 回归 | 61 passed | 原有 UI 模块；不是 Clark 浏览器验收 |
| 新库迁移与重复迁移 | 通过 | 独立库升级至 0025，重复执行无新增迁移；提交前补齐清单，空库 migration pytest 1 passed |
| wheel / sdist | 通过 | 包版本仍为 0.2.1，未发布新 Release |
| OpenAPI / 差异格式 | 已核对 | 从实际 app 导出；暂存检查仅有 0024/0025 已应用迁移的末尾空行提示，为保持迁移字节不变予以保留 |

Python 跳过项是既有缺失 agent_service fixture 的测试；不是将失败转为跳过。0.2 独立回归在最终仅影响 0.3 的显式重开修补之前执行；最终 0.3 验收及 Python 回归覆盖最终源码。

## 可追溯证据

[脱敏检查清单](acceptance/anchors-v03-summary.json) 与 [复跑入口](../acceptance/anchors_v03/README.md)。私有结果位于 `.runtime-acceptance/anchors-v03-9/`；原请求、身份、Context、HTTP 日志不发布。验收在运行前后检查 src 未变化。报告时按排序路径与文件字节累计 SHA256（src 中 Python 与迁移 SQL）：`f097e921f5c20d26a4fd6237f26e2cd5ce6de3e6bfdde439c3bccf45a461b591`。

架构配对更新和 Problem 移交分别注入事务中断，独立数据库快照核对未残留部分写入。还验证：非 DRI Mission Owner 正确确认；唯一状态键；推荐与人工修正历史；双击并发同键；进程重启与原信封恢复；撤权后的迟到提交和重放拒绝；同一议题汇集两来源；失败保留原 Problem 待办，成功退出原待办。SQL 核对未新增执行授权或交付验收。

## 交付状态

- Runtime 0.3 接口增量：本地隔离验收通过。
- 伙伴 Clark 接线：未验证。
- Clark 真实浏览器闭环：未验证。
- 真实模型收拢：未运行；本轮使用受控输入，不能替代真实模型质量验收。
- 此报告记录合并前验收；发布、容器部署、企业 SSO、生产迁移未执行。

本次 API 来自当前源码子进程，数据库与证据存储使用本地隔离数据；未用旧镜像 API 代替新源码验收，也未替换正在运行的 API/Worker。若伙伴启动容器联调，需要从此源码重新构建并补记镜像摘要。历史对象自动接续及 M2/M3 不在本轮范围。
