# 本机 Runtime 治理工作台验收（2026-09-17）

范围：独立分支 `codex/runtime-governance-workbench`，基于 `acea4d4` 的未提交源码；Method `tkos.method/0.3`。只修改 Runtime，未修改 Clark、未合并 main、未发布或部署远程服务。

## 已通过

| 验收 | 结果及证据范围 |
|---|---|
| Python | 145 项通过：个人会话、同源/CSRF、登录限流、账号重绑/重置、过期、权限、原信封日志及现有看板、M1B/0.3 模型 |
| 前端 | 15 个测试文件、98 项通过；类型检查、生产构建与资源清单验证通过 |
| 工作台真实 HTTP/数据库 | 23 项通过：三个独立会话、跨身份日志隐藏、场景及字段锚点、评论替代/撤回历史、CAS、关窗竞争、完整候选、本人核对不生效、DRI 不能确认、CEO 整组确认、SQL 执行者与 handoff |
| 既有 Anchor/Method 独立验收 | 35 项通过：真实 HTTP/PG/MinIO；撤权、过期基准、并发、事务原子性、重启恢复、旧版共存、无隐式执行或交付验收 |
| 真实浏览器 | 三个独立 profile；CEO 建立核对场景，DRI A 评论并替代，DRI B 评论并撤回，受控 Co-agent 关窗/收拢，DRI A 核对候选，CEO 整组确认，DRI 读取正式 Mission |
| 重启 | API 重启使旧会话失效；重新登录可读取原意见、候选及提交记录；重建镜像也可读取正式 Mission 和持久日志 |
| 构建产物 | `uv build` 通过；由本轮 wheel 重建 `tkos-runtime:governance-local`，健康检查、个人登录、Mission、提交查询和静态资源通过 |

浏览器在 390px 宽度无横向溢出。以下截图使用隔离合成数据：

- [正式 Mission](assets/governance/formal-missions.png)
- [窄屏 Mission](assets/governance/mobile-missions.png)

构建有现有前端 chunk 超过 500kB 的非阻断提示。

## 恢复与证据说明

工作台 HTTP 的“响应丢失”用丢弃客户端结果后重放原信封验证；单元测试验证未知提交状态及原信封重试。没有把此项描述为真实断网故障注入。Anchor 独立验收另覆盖重启与事务故障。

原始身份、登录码、数据库配置、Context、提交信封和 HTTP 记录保存在忽略目录 `.runtime-acceptance/governance/`，不进入报告。`image-check.json` 记录实际镜像 ID、架构与本轮 wheel SHA256。镜像标签不是正式 Release；不能仅凭基线 commit 推断未提交改动已发布。

## 未宣称完成

- **真实模型：未运行。** Co-agent 使用受控输出，并保存授权 Context 和生成参数；实际新增判断标准与意见取舍对应。
- **Clark 接线、Clark 浏览器闭环：未验证。** 此处浏览器验收对象是 Runtime 工作台。
- **远程部署、正式企业身份、多实例会话、M2 执行：未包含。** 本机个人测试登录码不作为企业 SSO。
- 本轮浏览器验证成功确认路径；重开和过期依据通过 Runtime 独立验收，不等同于已完成每条浏览器负向路径。

运行步骤及私有文件边界见 [复跑入口](../../acceptance/governance_workbench/README.md)。

## 视觉升级补充验收（2026-09-17）

统一侧栏、身份栏与页标题，重做登录、待办、空状态和 Mission 卡片；业务流程提示仅描述既有 M1B 阶段，不增加业务决定或统计。嵌入本体地图、关系图时隐藏重复导航，原独立只读入口保持不变。Canva 当前工具不支持交互网页输出，因此实际改动通过现有 React/shadcn 实现，没有生成 Canva 设计稿。

本轮重新通过类型检查、98 项前端测试、资源清单、wheel 和当前源码镜像构建；浏览器验证个人登录、正式 Mission、侧栏跳转、本体地图单一导航、业务关系图，以及 390px 窄屏无水平溢出。未改变 Runtime 授权与动作接口。

- [新版登录](assets/governance/redesign-login.png)
- [新版待办](assets/governance/redesign-tasks.png)
- [新版 Mission](assets/governance/redesign-missions.png)
- [新版本体地图](assets/governance/redesign-map.png)
- [新版窄屏](assets/governance/redesign-mobile.png)

本机入口保持 `http://127.0.0.1:58804/dashboard/`，容器 `tkos-governance-local` 已替换为本轮重新构建的镜像。个人登录码保持不变；重启后需重新登录。未推送或更新远程服务。
