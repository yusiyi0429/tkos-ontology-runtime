# tkos.world/0.2 接口变化清单（给天枢）｜2026-09-29

对象：天枢服务端接入本体的工程师。用途：10 月 12 日至 16 日的试用走 0.2，天枢在此之前把接入从 0.1 改到 0.2。本清单只写相对 0.1 的变化；0.1 的调用约定见《Agent 接入对接说明》（rev 31），没提到的沿用。

依据：0.2 契约草案 `docs/contracts/tkos-world-0.2.md` 与登记 `docs/contracts/world-registry-0.2.json`（分支 `world/0.2`）。下文「第 N 节」指该契约的章节。

**状态说明**：0.2 还是草案，正在实现。实验实例 9 月 29 日上线，同日按实现完成的版本重建；最近一次重建在 9 月 29 日晚，版本为 `24e6202`，含议题代记族，并按天枢个人任务重播了十月起点。本清单写到的动作与读取都已可调，实测示例见 `docs/world-v02-tianshu-examples.md`。本清单里的动作名、字段名与规则按契约写，是天枢可以开始改的依据；请求与返回的完整 JSON 以交付时附的实测示例为准。标「可能变」的在锁版前可能再改，改了会单独通知。示例里的 id 都是占位。

## 一、环境与版本

| 项 | 0.1 | 0.2 |
|-|-|-|
| 实例 | `https://world-lab.tokenkingos.com` | `https://world-02.tokenkingos.com`（9 月 29 日已上线）；world-lab 保持 0.1 不动 |
| scope、对象 id、域 id、principal 表 | world-lab 那一套 | 新实例上重新播种，清单随实例一起给；0.1 的 id 不能带过来 |
| 凭证 | 天枢服务主体一枚 | 新实例另发一枚，同样在公司域与各责任单元域持 AGENT；另用于代记（第八项） |
| 请求信封 | `contract_version: "tkos.world/0.1"` | `contract_version: "tkos.world/0.2"` |
| 写入流程 | prepare 再 commit、幂等键、回执 | 不变 |

- 实验实例可以清空重建：锁版前契约改动时会重建库，对象 id 会变。天枢的同步表请能按实例整批作废重来。
- 0.1 的数据不迁到 0.2，两边不建桥。

## 二、块值：多一个组件列表

0.1 的块只有 `{text, refs, artifacts}`。0.2 是 `{text, components, refs, artifacts}`（第 4 节）。

- 组件是块里一条可以单独引用的内容：`{id, type, scope?, text?, refs?, artifacts?, attributes?}`。
- `id`：天枢可以自己给，字符集 `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`，在所属对象内唯一；不给由服务生成。**建议直接用天枢的 todo id、issue id**（加前缀也行，例如 `todo:123`），这样跨周对得上。
- `type`：必须是该块允许的组件类型，天枢会用到的三种见下表。
- 删除组件留痕，删掉的 id 不能再用；组件不能从一个块挪到另一个块。
- 修订对象时块按字段合并：给出的 `text`、`refs`、`artifacts` 整体替换；`components` 按 id 合并，给出的 id 新增或改写，`{"id": "...", "removed": true}` 删除，没提到的保留（第 12 节）。0.1 是整块替换。

| 组件类型 | 放在哪 | 类型属性 |
|-|-|-|
| `progress_item` 进展条目 | 执行状态快照的 `progress` 块 | `principal_id`、`principal_name`（写入时的姓名）、`external_status`（天枢状态原样）、`entries`（本期条目列表，每条 `{at, source, text, url?}`，`source` 取 `web`、`codex`、`claude`、`github`、`other`）。一条进展有多个链接时，主链接放条目的 `url`，其余放进同一个组件的 `artifacts`，不要拆成多条条目（2026-09-29 与天枢约定） |
| `issue` 问题 | 各类快照的 `issues` 块 | `core_question`（核心判断问题，必填）、`responsible_hint`（最低充分责任主体，可选，本体 principal id） |
| `plan_item` 计划条目 | Mission 的执行计划块、Task 的计划块 | `responsible`（只作记录，不是指派、不产生权限） |

进展条目的字段来自对接说明 11.2 的草案，三方会上定稿；天枢状态与紧急度的枚举会上对一下（可能变）。

## 三、引用：四种形式

| 形式 | 写法 | 0.1 有没有 |
|-|-|-|
| 对象 | `<对象 id>@<版本>` | 有 |
| 块 | `<对象 id>@<版本>#<块 id>` | 有 |
| 组件 | `<对象 id>@<版本>#<块 id>/<组件 id>` | 新增 |
| 事件 | `event:<事件 id>` | 新增 |

- 写入一律用字符串；读回同时给字符串与钉定后的结构。引用钉在版本上，对象出新版本后不漂移；组件 id 稳定，在新版本里用同一个 id 找到同一条。
- 快照的 `subject_ref` 与写入声明的 `scene` 用对象形式；事件的 `subject_refs` 可以用对象形式或组件形式。

## 四、状态快照：统一外壳 + payload 类型 + 来源事件

0.1 的快照块固定是 `progress`、`issue`、`artifacts`。0.2 是统一外壳加按主体类型的 payload（第 7 节）。

外壳：

| 字段 | 说明 | 相对 0.1 |
|-|-|-|
| `subject_ref` | 主体，对象形式；可以是任一业务对象（Mission、责任单元、周期目标……） | 0.1 天枢只写 Mission；现在可以写单元 |
| `as_of` | 时点，带时区；不晚于写入时刻；同一主体同一时点只有一条 | 不变 |
| `period` | 可选，`YYYY-MM` | 不变 |
| `source_event_refs` | 来源事件，事件引用列表，**至少一条** | 新增，必填 |
| `payload_type` | 必须是主体类型登记的那一种，见下表 | 新增，必填 |
| `title` | 标题 | 不变 |
| `generator` | 生成者，**服务端按凭证填，请求里不要给** | 新增，只读 |

| `payload_type` | 主体 | 块 |
|-|-|-|
| `execution_state` | Mission、Task、Activity | `progress`（进展条目）、`blockers`、`issues`（问题组件）、`materials` |
| `goal_state` | 长期目标、周期目标 | `progress`、`issues`、`materials` |
| `unit_state` | 责任单元 | `progress`、`issues`、`materials` |
| `strategy_state` | Strategy | `issues`、`materials` |
| `company_review` | Company | `results`、`gaps`、`causes`、`key_changes`、`implications`、`materials` |

**天枢每周进展快照的改法**：

1. 先记一条外部事件作来源，例如 `category: "other"`，内容「天枢每周同步 <ISO 周>」，主体是该 Mission，幂等键建议 `tianshu:weekly-sync:<missionId>:<ISO周>`。
2. 再写快照（块放在 `payload.blocks.<块 id>` 下，同建对象与修订；外部事件的内容放在 `content`；块放在 payload 顶层返回 422）：`payload_type: "execution_state"`，`source_event_refs` 放第 1 步返回的 `event:<事件 id>`；进展按人 × 事项写成 `progress` 块里的 `progress_item` 组件，组件 id 用天枢 todo id；议题写成 `issues` 块里的 `issue` 组件，组件 id 用天枢 issue id；链接放 `materials`（0.1 的 `artifacts` 块改名为 `materials`，块内仍有 `artifacts` 链接列表）。
3. `as_of`、幂等键、声明沿用 0.1 的约定（周日 23:59:59+08:00，`tianshu:weekly:<missionId>:<ISO周>`，`human_acceptance.required=false`）。

0.1 里按模板渲染成 Markdown 的做法在 0.2 不再需要：每条事项、每个议题是一个组件，字段在 `attributes` 里；`text` 可以留一句说明。

## 五、写入声明：场景放宽

`scene` 可以是任一业务对象的对象形式引用（0.1 只认 Mission 或 Task），所以写单元、周期目标的快照时，场景写该对象本身即可（第 9.3 节）。其余两项（`trigger`、`human_acceptance`）不变；代记写入不带声明（见第八项）。

## 六、生命周期：显式事件取代外部事件推导

| 0.1 | 0.2 |
|-|-|
| 此后第一条状态快照推出「进行中」 | 由开始事件 `world_start` 推出；快照不再推导生命周期 |
| 交付类、验收类外部事件推出已交付、已关闭 | 由 `world_deliver`、`world_accept` 推出；交付类、验收类外部事件只作记录 |
| 没有退回、重开、取消 | `world_reject`（进入调整中）、`world_reopen`、`world_cancel` |
| Mission 立项与交付各一道门 | 门只在立项；交付改由生命周期事件表达 |

- 六个生命周期动作是通用动作，目标是对象；谁能记由状态表判定（第 10 节）：Task 的开始、交付由 Task 责任人记，验收通过、退回、重开、取消由 Mission Owner 记；Mission 的交付由 Owner 记，验收通过、退回、重开、取消由 DRI 记。
- **天枢「执行事项完成」要记成对应 Task 的 `world_deliver`**，记录者是执行人；天枢以代记的方式替执行人记（第八项）。交付要求 Task 处于进行中或调整中，所以之前要有指派（Mission Owner 记）与开始（执行人记）；只记一条交付类外部事件不会改变 Task 的状态。
- 与当前状态不匹配的记录返回 `INVALID_STATE`，所以重复推送同一事件不会产生第二次状态变化；同键重放照旧返回原回执。
- 撤回：记 `outcome: "withdrawn"` 并以 `supersedes_event_id` 指向原事件，只能撤回推出当前状态的那条（第 11 节）。

状态名与落地计划 rev 48 的口径一致：待验收即已交付，已完成即已关闭，退回进入调整中，未开始对应未指派与已指派。

## 七、Issue：问题组件与事件

议题在 0.2 是主受影响对象快照里的 `issue` 组件（第 13 节；CEO 若改为对象会另行通知，可能变）。

- 身份是（主受影响对象，组件 id）。同一对象同一核心问题沿用原 id，后续快照带同一个 id 表示新情况；引用任一条带这个 id 的快照，指的都是同一个问题。
- 流转各记一条事件。五个动作都**不带 `target`**，以 `params.issue_ref` 指明问题，写法是问题组件的组件引用 `<快照 id>@<版本>#issues/<组件 id>`：

| 动作 | 另外的参数 | 谁能记 | 状态 |
|-|-|-|-|
| 提出 `world_raise_issue` | 可选 `content`；Agent 必带写入声明 | 在主受影响对象所在域持 AGENT 的 Agent（天枢服务主体即是），或主受影响对象及其主干以上的责任人 | 未提出、形成中 → 待路由 |
| 路由 `world_route_issue` | `to_principal_id`（承接人：scope 内有效的人，不限单元），其余同提出 | 同提出 | 待路由 → 已路由；已路由可改路由 |
| 承接 `world_own_issue` | 可选 `content`，不带写入声明 | 当前路由指定的承接人本人 | 已路由 → 已承接 |
| 处置 `world_dispose_issue` | `disposition`（六类之一），`content.text` 写理由（必填） | 已承接的承接人本人 | 四类进已处置；`route_escalate` 回待路由；`pushback` 进形成中 |
| 退回形成 `world_return_issue` | 同提出 | 路由者或本轮承接人 | 已路由、已承接 → 形成中 |

- 回执 `result` 带 `event_id`、`subject_refs`（第一条是问题组件，第二条是主受影响对象）与 `issue`：`{primary_affected_object_id, component_id, status, display_name}`。Issue 动作不出修订，不改变业务对象的生命周期。
- 错误：`issue_ref` 所在对象不在本 scope 是 404；提出、路由时在主受影响对象所在域没有角色，承接、处置、退回形成时在 scope 内没有生效指派，Agent 承接或处置，记录者不符，都是 403；正在处理或已处置的问题再提出、状态不允许是 409；参数不对（含带 `target`、缺理由、处置不在六类、Agent 缺声明、`issue_ref` 不是快照 issues 块里的问题组件、承接人不是 scope 内有效的人）是 422。
- **承接人不限单元**（2026-09-29 定，契约补 44）：承接人是 scope 内有效的人（启用的人，持任一生效指派），不必在主受影响对象所在的域持角色，转给别的单元的人可以；承接、处置与退回形成按 scope 判权。提出与路由仍在主受影响对象所在的域判。
- 取对象的 `records.open_issues` 列出主受影响对象是它、提出过还没处置的问题：`{component_id, issue_ref, text, core_question, responsible_hint, as_of, lifecycle, route_target, owner}`。
- 天枢服务主体可以以自己的身份提出、路由、退回形成；承接与处置由承接人本人记，或由天枢按他登记的议题族委托代记（2026-09-29 定，见第八项）。
- 正在处理的问题不能重复提出；已处置的不再提出，复发用新 id 并在内容里引用原问题。处置为「带入下次形成」「立即重开」的问题，在下一次相关的形成时由取上下文带出（第十项）。

## 八、代记：人在天枢页面确认，直接进本体

0.1 的门与指派只能由人持自己的凭证记。0.2 开放代记（第 14 节）：

1. **登记委托**：人（CEO、DRI、Owner、执行人）本人记 `world_grant_delegation`，参数 `delegate_principal_id`（天枢服务主体）、`families`（`gate` 门、`assign` 指派、`lifecycle` 生命周期、`issue` 议题，可多选）、`domain_ids`（范围内的域）、`valid_until`（必填）。本人可随时 `world_revoke_delegation` 撤销，参数 `delegation_event_id`（登记那条事件的 id，登记回执里有），即时生效。委托不能转委托。**每个人登记一次委托这一步要本人做**：登记与撤销只能由本人持自己的凭证经 HTTP 记，天枢不能代记；E&O 会在实例上线时协助（方式另行说明）。
2. **代记写入**：天枢以自己的凭证调用可代记的动作，另带
   ```json
   "on_behalf_of": {
     "principal_id": "<被代记的人>",
     "external_record_id": "<天枢里这次确认的记录 id>",
     "external_confirmed_at": "2026-10-12T15:30:00+08:00"
   }
   ```
   `external_confirmed_at` 不晚于写入时刻。代记写入不带写入声明。带了写入声明、`external_confirmed_at` 晚于写入时刻、给不可代记的动作带 `on_behalf_of`，都返回 `INVALID_REQUEST`（422）。
3. **判权**：按被代记的人判，规则和他本人记时一样；同时要求委托有效、动作所属的族与目标所在域在委托范围内。任一不满足返回 `FORBIDDEN`。
4. **效果**：事件与回执同时记下记录者（天枢服务主体）与被代记的人，回执的 `on_behalf_of` 另带所用委托的 `delegation_event_id`；生命周期与决定权按被代记的人算；事件的发生时刻是写入时刻，外部确认时刻另存。被代记的人可以本人撤回。
5. 代记只走 HTTP。代记不含建对象（待 CEO 定，可能变）。

对照映射表（对接说明 10.1），0.2 下可以代记的：

| 天枢里的人工确认 | 本体动作 | 族 |
|-|-|-|
| 月度计划签发（DRI 提交、CEO 签） | `world_commit_period_goal`（DRI）、`world_confirm_period_goal`（CEO） | 门 |
| 任务卡确认（Owner 提交、DRI 确认） | `world_commit_mission`、`world_confirm_mission` | 门 |
| 指定 Mission 负责人、执行人 | `world_assign` | 指派 |
| 执行事项开始、完成、验收、打回 | `world_start`、`world_deliver`、`world_accept`、`world_reject` | 生命周期 |
| 会后 CEO 确认的战略类条目 | Strategy：CEO 指定本轮 `world_assign_strategy_round`、被指定的人各记 Agreement `world_agree_strategy`、CEO 确认 `world_confirm_strategy` 或再确认 `world_reconfirm_strategy`；长期目标的确认 `world_confirm_long_term_goal` 与再确认 `world_reconfirm_long_term_goal` | 门 |
| 月度复盘确认、目标取消 | 复盘确认 `world_confirm_review`（目标是快照；确认公司复盘只赋效力，确认周期目标的复盘使周期目标进入已关闭）、周期目标再确认 `world_reconfirm_period_goal`、`world_cancel`（长期目标进入已终止、周期目标进入已取消） | 门、生命周期 |
| CEO 关注某张任务卡 | `world_mark_core_battle`（只影响可见性，不加确认门；CEO 若改方案会另行通知，可能变） | 门 |
| 议题的承接、处置与退回（承接人在天枢里操作） | `world_own_issue`、`world_dispose_issue`、`world_return_issue` | 议题 |

**议题族**（2026-09-29 定，契约补 49）：Issue 的承接 `world_own_issue`、处置 `world_dispose_issue` 与退回形成 `world_return_issue` 可以代记，动作族是 `issue`（议题），登记委托时在 `families` 里选上它。提出与路由不在这一族，天枢服务主体照旧以自己的身份记（第七项）。

- 判权按被代记的人，规则同他本人记：承接是当前路由指定的承接人，处置是已承接的承接人，退回形成是路由者或承接人；这三个动作按 scope 判，承接人在别的单元也可以。
- 委托的 `domain_ids` 要覆盖问题所在的域，也就是主受影响对象所在的域（Issue 动作不带 `target`，以 `issue_ref` 所在快照的域为准），不是承接人自己的单元：代一位 DRI 承接别的单元的问题，他的委托要含那个单元的域。
- 委托里没有 `issue` 族、域不覆盖、被代记的人不是承接人（或路由者），都是 `403 FORBIDDEN`；天枢服务主体不带 `on_behalf_of`、以自己的身份承接或处置，仍是 403。代记的退回形成不带写入声明。
- 回执 `result` 在第七项的字段之外另带 `on_behalf_of`（含所用委托的 `delegation_event_id`）；事件与读投影 `records.open_issues` 的变化同本人记。

代记处置的请求示例（id 都是占位）。先发 `POST /v1/actions/prepare`，把返回的 `expected_versions` 原样带进 `POST /v1/actions`；Issue 动作的 `target` 为 null：

```json
{
  "action_type": "world_dispose_issue",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "tianshu:issue-dispose:<天枢里这次处置的记录 id>",
  "reason": "天枢：承接人处置议题",
  "params": {
    "issue_ref": "<快照 id>@<版本>#issues/<组件 id>",
    "disposition": "current_layer_action",
    "content": {"text": "本层处理：试点推迟一周。"},
    "on_behalf_of": {
      "principal_id": "<承接人的 principal id>",
      "external_record_id": "<天枢里这次处置的记录 id>",
      "external_confirmed_at": "2026-10-13T10:20:00+08:00"
    }
  }
}
```

代记承接、退回形成同样写法：`params` 只带 `issue_ref`、可选 `content` 与 `on_behalf_of`。

## 九、外部引用与查找

0.1 没有外部引用字段，天枢靠同步表对照。0.2（第 3.4、15.2 节）：

- 每个业务对象可以带 `external_refs`：列表，每项 `{system, id, url?}`。
- 写法（2026-09-29 与天枢约定，三方会上若改再跟着改）：`system` 统一用 `tianshu`，类别写进 `id` 前缀。
  - 个人任务对应 Mission：`mission:<编号>`，由天枢以修订写入。
  - 执行事项对应 Task：`todo:<天枢事项 uuid>`，由 E&O 建 Task 时写入。
  - E&O 责任单元：`capability:05`，由 E&O 播种时写好。
  - 战场暂不写：本体里没有和战场对应的对象，同一对引用又只能挂一个对象，三方会上再定。
- 外部引用是活动属性：有门对象有了正式内容后也可以直接修订，不走门。天枢服务主体以 Agent 身份修订时要带写入声明，只改外部引用这类活动属性时 `human_acceptance.required` 可以为 false；能不能改某个对象按修订权限判：天枢能改的是有门对象（Mission、周期目标等）的外部引用；责任单元、Task 这类无门对象只由主干上的责任人改，天枢修订会返回 403（实测示例第十一节）。
- 同一 scope 内同一 `(system, id)` 只能指向一个对象，按各对象的最新修订判定。建对象或修订时撞上另一对象已有的一对，返回 `409 INVALID_STATE`，错误信息里写出那个对象的 id；一个对象的 `external_refs` 里同一对写两次是 `422 INVALID_REQUEST`。要把一对从 A 挪到 B：先修订 A 去掉它，再修订 B 加上。
- 按外部引用查找：见第十项的列对象接口，带 `external_system` 与 `external_id`。

同步表仍建议保留（记幂等键、`context_pack_id`、回执），但不必再靠它找对象。

## 十、读：列对象与分组读投影

**列对象**（新增，第 15.2 节）：`GET /v1/world/objects`，参数都可选、可以组合，都按各对象的最新修订判：

| 参数 | 说明 |
|-|-|
| `unit_id` | 责任单元的对象 id：列它所在的域里的对象（每个责任单元在自己的域，含单元本身）；与 `domain_id` 只给一个 |
| `domain_id` | 域 id |
| `type` | 对象类型：`Company`、`Strategy`、`ResponsibilityUnit`、`LongTermGoal`、`PeriodGoal`、`Mission`、`Task`、`Activity`、`StateSnapshot` |
| `period` | `YYYY-MM`：周期目标按自己的 `period`，Mission 按其周期目标，Task 按其 Mission、Activity 按其 Task 所属 Mission 的周期目标，状态快照按自己的 `period`；其余类型没有周期，给了 `period` 就不列 |
| `external_system`、`external_id` | 按外部引用查找：两者都给时至多一项（`(system, id)` 在 scope 内唯一）；只给 `external_system` 时列带该系统任一外部引用的对象；只给 `external_id` 是 422 |
| `limit` | 每页条数，1 至 100，默认 50 |
| `cursor` | 上一页返回的 `next_cursor`，原样带回；只能用于同一组筛选、同一凭证 |

不认识的参数、重复的参数一律 `422 INVALID_REQUEST`，不静默忽略。按对象的建立时刻与 id 排序。返回：

```json
{
  "items": [
    {
      "object_id": "…", "object_type": "Mission", "type_display_name": "Mission",
      "category": {"id": "business_object", "display_name": "业务对象"},
      "title": "…",
      "version": 3, "revision_id": "…", "object_version": 5,
      "lifecycle": {"status": "in_progress", "display_name": "进行中", "event_id": "…"},
      "domain_id": "…",
      "external_refs": [{"system": "tianshu", "id": "mission:<编号>", "url": null}],
      "contract_version": "tkos.world/0.2"
    }
  ],
  "next_cursor": "…"
}
```

- `version`、`revision_id` 是最新修订（引用写 `<object_id>@<version>`）；`object_version` 是对象行的并发版本，写入时作 `target.expected_version`，同取对象 `business` 组里的那两项。
- `lifecycle` 与取对象的 `records.lifecycle` 相同；没有生命周期的类型（Company、责任单元、状态快照）为 null。
- `next_cursor` 为 null 即最后一页。
- 错误：`unit_id` 与 `domain_id` 同给、`unit_id` 不是责任单元、`period` 或 `type` 取值不对、`limit` 越界、游标不对（换了筛选或凭证）是 `422 INVALID_REQUEST`；`unit_id`、`domain_id` 不在本 scope（含别的 scope 的）是 `404 NOT_FOUND`；凭证在 scope 内没有任何生效角色是 `403 FORBIDDEN`。
- 同一 scope 里若有 0.1 对象（实验实例上没有），也会列出，`contract_version` 为 `tkos.world/0.1`，生命周期按 0.1 的状态机，`external_refs` 为空。

**取对象的返回改为三组**（第 15.1 节）：

- `business`：id、类型、类别、版本与 `revision_id`、属性（含 `external_refs`）、关系引用、块与组件、组件台账、正式内容指针、进行中的一轮。
- `identity`：责任人（注明来自属性还是角色）、与该对象有关的当前有效委托（委托的域覆盖该对象所在的域即算有关）。
- `records`：生命周期与推出它的事件、最新快照（标明未经确认）、最近的已确认复盘、未处置的问题。

写前取 `revision_id` 与 `object_version` 的做法不变，只是它们挪进了 `business` 组：`target` 取 `business.object_id`、`business.revision_id` 与 `business.object_version`（作 `expected_version`）。`business.version` 是修订序号，引用里的版本用它；`object_version` 是并发版本，两者不必相等。

状态快照的读回里没有 `object_version`；以快照为目标的写入（复盘确认 `world_confirm_review`）从列对象的对象头里取 `revision_id` 与 `object_version`。

**读 0.1 对象**（第 15.4 节，实验实例上用不到，写在这里备查）：取对象、取状态对 0.1 对象默认仍给 0.1 的形状；带 `view=tkos.world/0.2` 时按上面的三组给出，内容仍按 0.1 契约解释（块、属性按 0.1 登记，生命周期按 0.1 的状态机），0.1 的引用读成 0.2 的对象或块形式，0.1 快照按只读的 `legacy_0_1` payload 给出（`progress`、`issue`、`artifacts` 三块，生成者是写它的人，没有来源事件）。0.2 对象带不带这个参数都一样；`view` 只认 `tkos.world/0.2`，其余取值 422。

**取事件**：按发生时刻升序；每条带 `class`（门、生命周期、记录）、记录者、被代记的人与外部确认记录、迟记标记（补记过去时刻时）、被更正与被撤回的关系。

**取上下文**（第 15.3 节）：接口与请求不变；默认预算改为 100000 字符（0.1 是 12000），每个对象 10 条事件、近期 30 天不变。0.2 的预算只报成本、不作门（决 18）。变化：

- 引用细到组件；Why 沿单元长期目标的 `goal_ref` 多取一跳到公司级长期目标（`context_pack.layers[i].hop`，只带定义类块），Strategy 与 Company 也进「为什么」。
- 渲染的 Markdown 以六问为节：开头「六问指引」，然后「为什么」「做什么」「谁负责」「现在怎样」「发生了什么」「凭什么」。事件行写成「X 记」或「X 代 Y 记」，指派另写「指派给 Z」。
- **形成时带入**：出发对象是有门的类型（Strategy、长期目标、周期目标、Mission）时，`context_pack.carried` 带出必须看到、不必须采用的内容，Markdown 在六问指引之后单出一节「## 形成时带入」，这一节不被预算裁剪（所以预算很紧时 `over_budget` 会是 true）。
  - 待带入的问题：处置为「带入下次形成」或「立即重开」、此后主受影响对象还没记过门事件的问题；主受影响对象是责任单元的，本单元任一周期目标此后记了门事件就不再带入（2026-09-29 定，契约补 46）。从周期目标出发带本单元（责任单元、本单元的长期目标与周期目标）的，从其他有门对象出发带它本身的。每项 `{issue_ref, primary, text, core_question, disposition, disposed_by, reason}`。
  - 从周期目标出发另带：本 scope 最近的已确认公司复盘 `company_review`（按 `as_of` 取最新，快照外壳加 results、gaps、causes、key_changes、implications 五块，没有时为 null、Markdown 写一句缺口），以及本单元有效（已确认、未终止）的长期目标 `long_term_goals`（对象头加定义类块引用；`goal_ref` 指的那条已在「为什么」里，不重复）。
  - 出发对象没有门（Task、Activity、责任单元、Company）时 `carried` 为 null。
- **裁剪顺序变了**（补 48，2026-09-29 定）：超预算时先由远及近裁上层里不在 Why 链上的块（约束、计划这类），再裁最旧的事件，再由远及近裁跨链关系与上层的快照，Why 链上的项（上层的定义类块、多取的一跳、下层引用钉到的块）最后才裁。0.1 是先裁最旧的事件、再由远及近逐层裁。`plan.trimmed` 的形状与原因（`over_budget`、`over_level_cap`）不变，只是条目的先后不同；当前对象的块与最新快照、形成时带入的内容照旧不裁。
- **每个对象的事件上限**不计当前对象最近一次指派事件与推出它当前生命周期的事件：这两条只要在近期窗口里，就不会被上限挤掉，其余事件照旧按上限（默认 10 条）留最新的，所以当前对象的事件可能比上限多一两条。预算不够时，它们仍和别的事件一样按最旧的先裁。
- 包比 0.1 大：验收里从 Activity 出发的包约 11000 字，E&O 实验的五个场景约 8500 到 9500 字，都远在默认预算内。请求里不必再给预算；需要压成本时再给小一些的 `max_chars`。

## 十一、补记与迟记

- 外部事件与状态刷新可以补记过去的时刻：外部事件取 `occurred_at`，快照取 `as_of`；都不晚于写入时刻。读取按发生时刻排序并标「迟记」。
- 门事件、生命周期事件与其余记录事件的发生时刻就是写入时刻，不接受补记；天枢里「实际确认时刻」放在代记的 `external_confirmed_at`。

## 十二、天枢要改的，汇总

1. 切到新实例、新 scope、新凭证，信封版本改 `tkos.world/0.2`；同步表能按实例整批重来。
2. 每周进展：先记来源外部事件，再写带 `payload_type`、`source_event_refs` 的快照；进展与议题改为组件，组件 id 用天枢 id；`artifacts` 块改 `materials`。
3. 执行事项完成改记 Task 的 `world_deliver`（代记执行人），不再只记交付类外部事件。
4. 月度计划签发、任务卡确认、指派经代记写入，带 `on_behalf_of`；请参与的人先登记委托。
5. Mission 写个人任务的 `external_refs`（`mission:<编号>`）；Task 的 `todo:<uuid>` 由 E&O 建 Task 时写，E&O 责任单元的 `capability:05` 由 E&O 写好，战场暂不写。按列对象接口查回，替代同步表里的对象对照。
   - 新增的执行事项，先在对应 Mission 的执行计划块里写一条计划条目：组件 id 为 `todo:<uuid>`，`responsible` 填执行人，以 Agent 身份修订，带写入声明、不要求人工验收。E&O 按这些条目建 Task，写同一个 `todo:<uuid>`，天枢按外部引用查回后再代记指派、开始、交付。天枢服务主体不建对象。
   - 执行事项状态的对应：进入 `in_progress` 记 `world_start`，`done` 记 `world_deliver`，都代执行人记；`returned` 代 Mission Owner 记 `world_reject`。执行事项创建、建出 Task 之后先代 Mission Owner 记 `world_assign`。Task 的责任人只能是本 scope 里的人，Agent 不能当 Task 的责任人。天枢加上「通过」的话，代 Owner 记 `world_accept`。
6. 读取改从 `business`、`identity`、`records` 三组取字段；引用解析支持组件与事件两种新形式。
7. 议题按 Issue 的事件流转（提出、路由由天枢服务主体记；承接、处置由承接人本人记，或经议题族委托代记，见第八项）。
8. 调用日志的约定不变（对接说明 4.4），引用集合包括组件引用与事件引用。

## 十三、时间表

| 时间 | 事项 |
|-|-|
| 9 月 29 日 | 0.2 实验实例上线（地址见第一项），本清单写到的接口都可调；本清单交天枢 |
| 9 月 29 日晚 | 按天枢反馈重建（`24e6202`）：加议题代记族；十月起点按天枢的三个个人任务重播，周期目标与三个 Mission 都是草稿，门留给天枢代记；三个人的委托已登记，四族（门、指派、生命周期、议题），域 eo（CEO 另加 company），到 10 月 31 日；E&O 责任单元已带 `capability:05`；实测示例按这个版本生成 |
| 10 月 9 日前 | 交付新凭证、id 清单与实测示例（此前的凭证与 id 已随重建作废） |
| 10 月 9 日至 11 日 | 天枢按实例联调 |
| 10 月 12 日至 16 日 | 试用；16 日联调验收 |

三方会上要定的仍是对接说明 11.2 列的几项；外部引用的写法已于 9 月 29 日与天枢约定（第九项），战场挂在哪个对象上仍待定。Issue 的承接与处置是否纳入代记已于 9 月 29 日定下：纳入，单列议题族（第八项）。
