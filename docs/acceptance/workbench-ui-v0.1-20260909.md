# 四页工作台 v0.1 独立验收记录

日期：2026-09-09。结论：本地合成演练范围通过，四页可供用户验证。

## 交付物与基线

- Runtime 基线：`a83ec4f8fe3dfc116fc86513fa90129905fd5a66`。
- 页面源码：`workbench/`；本地安装：`deploy/workbench-local/`。
- 页面：<http://127.0.0.1:8032/workbench/#/instance>；API：`127.0.0.1:8031`。
- 主体实现及首轮修订：Kimi Code 0.40.1；独立审查、浏览器验收、部署及最终修正：Codex。
- 独立验收完成时尚未提交推送；具体工作区文件 SHA256 记录在下述 `final-audit.json`。

## 已核对的行为

1. 对象目录来自真实 10 种类型；实例、业务域、关系、revision、回执消费 Runtime 游标。
   强制缩小真实请求页长，验证证据列表、关系、回执、多 revision 翻页及精确版本选择。
2. 交付轨迹为承接 → v1 → 退回补充 → v2 → 有权人验收；切换 v1/v2 后，
   评审结论和证据引用对应各自版本，能读取原始证据字节。
3. 三项状态独立：交付 `delivery_accepted`，Outcome `not_assessed`，MF `investigating`。
   Outcome 不使用对象的 `confirmed` 生命周期推断达成。
4. 延迟真实响应验证类型筛选、证据、回执详情的乱序隔离；离页请求取消后返回仍可正常加载。
   网络失败后重试成功。切换身份清除先前对象和快照，不遗留旧身份的事件处理器。
5. Context 仅点击按钮才 POST；时间/对象条件变化仅提示差异。以历史 known_at
   `2026-09-09T08:46:47.200Z` 回看为 v1、changes_requested；当前时点为 v2、accepted。
   CEO 和 verifier 可生成/回读，无权身份回读为 404。毫秒输入与服务端微秒不混同。
6. 四页在桌面与 390px 宽度实际渲染，无水平溢出。原型配色、导航、状态带和交付轨迹保留。

## 验证结果与证据

证据目录：`artifacts/runtime-acceptance/workbench-ui-20260909/`（本地忽略目录）。

| 验证 | 结果 | 文件 |
| --- | --- | --- |
| 前端自动回归 | 51/51 | node-tests.log |
| GET 联调 | 34/34 | node-get-integration.log |
| HTTP 权限与代理 | 52 次读取、12 次业务写阻断、3 次 Origin 阻断等通过 | http-checks.json |
| 页面竞态、分页、闭环 | 通过 | catalog-browser.json、instance-browser.json、relations-browser.json、receipts-browser.json |
| Context 与身份切换 | 通过 | context-browser.json、context-roles-browser.json、final-boundaries-browser.json |
| 时间版本与导航 | 通过 | revision-navigation-browser.json、historical-context.json、current-context.json |
| 窄屏四页 | 通过 | mobile-browser.json |
| 最终部署与保留检查 | 通过 | final-audit.json |

最终对比确认：18 个公开部署文件与源码逐字节一致；Docker `desktop-linux` 的 14 个
既有容器 ID、镜像、启动时间保持不变且仍运行；24 个既有工作区文件未被覆盖。
17 张范围内受检表仅审计快照增加 5 条，其他受检表摘要不变。
凭据仅由私有代理持有，不进入浏览器静态文件或验收报告。

## 范围边界

验收数据为本地隔离合成案例。合成角色切换器不是正式登录。
工作台展示业务事实，唯一写入为审计快照；DRI 正式业务动作继续由 Clark 承载。
未做生产切换、真实企业系统接入、规模容量、全套无障碍或实时撤权刷新验收。
