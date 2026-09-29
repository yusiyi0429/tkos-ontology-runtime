# tkos.world/0.2 给天枢的实测请求与返回示例

对象：天枢服务端接入本体的工程师。本文是《接口变化清单》（`docs/world-v02-tianshu-interface-changes.md`）承诺的实测示例，按天枢的接入顺序分节，每节对应清单的一项或几项。

**怎么生成的**：`deploy/world-02/examples.py` 在冒烟 scope 上按下面的顺序逐步真打 HTTP 接口，每一步断言返回码与关键字段，记下全部请求与返回，再脱敏渲染成本文。本文以服务提交 `24e6202`、`https://world-02.tokenkingos.com`上的运行 `20260929-091859-9d0f`（下文写作 `<run>`）为准；实例重建或契约改动后按 `deploy/world-02/README.md` 第 7 节后的说明重新生成。

**占位规则**：

- 同一次运行里的每个 id 按它指向什么换成占位，前后一致：`<scope>`；域 `<domain:eo>`；主体 `<principal:tianshu>`（冒号后是冒烟名单的主体键）；骨架对象 `<company>`、`<strategy>`、`<unit:eo>`；本次新建的对象按类型编号，如 `<mission-1>`、`<task-1>`、`<snapshot-1>`；事件 `<event-3>`、修订 `<revision-5>`、回执 `<receipt-2>`（事件的 `action_id` 就是产生它的回执 id）、上下文包 `<context-pack-1>`、角色指派 `<assignment:tianshu:AGENT@eo>`。引用里的 id 同样替换，例如 `<mission-1>@3#acceptance/m-ac1`、`event:<event-3>`。真实接口里这些位置都是 uuid。天枢执行事项的 id 在请求里是真 uuid（`todo:` 加 uuid），本文写作 `todo:<todo-uuid>`。
- 时刻是实跑时刻，格式原样：请求里按天枢的习惯写 `+08:00`，返回里服务统一给 UTC。
- 显示名一律是冒烟名单的角色名；分页游标写作 `<cursor>`，真实值是不透明字符串，原样带回即可。
- 「身份」是这个请求带哪个主体的凭证（放在请求头里，本文不列出凭证）。天枢的请求都用天枢服务主体的凭证；人本人的请求（登记委托、建 Task 等）只是为了示例完整。
- 过长的返回截短了：超过 6 项的数组留前 3 项，超过 1200 字的文本留开头（取上下文一节更紧：超过 2 项留 1 项，文本留 1500 字），截掉的在原处写明「截去 N 项」或「截去 N 字」。请求都是完整的。

**各步结果**：

| 节 | 内容 | 结果 |
|-|-|-|
|  | 准备（不是天枢的调用） | 通过 |
| 1 | 列对象、按外部引用查回 | 通过 |
| 2 | 写外部引用 | 通过 |
| 3 | 每周同步：来源事件与执行状态快照 | 通过 |
| 4 | 会议事件 | 通过 |
| 5 | 代记门：周期目标与 Mission 的承诺、确认 | 通过 |
| 6 | 执行计划：天枢写计划条目 | 通过 |
| 7 | Task：建、指派与生命周期 | 通过 |
| 8 | 执行计划：Co-Agent 再加一条 | 通过 |
| 9 | 议题：提出、路由、承接、处置、退回形成 | 通过 |
| 10 | 取上下文 | 通过 |
| 11 | 典型错误 | 通过 |
|  | 收尾：撤销委托 | 通过 |

## 准备（不是天枢的调用）

以下由人本人经 HTTP 记，只列结果，不列请求；骨架（Company、Strategy、两个责任单元）沿用冒烟 scope 已有的一套。责任单元（能力域）的外部引用由 E&O 写好：单元的 DRI 本人写，天枢只写 Mission 的（天枢改单元的是 403，见第 11 节）。

- `eo-dri`（E&O DRI）给 E&O 责任单元写能力域的外部引用 `tianshu`/`capability:05`（骨架，重跑原样再写）：`<unit:eo>`
- `ceo`（CEO）建公司级长期目标并确认：`<long-term-goal-1>`
- `eo-dri`（E&O DRI）建 E&O 长期目标，CEO 确认：`<long-term-goal-2>`
- `eo-dri`（E&O DRI）建本期周期目标（草稿，第 5 节代记承诺与确认）：`<period-goal-1>`
- `eo-dri`（E&O DRI）建 Mission（草稿）并指派 E&O Mission Owner：`<mission-1>`

## 1. 列对象、按外部引用查回

接口清单第十项：按外部引用查回 E&O 写好的能力域；按单元、类型、周期列 Mission；取对象的三组读投影。本次 Mission 的外部引用还没写，按它查回空列表。

**按外部引用查回责任单元：E&O 写好的能力域 `capability:05`**

`GET /v1/world/objects?external_system=tianshu&external_id=capability:05`，身份：`tianshu`（天枢服务主体）

返回 `200`：

```json
{
  "items": [
    {
      "object_id": "<unit:eo>",
      "object_type": "ResponsibilityUnit",
      "type_display_name": "责任单元",
      "category": {"id": "business_object", "display_name": "业务对象"},
      "title": "E&O",
      "version": 2,
      "revision_id": "<revision-3>",
      "object_version": 3,
      "lifecycle": null,
      "domain_id": "<domain:eo>",
      "external_refs": [{"id": "capability:05", "url": null, "system": "tianshu"}],
      "contract_version": "tkos.world/0.2"
    }
  ],
  "next_cursor": null
}
```

**列 E&O 单元本期的 Mission：按建立时刻升序分页，每页 5 条，把上一页的 `next_cursor` 原样带回取下一页；这里列出本次 Mission 所在的那一页**

`GET /v1/world/objects?unit_id=<unit:eo>&type=Mission&period=2026-09&limit=5`，身份：`tianshu`（天枢服务主体）

返回 `200`：

```json
{
  "items": [
    {
      "object_id": "<mission-2>",
      "object_type": "Mission",
      "type_display_name": "Mission",
      "category": {"id": "business_object", "display_name": "业务对象"},
      "title": "冒烟 20260929-091735-0165 Mission",
      "version": 3,
      "revision_id": "<revision-10>",
      "object_version": 6,
      "lifecycle": {"status": "in_progress", "display_name": "进行中", "event_id": "<event-9>"},
      "domain_id": "<domain:eo>",
      "external_refs": [],
      "contract_version": "tkos.world/0.2"
    },
    {
      "object_id": "<mission-3>",
      "object_type": "Mission",
      "type_display_name": "Mission",
      "category": {"id": "business_object", "display_name": "业务对象"},
      "title": "冒烟 20260929-091753-a344 Mission",
      "version": 3,
      "revision_id": "<revision-11>",
      "object_version": 6,
      "lifecycle": {"status": "in_progress", "display_name": "进行中", "event_id": "<event-10>"},
      "domain_id": "<domain:eo>",
      "external_refs": [],
      "contract_version": "tkos.world/0.2"
    },
    {
      "object_id": "<mission-4>",
      "object_type": "Mission",
      "type_display_name": "Mission",
      "category": {"id": "business_object", "display_name": "业务对象"},
      "title": "冒烟 20260929-091805-2614 Mission",
      "version": 3,
      "revision_id": "<revision-12>",
      "object_version": 6,
      "lifecycle": {"status": "in_progress", "display_name": "进行中", "event_id": "<event-11>"},
      "domain_id": "<domain:eo>",
      "external_refs": [],
      "contract_version": "tkos.world/0.2"
    },
    {
      "object_id": "<mission-1>",
      "object_type": "Mission",
      "type_display_name": "Mission",
      "category": {"id": "business_object", "display_name": "业务对象"},
      "title": "示例 <run> Mission",
      "version": 2,
      "revision_id": "<revision-9>",
      "object_version": 2,
      "lifecycle": {"status": "draft", "display_name": "草稿", "event_id": "<event-7>"},
      "domain_id": "<domain:eo>",
      "external_refs": [],
      "contract_version": "tkos.world/0.2"
    }
  ],
  "next_cursor": null
}
```

**取对象：`business`、`identity`、`records` 三组**

`GET /v1/world/objects/<mission-1>`，身份：`tianshu`（天枢服务主体）

返回 `200`：

```json
{
  "object_id": "<mission-1>",
  "business": {
    "object_id": "<mission-1>",
    "object_type": "Mission",
    "type_display_name": "Mission",
    "category": {"id": "business_object", "display_name": "业务对象"},
    "candidate": false,
    "version": 2,
    "revision_id": "<revision-9>",
    "object_version": 2,
    "title": "示例 <run> Mission",
    "attributes": {
      "core_battle": false,
      "responsible": "<principal:eo-owner>",
      "external_refs": []
    },
    "relations": [
      {
        "field": "goal_ref",
        "relation": "serves",
        "value": {
          "block": null,
          "component": null,
          "object_id": "<period-goal-1>",
          "revision_id": "<revision-7>",
          "object_version": 1,
          "ref": "<period-goal-1>@1"
        }
      },
      {"field": "depends_on", "relation": "depends_on", "value": []},
      {"field": "contributes_to", "relation": "contributes_to", "value": []}
    ],
    "blocks": [
      {
        "id": "definition",
        "display_name": "定义",
        "kind": "definition",
        "class": "formal",
        "value": {
          "refs": [],
          "text": "示例：Mission 定义",
          "artifacts": [],
          "components": []
        },
        "empty": false,
        "text": "示例：Mission 定义",
        "components": [],
        "ref": "<mission-1>@2#definition"
      },
      {
        "id": "acceptance",
        "display_name": "验收标准",
        "kind": "definition",
        "class": "formal",
        "value": {
          "refs": [],
          "text": "",
          "artifacts": [],
          "components": [
            {
              "id": "m-ac1",
              "refs": [
                {
                  "block": "acceptance",
                  "component": "pg-ac1",
                  "object_id": "<period-goal-1>",
                  "revision_id": "<revision-7>",
                  "object_version": 1,
                  "ref": "<period-goal-1>@1#acceptance/pg-ac1"
                }
              ],
              "text": "示例：交付可用",
              "type": "acceptance_criterion",
              "scope": null,
              "artifacts": [],
              "attributes": {},
              "ref": "<mission-1>@2#acceptance/m-ac1"
            }
          ]
        },
        "empty": false,
        "text": "",
        "components": [
          {
            "id": "m-ac1",
            "refs": [
              {
                "block": "acceptance",
                "component": "pg-ac1",
                "object_id": "<period-goal-1>",
                "revision_id": "<revision-7>",
                "object_version": 1,
                "ref": "<period-goal-1>@1#acceptance/pg-ac1"
              }
            ],
            "text": "示例：交付可用",
            "type": "acceptance_criterion",
            "scope": null,
            "artifacts": [],
            "attributes": {},
            "ref": "<mission-1>@2#acceptance/m-ac1"
          }
        ],
        "ref": "<mission-1>@2#acceptance"
      },
      {
        "id": "play",
        "display_name": "打法",
        "kind": "definition",
        "class": "formal",
        "value": {"refs": [], "text": "示例：核心路径", "artifacts": [], "components": []},
        "empty": false,
        "text": "示例：核心路径",
        "components": [],
        "ref": "<mission-1>@2#play"
      },
      {
        "id": "execution_plan",
        "display_name": "执行计划",
        "kind": "plan",
        "class": "activity",
        "value": null,
        "empty": true,
        "text": "当前没有执行计划",
        "components": [],
        "ref": "<mission-1>@2#execution_plan"
      },
      {
        "id": "constraint",
        "display_name": "约束",
        "kind": "constraint",
        "class": "formal",
        "value": null,
        "empty": true,
        "text": "当前没有约束",
        "components": [],
        "ref": "<mission-1>@2#constraint"
      }
    ],
    "component_ledger": [
      {
        "id": "m-ac1",
        "type": "acceptance_criterion",
        "block": "acceptance",
        "added_in_version": 1,
        "removed_in_version": null
      }
    ],
    "formal": {"lifecycle_status": "draft", "effective_revision_id": null},
    "round": null
  },
  "identity": {
    "responsible": {
      "roles": {"human": "OWNER"},
      "source": "attribute",
      "principals": [
        {
          "principal_id": "<principal:eo-owner>",
          "principal_type": "human",
          "display_name": "E&O Mission Owner"
        }
      ]
    },
    "delegations": []
  },
  "records": {
    "lifecycle": {"status": "draft", "display_name": "草稿", "event_id": "<event-7>"},
    "latest_state": null,
    "confirmed_review": null,
    "open_issues": []
  },
  "protocol": {
    "registration_status": "registered",
    "interpretation_status": "world_v0_2",
    "protocol_id": "tkos.world",
    "contract_version": "tkos.world/0.2",
    "method_profile_ref": {
      "profile_id": "urn:tkos:world",
      "revision": "0.2.0",
      "canonical_hash": "0ec50f6f2cc6f4c9a7cec1f117e0225cae8cf93d044ff9335ce26f0d49d4b159"
    },
    "binding_version": 1,
    "record_origin": "synthetic",
    "note": "World 0.2 draft; objects read in three groups (business objects, identity projection, time records) with an append-only event log."
  }
}
```

**按外部引用查找本次的 Mission：还没写过这一对时 `items` 为空**

`GET /v1/world/objects?external_system=tianshu&external_id=mission:demo-<run>`，身份：`tianshu`（天枢服务主体）

返回 `200`：

```json
{"items": [], "next_cursor": null}
```


## 2. 写外部引用

接口清单第九项：天枢以 Agent 身份修订 Mission 的 `external_refs`，带写入声明，只改活动属性时 `human_acceptance.required` 为 false。每个写入都先以同一请求体调 `/v1/actions/prepare`，再把返回的 `expected_versions` 带回 `/v1/actions` 提交；本节列出两段，之后只列提交与出错的 prepare。

**天枢修订 Mission 的外部引用：先 prepare**

`POST /v1/actions/prepare`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_revise_object",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<mission-1>", "revision_id": "<revision-9>", "expected_version": 2},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:009",
  "reason": "world-02 天枢接入示例",
  "params": {
    "payload": {"external_refs": [{"system": "tianshu", "id": "mission:demo-<run>"}]},
    "declaration": {
      "scene": "<mission-1>@2",
      "trigger": "天枢 Mission 关联本体",
      "human_acceptance": {"required": false}
    }
  }
}
```

返回 `200`：

```json
{
  "action_type": "world_revise_object",
  "auth_epoch": 4,
  "target": {"object_id": "<mission-1>", "expected_version": 2, "revision_id": "<revision-9>"},
  "expected_versions": []
}
```

**再以同一请求体（带上 prepare 返回的 `expected_versions`）提交，返回回执**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_revise_object",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<mission-1>", "revision_id": "<revision-9>", "expected_version": 2},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:009",
  "reason": "world-02 天枢接入示例",
  "params": {
    "payload": {"external_refs": [{"system": "tianshu", "id": "mission:demo-<run>"}]},
    "declaration": {
      "scene": "<mission-1>@2",
      "trigger": "天枢 Mission 关联本体",
      "human_acceptance": {"required": false}
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-9>",
  "action_type": "world_revise_object",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<mission-1>@3",
    "version": 3,
    "event_id": "<event-12>",
    "domain_id": "<domain:eo>",
    "object_id": "<mission-1>",
    "declaration": {
      "scene": {
        "ref": "<mission-1>@2",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-9>",
        "object_version": 2
      },
      "trigger": "天枢 Mission 关联本体",
      "human_acceptance": {"required": false}
    },
    "revision_id": "<revision-13>",
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<period-goal-1>", "<mission-1>"],
    "required_assignment_ids": ["<assignment:tianshu:AGENT@eo>"]
  },
  "object_versions": [{"object_id": "<mission-1>", "object_version": 3}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:05.064314+00:00"
}
```

**按外部引用查回本次的 Mission**

`GET /v1/world/objects?external_system=tianshu&external_id=mission:demo-<run>`，身份：`tianshu`（天枢服务主体）

返回 `200`：

```json
{
  "items": [
    {
      "object_id": "<mission-1>",
      "object_type": "Mission",
      "type_display_name": "Mission",
      "category": {"id": "business_object", "display_name": "业务对象"},
      "title": "示例 <run> Mission",
      "version": 3,
      "revision_id": "<revision-13>",
      "object_version": 3,
      "lifecycle": {"status": "draft", "display_name": "草稿", "event_id": "<event-7>"},
      "domain_id": "<domain:eo>",
      "external_refs": [{"id": "mission:demo-<run>", "url": null, "system": "tianshu"}],
      "contract_version": "tkos.world/0.2"
    }
  ],
  "next_cursor": null
}
```


## 3. 每周同步：来源事件与执行状态快照

接口清单第四项：先记一条外部事件作来源，再写引用它的执行状态快照。进展条目的组件 id 用天枢执行事项 id（`todo:` 加天枢的 uuid），本期条目放 `attributes.entries`，多个链接放组件的 `artifacts`；问题写成 `issues` 块里的 `issue` 组件，组件 id 用天枢 issue id。幂等键按清单的建议写。

**先记来源外部事件（`category: other`）**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_record_event",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "tianshu:weekly-sync:<mission-1>:2026-W40",
  "reason": "world-02 天枢接入示例",
  "params": {
    "category": "other",
    "subject_refs": ["<mission-1>@3"],
    "occurred_at": "2026-09-29T17:18:05+08:00",
    "content": {"text": "天枢每周同步 2026-W40"},
    "declaration": {
      "scene": "<mission-1>@3",
      "trigger": "天枢每周同步",
      "human_acceptance": {"required": false}
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-10>",
  "action_type": "world_record_event",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "event_id": "<event-13>",
    "domain_id": "<domain:eo>",
    "declaration": {
      "scene": {
        "ref": "<mission-1>@3",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-13>",
        "object_version": 3
      },
      "trigger": "天枢每周同步",
      "human_acceptance": {"required": false}
    },
    "occurred_at": "2026-09-29T09:18:05Z",
    "subject_refs": [
      {
        "ref": "<mission-1>@3",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-13>",
        "object_version": 3
      }
    ],
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<mission-1>"],
    "required_assignment_ids": []
  },
  "object_versions": [],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:05.297657+00:00"
}
```

**再写执行状态快照，`source_event_refs` 引用上一步的事件**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_refresh_state",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "tianshu:weekly:<mission-1>:2026-W40",
  "reason": "world-02 天枢接入示例",
  "params": {
    "declaration": {
      "scene": "<mission-1>@3",
      "trigger": "天枢每周同步",
      "human_acceptance": {"required": false}
    },
    "payload": {
      "title": "示例 <run> 执行状态 2026-W40",
      "subject_ref": "<mission-1>@3",
      "as_of": "2026-09-29T17:18:35+08:00",
      "period": "2026-09",
      "payload_type": "execution_state",
      "source_event_refs": ["event:<event-13>"],
      "blocks": {
        "progress": {
          "components": [
            {
              "id": "todo:<todo-uuid>",
              "type": "progress_item",
              "text": "接入 0.2 的本周进展",
              "artifacts": [
                "https://example.com/tianshu/todo/1",
                "https://example.com/tianshu/pr/1"
              ],
              "attributes": {
                "principal_id": "<principal:eo-owner>",
                "principal_name": "E&O Mission Owner",
                "external_status": "进行中",
                "entries": [
                  {
                    "at": "2026-09-27T17:19:05+08:00",
                    "source": "web",
                    "text": "对齐 0.2 接口变化清单"
                  },
                  {
                    "at": "2026-09-28T17:19:05+08:00",
                    "source": "github",
                    "text": "提交接入改动",
                    "url": "https://example.com/tianshu/pr/1"
                  }
                ]
              }
            }
          ]
        },
        "issues": {
          "components": [
            {
              "id": "issue:demo-<run>-1",
              "type": "issue",
              "text": "每周同步里发现的问题",
              "attributes": {
                "core_question": "天枢里的指派要不要同步成本体的 Task 指派？",
                "responsible_hint": "<principal:eo-owner>"
              }
            }
          ]
        },
        "materials": {"artifacts": ["https://example.com/tianshu/weekly/1"]}
      }
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-11>",
  "action_type": "world_refresh_state",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<snapshot-1>@1",
    "as_of": "2026-09-29T09:18:35Z",
    "version": 1,
    "event_id": "<event-14>",
    "domain_id": "<domain:eo>",
    "generator": "<principal:tianshu>",
    "object_id": "<snapshot-1>",
    "declaration": {
      "scene": {
        "ref": "<mission-1>@3",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-13>",
        "object_version": 3
      },
      "trigger": "天枢每周同步",
      "human_acceptance": {"required": false}
    },
    "revision_id": "<revision-14>",
    "subject_refs": [
      {
        "ref": "<mission-1>@3",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-13>",
        "object_version": 3
      }
    ],
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<mission-1>", "<snapshot-1>"],
    "required_assignment_ids": ["<assignment:tianshu:AGENT@eo>"]
  },
  "object_versions": [{"object_id": "<snapshot-1>", "object_version": 1}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:05.412692+00:00"
}
```

**取状态：最新快照，带生成者、来源事件与未经确认标记**

`GET /v1/world/objects/<mission-1>/state`，身份：`tianshu`（天枢服务主体）

返回 `200`：

```json
{
  "object_id": "<mission-1>",
  "as_of": null,
  "snapshot": {
    "object_id": "<snapshot-1>",
    "object_type": "StateSnapshot",
    "category": {"id": "time_record", "display_name": "时间记录"},
    "version": 1,
    "revision_id": "<revision-14>",
    "ref": "<snapshot-1>@1",
    "title": "示例 <run> 执行状态 2026-W40",
    "subject_ref": {
      "block": null,
      "component": null,
      "object_id": "<mission-1>",
      "revision_id": "<revision-13>",
      "object_version": 3,
      "ref": "<mission-1>@3"
    },
    "as_of": "2026-09-29T09:18:35Z",
    "period": "2026-09",
    "generator": {
      "principal_id": "<principal:tianshu>",
      "principal_type": "agent",
      "display_name": "天枢服务主体"
    },
    "source_event_refs": [{"event_id": "<event-13>", "ref": "event:<event-13>"}],
    "payload_type": {"id": "execution_state", "display_name": "执行状态"},
    "blocks": [
      {
        "id": "progress",
        "display_name": "进展",
        "kind": "state",
        "class": null,
        "value": {
          "refs": [],
          "text": "",
          "artifacts": [],
          "components": [
            {
              "id": "todo:<todo-uuid>",
              "refs": [],
              "text": "接入 0.2 的本周进展",
              "type": "progress_item",
              "scope": null,
              "artifacts": [
                "https://example.com/tianshu/todo/1",
                "https://example.com/tianshu/pr/1"
              ],
              "attributes": {
                "entries": [
                  {
                    "at": "2026-09-27T09:19:05Z",
                    "url": null,
                    "text": "对齐 0.2 接口变化清单",
                    "source": "web"
                  },
                  {
                    "at": "2026-09-28T09:19:05Z",
                    "url": "https://example.com/tianshu/pr/1",
                    "text": "提交接入改动",
                    "source": "github"
                  }
                ],
                "principal_id": "<principal:eo-owner>",
                "principal_name": "E&O Mission Owner",
                "external_status": "进行中"
              },
              "ref": "<snapshot-1>@1#progress/todo:<todo-uuid>"
            }
          ]
        },
        "empty": false,
        "text": "",
        "components": [
          {
            "id": "todo:<todo-uuid>",
            "refs": [],
            "text": "接入 0.2 的本周进展",
            "type": "progress_item",
            "scope": null,
            "artifacts": [
              "https://example.com/tianshu/todo/1",
              "https://example.com/tianshu/pr/1"
            ],
            "attributes": {
              "entries": [
                {
                  "at": "2026-09-27T09:19:05Z",
                  "url": null,
                  "text": "对齐 0.2 接口变化清单",
                  "source": "web"
                },
                {
                  "at": "2026-09-28T09:19:05Z",
                  "url": "https://example.com/tianshu/pr/1",
                  "text": "提交接入改动",
                  "source": "github"
                }
              ],
              "principal_id": "<principal:eo-owner>",
              "principal_name": "E&O Mission Owner",
              "external_status": "进行中"
            },
            "ref": "<snapshot-1>@1#progress/todo:<todo-uuid>"
          }
        ],
        "ref": "<snapshot-1>@1#progress"
      },
      {
        "id": "blockers",
        "display_name": "阻塞与偏差",
        "kind": "state",
        "class": null,
        "value": null,
        "empty": true,
        "text": "当前没有阻塞与偏差",
        "components": [],
        "ref": "<snapshot-1>@1#blockers"
      },
      {
        "id": "issues",
        "display_name": "问题",
        "kind": "state",
        "class": null,
        "value": {
          "refs": [],
          "text": "",
          "artifacts": [],
          "components": [
            {
              "id": "issue:demo-<run>-1",
              "refs": [],
              "text": "每周同步里发现的问题",
              "type": "issue",
              "scope": null,
              "artifacts": [],
              "attributes": {
                "core_question": "天枢里的指派要不要同步成本体的 Task 指派？",
                "responsible_hint": "<principal:eo-owner>"
              },
              "ref": "<snapshot-1>@1#issues/issue:demo-<run>-1"
            }
          ]
        },
        "empty": false,
        "text": "",
        "components": [
          {
            "id": "issue:demo-<run>-1",
            "refs": [],
            "text": "每周同步里发现的问题",
            "type": "issue",
            "scope": null,
            "artifacts": [],
            "attributes": {
              "core_question": "天枢里的指派要不要同步成本体的 Task 指派？",
              "responsible_hint": "<principal:eo-owner>"
            },
            "ref": "<snapshot-1>@1#issues/issue:demo-<run>-1"
          }
        ],
        "ref": "<snapshot-1>@1#issues"
      },
      {
        "id": "materials",
        "display_name": "材料",
        "kind": "state",
        "class": null,
        "value": {
          "refs": [],
          "text": "",
          "artifacts": ["https://example.com/tianshu/weekly/1"],
          "components": []
        },
        "empty": false,
        "text": "",
        "components": [],
        "ref": "<snapshot-1>@1#materials"
      }
    ],
    "unconfirmed": true
  }
}
```


## 4. 会议事件

接口清单第十一项：外部事件可以补记过去的时刻（这里补记两小时前的会），读取按发生时刻升序并标迟记。

**记会议事件（`category: meeting`，补记两小时前的会，主体可以多个）**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_record_event",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:012",
  "reason": "world-02 天枢接入示例",
  "params": {
    "category": "meeting",
    "subject_refs": ["<mission-1>@3", "<period-goal-1>@1"],
    "occurred_at": "2026-09-29T15:19:05+08:00",
    "content": {"text": "E&O 周会：确认本周计划与问题", "artifacts": ["https://example.com/tianshu/minutes/1"]},
    "declaration": {
      "scene": "<mission-1>@3",
      "trigger": "天枢同步会议纪要",
      "human_acceptance": {"required": false}
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-12>",
  "action_type": "world_record_event",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "event_id": "<event-15>",
    "domain_id": "<domain:eo>",
    "declaration": {
      "scene": {
        "ref": "<mission-1>@3",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-13>",
        "object_version": 3
      },
      "trigger": "天枢同步会议纪要",
      "human_acceptance": {"required": false}
    },
    "occurred_at": "2026-09-29T07:19:05Z",
    "subject_refs": [
      {
        "ref": "<mission-1>@3",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-13>",
        "object_version": 3
      },
      {
        "ref": "<period-goal-1>@1",
        "block": null,
        "component": null,
        "object_id": "<period-goal-1>",
        "revision_id": "<revision-7>",
        "object_version": 1
      }
    ],
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<period-goal-1>", "<mission-1>"],
    "required_assignment_ids": []
  },
  "object_versions": [],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:05.895064+00:00"
}
```

**取事件：按发生时刻升序，补记的会标迟记**

`GET /v1/world/objects/<mission-1>/events`，身份：`tianshu`（天枢服务主体）

返回 `200`：

```json
{
  "object_id": "<mission-1>",
  "since": null,
  "events": [
    {
      "event_id": "<event-15>",
      "scope_id": "<scope>",
      "kind": "event.recorded",
      "class": "record",
      "category": "meeting",
      "outcome": null,
      "disposition": null,
      "subject_refs": [
        {
          "block": null,
          "component": null,
          "object_id": "<mission-1>",
          "revision_id": "<revision-13>",
          "object_version": 3,
          "ref": "<mission-1>@3"
        },
        {
          "block": null,
          "component": null,
          "object_id": "<period-goal-1>",
          "revision_id": "<revision-7>",
          "object_version": 1,
          "ref": "<period-goal-1>@1"
        }
      ],
      "principal": {
        "principal_id": "<principal:tianshu>",
        "principal_type": "agent",
        "display_name": "天枢服务主体"
      },
      "on_behalf_of": null,
      "external_confirmation": null,
      "occurred_at": "2026-09-29T07:19:05Z",
      "recorded_at": "2026-09-29T09:19:05.894743Z",
      "late": true,
      "content": {
        "refs": [],
        "text": "E&O 周会：确认本周计划与问题",
        "artifacts": ["https://example.com/tianshu/minutes/1"],
        "components": []
      },
      "detail": null,
      "action": "world_record_event",
      "action_id": "<receipt-12>",
      "supersedes_event_id": null,
      "corrected_by": [],
      "withdrawn_by": []
    },
    {
      "event_id": "<event-13>",
      "scope_id": "<scope>",
      "kind": "event.recorded",
      "class": "record",
      "category": "other",
      "outcome": null,
      "disposition": null,
      "subject_refs": [
        {
          "block": null,
          "component": null,
          "object_id": "<mission-1>",
          "revision_id": "<revision-13>",
          "object_version": 3,
          "ref": "<mission-1>@3"
        }
      ],
      "principal": {
        "principal_id": "<principal:tianshu>",
        "principal_type": "agent",
        "display_name": "天枢服务主体"
      },
      "on_behalf_of": null,
      "external_confirmation": null,
      "occurred_at": "2026-09-29T09:18:05Z",
      "recorded_at": "2026-09-29T09:19:05.297341Z",
      "late": true,
      "content": {"refs": [], "text": "天枢每周同步 2026-W40", "artifacts": [], "components": []},
      "detail": null,
      "action": "world_record_event",
      "action_id": "<receipt-10>",
      "supersedes_event_id": null,
      "corrected_by": [],
      "withdrawn_by": []
    },
    {
      "event_id": "<event-14>",
      "scope_id": "<scope>",
      "kind": "state.refreshed",
      "class": "record",
      "category": null,
      "outcome": null,
      "disposition": null,
      "subject_refs": [
        {
          "block": null,
          "component": null,
          "object_id": "<snapshot-1>",
          "revision_id": "<revision-14>",
          "object_version": 1,
          "ref": "<snapshot-1>@1"
        },
        {
          "block": null,
          "component": null,
          "object_id": "<mission-1>",
          "revision_id": "<revision-13>",
          "object_version": 3,
          "ref": "<mission-1>@3"
        }
      ],
      "principal": {
        "principal_id": "<principal:tianshu>",
        "principal_type": "agent",
        "display_name": "天枢服务主体"
      },
      "on_behalf_of": null,
      "external_confirmation": null,
      "occurred_at": "2026-09-29T09:18:35Z",
      "recorded_at": "2026-09-29T09:19:05.412398Z",
      "late": true,
      "content": null,
      "detail": null,
      "action": "world_refresh_state",
      "action_id": "<receipt-11>",
      "supersedes_event_id": null,
      "corrected_by": [],
      "withdrawn_by": []
    },
    {
      "event_id": "<event-7>",
      "scope_id": "<scope>",
      "kind": "object.created",
      "class": "record",
      "category": null,
      "outcome": null,
      "disposition": null,
      "subject_refs": [
        {
          "block": null,
          "component": null,
          "object_id": "<mission-1>",
          "revision_id": "<revision-8>",
          "object_version": 1,
          "ref": "<mission-1>@1"
        }
      ],
      "principal": {
        "principal_id": "<principal:eo-dri>",
        "principal_type": "human",
        "display_name": "E&O DRI"
      },
      "on_behalf_of": null,
      "external_confirmation": null,
      "occurred_at": "2026-09-29T09:19:04.598890Z",
      "recorded_at": "2026-09-29T09:19:04.598890Z",
      "late": false,
      "content": null,
      "detail": null,
      "action": "world_create_object",
      "action_id": "<receipt-7>",
      "supersedes_event_id": null,
      "corrected_by": [],
      "withdrawn_by": []
    },
    {
      "event_id": "<event-8>",
      "scope_id": "<scope>",
      "kind": "assign",
      "class": "record",
      "category": null,
      "outcome": null,
      "disposition": null,
      "subject_refs": [
        {
          "block": null,
          "component": null,
          "object_id": "<mission-1>",
          "revision_id": "<revision-9>",
          "object_version": 2,
          "ref": "<mission-1>@2"
        }
      ],
      "principal": {
        "principal_id": "<principal:eo-dri>",
        "principal_type": "human",
        "display_name": "E&O DRI"
      },
      "on_behalf_of": null,
      "external_confirmation": null,
      "occurred_at": "2026-09-29T09:19:04.745479Z",
      "recorded_at": "2026-09-29T09:19:04.745479Z",
      "late": false,
      "content": null,
      "detail": {"principal_id": "<principal:eo-owner>"},
      "action": "world_assign",
      "action_id": "<receipt-8>",
      "supersedes_event_id": null,
      "corrected_by": [],
      "withdrawn_by": []
    },
    {
      "event_id": "<event-12>",
      "scope_id": "<scope>",
      "kind": "object.revised",
      "class": "record",
      "category": null,
      "outcome": null,
      "disposition": null,
      "subject_refs": [
        {
          "block": null,
          "component": null,
          "object_id": "<mission-1>",
          "revision_id": "<revision-13>",
          "object_version": 3,
          "ref": "<mission-1>@3"
        }
      ],
      "principal": {
        "principal_id": "<principal:tianshu>",
        "principal_type": "agent",
        "display_name": "天枢服务主体"
      },
      "on_behalf_of": null,
      "external_confirmation": null,
      "occurred_at": "2026-09-29T09:19:05.064036Z",
      "recorded_at": "2026-09-29T09:19:05.064036Z",
      "late": false,
      "content": null,
      "detail": null,
      "action": "world_revise_object",
      "action_id": "<receipt-9>",
      "supersedes_event_id": null,
      "corrected_by": [],
      "withdrawn_by": []
    }
  ]
}
```


## 5. 代记门：周期目标与 Mission 的承诺、确认

接口清单第八项：人本人先登记委托（门、指派、生命周期、议题，按人各取所需），天枢再带 `on_behalf_of` 代记，代记写入不带写入声明。事件同时记下记录者（天枢服务主体）与被代记的人，外部确认时刻另存。

**CEO 本人登记委托（gate，E&O 域，一小时）**

`POST /v1/actions`，身份：`ceo`（CEO）

```json
{
  "action_type": "world_grant_delegation",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:013",
  "reason": "world-02 天枢接入示例",
  "params": {
    "delegate_principal_id": "<principal:tianshu>",
    "families": ["gate"],
    "domain_ids": ["<domain:eo>"],
    "valid_until": "2026-09-29T18:19:05+08:00"
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-13>",
  "action_type": "world_grant_delegation",
  "actor_id": "<principal:ceo>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "detail": {
      "families": ["gate"],
      "domain_ids": ["<domain:eo>"],
      "valid_until": "2026-09-29T10:19:05Z",
      "delegate_principal_id": "<principal:tianshu>"
    },
    "event_id": "<event-16>",
    "domain_id": "<domain:company>",
    "subject_refs": [
      {
        "ref": "<company>@1",
        "block": null,
        "component": null,
        "object_id": "<company>",
        "revision_id": "<revision-4>",
        "object_version": 1
      }
    ],
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<company>"],
    "required_assignment_ids": []
  },
  "object_versions": [],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:06.003968+00:00"
}
```

**E&O DRI 本人登记委托（gate，E&O 域，一小时）**

`POST /v1/actions`，身份：`eo-dri`（E&O DRI）

```json
{
  "action_type": "world_grant_delegation",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:014",
  "reason": "world-02 天枢接入示例",
  "params": {
    "delegate_principal_id": "<principal:tianshu>",
    "families": ["gate"],
    "domain_ids": ["<domain:eo>"],
    "valid_until": "2026-09-29T18:19:05+08:00"
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-14>",
  "action_type": "world_grant_delegation",
  "actor_id": "<principal:eo-dri>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "detail": {
      "families": ["gate"],
      "domain_ids": ["<domain:eo>"],
      "valid_until": "2026-09-29T10:19:05Z",
      "delegate_principal_id": "<principal:tianshu>"
    },
    "event_id": "<event-17>",
    "domain_id": "<domain:company>",
    "subject_refs": [
      {
        "ref": "<company>@1",
        "block": null,
        "component": null,
        "object_id": "<company>",
        "revision_id": "<revision-4>",
        "object_version": 1
      }
    ],
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<company>"],
    "required_assignment_ids": []
  },
  "object_versions": [],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:06.083301+00:00"
}
```

**E&O Mission Owner 本人登记委托（gate、assign、lifecycle、issue，E&O 域，一小时）**

`POST /v1/actions`，身份：`eo-owner`（E&O Mission Owner）

```json
{
  "action_type": "world_grant_delegation",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:015",
  "reason": "world-02 天枢接入示例",
  "params": {
    "delegate_principal_id": "<principal:tianshu>",
    "families": ["gate", "assign", "lifecycle", "issue"],
    "domain_ids": ["<domain:eo>"],
    "valid_until": "2026-09-29T18:19:05+08:00"
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-15>",
  "action_type": "world_grant_delegation",
  "actor_id": "<principal:eo-owner>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "detail": {
      "families": ["gate", "assign", "lifecycle", "issue"],
      "domain_ids": ["<domain:eo>"],
      "valid_until": "2026-09-29T10:19:05Z",
      "delegate_principal_id": "<principal:tianshu>"
    },
    "event_id": "<event-18>",
    "domain_id": "<domain:company>",
    "subject_refs": [
      {
        "ref": "<company>@1",
        "block": null,
        "component": null,
        "object_id": "<company>",
        "revision_id": "<revision-4>",
        "object_version": 1
      }
    ],
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<company>"],
    "required_assignment_ids": []
  },
  "object_versions": [],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:06.421084+00:00"
}
```

**E&O 执行者 本人登记委托（lifecycle，E&O 域，一小时）**

`POST /v1/actions`，身份：`eo-ic`（E&O 执行者）

```json
{
  "action_type": "world_grant_delegation",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:016",
  "reason": "world-02 天枢接入示例",
  "params": {
    "delegate_principal_id": "<principal:tianshu>",
    "families": ["lifecycle"],
    "domain_ids": ["<domain:eo>"],
    "valid_until": "2026-09-29T18:19:05+08:00"
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-16>",
  "action_type": "world_grant_delegation",
  "actor_id": "<principal:eo-ic>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "detail": {
      "families": ["lifecycle"],
      "domain_ids": ["<domain:eo>"],
      "valid_until": "2026-09-29T10:19:05Z",
      "delegate_principal_id": "<principal:tianshu>"
    },
    "event_id": "<event-19>",
    "domain_id": "<domain:company>",
    "subject_refs": [
      {
        "ref": "<company>@1",
        "block": null,
        "component": null,
        "object_id": "<company>",
        "revision_id": "<revision-4>",
        "object_version": 1
      }
    ],
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<company>"],
    "required_assignment_ids": []
  },
  "object_versions": [],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:06.490826+00:00"
}
```

**代 E&O DRI 承诺周期目标（月度计划提交）**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_commit_period_goal",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<period-goal-1>", "revision_id": "<revision-7>", "expected_version": 1},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:018",
  "reason": "world-02 天枢接入示例",
  "params": {
    "on_behalf_of": {
      "principal_id": "<principal:eo-dri>",
      "external_record_id": "tianshu:plan-submit:<run>-017",
      "external_confirmed_at": "2026-09-29T17:16:06+08:00"
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-17>",
  "action_type": "world_commit_period_goal",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<period-goal-1>@1",
    "version": 1,
    "event_id": "<event-20>",
    "domain_id": "<domain:eo>",
    "object_id": "<period-goal-1>",
    "revision_id": "<revision-7>",
    "on_behalf_of": {
      "principal_id": "<principal:eo-dri>",
      "external_record_id": "tianshu:plan-submit:<run>-017",
      "delegation_event_id": "<event-17>",
      "external_confirmed_at": "2026-09-29T09:16:06Z"
    },
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<period-goal-1>"],
    "required_assignment_ids": ["<assignment:eo-dri:DOMAIN_DRI@eo>"]
  },
  "object_versions": [{"object_id": "<period-goal-1>", "object_version": 2}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:06.642077+00:00"
}
```

**代 CEO 确认周期目标（月度计划签发）**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_confirm_period_goal",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<period-goal-1>", "revision_id": "<revision-7>", "expected_version": 2},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:020",
  "reason": "world-02 天枢接入示例",
  "params": {
    "outcome": "accepted",
    "on_behalf_of": {
      "principal_id": "<principal:ceo>",
      "external_record_id": "tianshu:plan-sign:<run>-019",
      "external_confirmed_at": "2026-09-29T17:16:06+08:00"
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-18>",
  "action_type": "world_confirm_period_goal",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<period-goal-1>@1",
    "version": 1,
    "event_id": "<event-21>",
    "domain_id": "<domain:eo>",
    "object_id": "<period-goal-1>",
    "revision_id": "<revision-7>",
    "on_behalf_of": {
      "principal_id": "<principal:ceo>",
      "external_record_id": "tianshu:plan-sign:<run>-019",
      "delegation_event_id": "<event-16>",
      "external_confirmed_at": "2026-09-29T09:16:06Z"
    },
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<period-goal-1>"],
    "required_assignment_ids": ["<assignment:ceo:CEO@eo>"]
  },
  "object_versions": [{"object_id": "<period-goal-1>", "object_version": 3}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:07.229278+00:00"
}
```

**代 Mission Owner 承诺 Mission（任务卡提交）**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_commit_mission",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<mission-1>", "revision_id": "<revision-13>", "expected_version": 3},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:022",
  "reason": "world-02 天枢接入示例",
  "params": {
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:card-submit:<run>-021",
      "external_confirmed_at": "2026-09-29T17:16:07+08:00"
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-19>",
  "action_type": "world_commit_mission",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<mission-1>@3",
    "version": 3,
    "event_id": "<event-22>",
    "domain_id": "<domain:eo>",
    "object_id": "<mission-1>",
    "revision_id": "<revision-13>",
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:card-submit:<run>-021",
      "delegation_event_id": "<event-18>",
      "external_confirmed_at": "2026-09-29T09:16:07Z"
    },
    "contract_version": "tkos.world/0.2",
    "responsible_through": "<mission-1>",
    "referenced_object_ids": ["<mission-1>"],
    "required_assignment_ids": ["<assignment:eo-owner:OWNER@eo>"]
  },
  "object_versions": [{"object_id": "<mission-1>", "object_version": 4}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:07.405724+00:00"
}
```

**代 E&O DRI 确认 Mission（任务卡确认）**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_confirm_mission",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<mission-1>", "revision_id": "<revision-13>", "expected_version": 4},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:024",
  "reason": "world-02 天枢接入示例",
  "params": {
    "outcome": "accepted",
    "on_behalf_of": {
      "principal_id": "<principal:eo-dri>",
      "external_record_id": "tianshu:card-confirm:<run>-023",
      "external_confirmed_at": "2026-09-29T17:16:07+08:00"
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-20>",
  "action_type": "world_confirm_mission",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<mission-1>@3",
    "version": 3,
    "event_id": "<event-23>",
    "domain_id": "<domain:eo>",
    "object_id": "<mission-1>",
    "revision_id": "<revision-13>",
    "on_behalf_of": {
      "principal_id": "<principal:eo-dri>",
      "external_record_id": "tianshu:card-confirm:<run>-023",
      "delegation_event_id": "<event-17>",
      "external_confirmed_at": "2026-09-29T09:16:07Z"
    },
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<mission-1>"],
    "required_assignment_ids": ["<assignment:eo-dri:DOMAIN_DRI@eo>"]
  },
  "object_versions": [{"object_id": "<mission-1>", "object_version": 5}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:07.519272+00:00"
}
```

**取 Mission 的事件（`since` 之后）：记录者是天枢，另有被代记的人与外部确认记录**

`GET /v1/world/objects/<mission-1>/events?since=2026-09-29T09:19:06.524637Z`，身份：`tianshu`（天枢服务主体）

返回 `200`：

```json
{
  "object_id": "<mission-1>",
  "since": "2026-09-29T09:19:06.524637Z",
  "events": [
    {
      "event_id": "<event-22>",
      "scope_id": "<scope>",
      "kind": "commit",
      "class": "gate",
      "category": null,
      "outcome": null,
      "disposition": null,
      "subject_refs": [
        {
          "block": null,
          "component": null,
          "object_id": "<mission-1>",
          "revision_id": "<revision-13>",
          "object_version": 3,
          "ref": "<mission-1>@3"
        }
      ],
      "principal": {
        "principal_id": "<principal:tianshu>",
        "principal_type": "agent",
        "display_name": "天枢服务主体"
      },
      "on_behalf_of": {"principal_id": "<principal:eo-owner>", "display_name": "E&O Mission Owner"},
      "external_confirmation": {
        "external_record_id": "tianshu:card-submit:<run>-021",
        "external_confirmed_at": "2026-09-29T09:16:07Z"
      },
      "occurred_at": "2026-09-29T09:19:07.405443Z",
      "recorded_at": "2026-09-29T09:19:07.405443Z",
      "late": false,
      "content": null,
      "detail": null,
      "action": "world_commit_mission",
      "action_id": "<receipt-19>",
      "supersedes_event_id": null,
      "corrected_by": [],
      "withdrawn_by": []
    },
    {
      "event_id": "<event-23>",
      "scope_id": "<scope>",
      "kind": "confirm",
      "class": "gate",
      "category": null,
      "outcome": "accepted",
      "disposition": null,
      "subject_refs": [
        {
          "block": null,
          "component": null,
          "object_id": "<mission-1>",
          "revision_id": "<revision-13>",
          "object_version": 3,
          "ref": "<mission-1>@3"
        }
      ],
      "principal": {
        "principal_id": "<principal:tianshu>",
        "principal_type": "agent",
        "display_name": "天枢服务主体"
      },
      "on_behalf_of": {"principal_id": "<principal:eo-dri>", "display_name": "E&O DRI"},
      "external_confirmation": {
        "external_record_id": "tianshu:card-confirm:<run>-023",
        "external_confirmed_at": "2026-09-29T09:16:07Z"
      },
      "occurred_at": "2026-09-29T09:19:07.519022Z",
      "recorded_at": "2026-09-29T09:19:07.519022Z",
      "late": false,
      "content": null,
      "detail": null,
      "action": "world_confirm_mission",
      "action_id": "<receipt-20>",
      "supersedes_event_id": null,
      "corrected_by": [],
      "withdrawn_by": []
    }
  ]
}
```


## 6. 执行计划：天枢写计划条目

接口清单第二项：天枢以 Agent 身份修订 Mission 的执行计划块（活动块，已成立后也不走门），带写入声明、不要求人工验收。计划条目的组件 id 用天枢执行事项 id，`responsible` 填执行人，只作记录，不是指派。

**天枢修订 Mission 的执行计划块：计划条目的组件 id 用执行事项 id，`responsible` 填执行人**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_revise_object",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<mission-1>", "revision_id": "<revision-13>", "expected_version": 5},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:025",
  "reason": "world-02 天枢接入示例",
  "params": {
    "payload": {
      "blocks": {
        "execution_plan": {
          "components": [
            {
              "id": "todo:<todo-uuid>",
              "type": "plan_item",
              "text": "示例：接入改动（天枢执行事项）",
              "attributes": {
                "responsible": "<principal:eo-ic>"
              }
            }
          ]
        }
      }
    },
    "declaration": {
      "scene": "<mission-1>@3",
      "trigger": "天枢同步执行事项到执行计划",
      "human_acceptance": {"required": false}
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-21>",
  "action_type": "world_revise_object",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<mission-1>@4",
    "version": 4,
    "event_id": "<event-24>",
    "domain_id": "<domain:eo>",
    "object_id": "<mission-1>",
    "declaration": {
      "scene": {
        "ref": "<mission-1>@3",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-13>",
        "object_version": 3
      },
      "trigger": "天枢同步执行事项到执行计划",
      "human_acceptance": {"required": false}
    },
    "revision_id": "<revision-15>",
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<period-goal-1>", "<mission-1>"],
    "required_assignment_ids": ["<assignment:tianshu:AGENT@eo>"]
  },
  "object_versions": [{"object_id": "<mission-1>", "object_version": 6}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:07.993071+00:00"
}
```


## 7. Task：建、指派与生命周期

接口清单第六、八项：E&O DRI 建 Task，`external_refs` 写同一个执行事项 id；之后天枢代 Mission Owner 指派、打回、验收、重开，代执行人开始、交付。重开要求 Task 已关闭，所以打回之后先再交付、验收一次；重复的交付与验收没有列出。

**E&O DRI 建 Task，`external_refs` 写同一个执行事项 id**

`POST /v1/actions`，身份：`eo-dri`（E&O DRI）

```json
{
  "action_type": "world_create_object",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:026",
  "reason": "world-02 天枢接入示例",
  "params": {
    "domain_id": "<domain:eo>",
    "object_type": "Task",
    "payload": {
      "title": "示例 <run> Task",
      "parent_ref": "<mission-1>@4",
      "external_refs": [{"system": "tianshu", "id": "todo:<todo-uuid>"}],
      "blocks": {
        "definition": {"text": "示例：Task 定义"},
        "acceptance": {
          "components": [
            {
              "id": "t-ac1",
              "type": "acceptance_criterion",
              "text": "示例：验收一",
              "refs": [
                "<mission-1>@4#acceptance/m-ac1"
              ]
            }
          ]
        },
        "plan": {
          "components": [
            {
              "id": "tp-1",
              "type": "plan_item",
              "text": "示例：先做一段"
            }
          ]
        }
      }
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-22>",
  "action_type": "world_create_object",
  "actor_id": "<principal:eo-dri>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<task-1>@1",
    "version": 1,
    "event_id": "<event-25>",
    "domain_id": "<domain:eo>",
    "object_id": "<task-1>",
    "revision_id": "<revision-16>",
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<mission-1>", "<task-1>"],
    "required_assignment_ids": ["<assignment:eo-dri:DOMAIN_DRI@eo>"]
  },
  "object_versions": [{"object_id": "<task-1>", "object_version": 1}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:08.554436+00:00"
}
```

**代 Mission Owner 指派执行人**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_assign",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<task-1>", "revision_id": "<revision-16>", "expected_version": 1},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:028",
  "reason": "world-02 天枢接入示例",
  "params": {
    "principal_id": "<principal:eo-ic>",
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:assign:<run>-027",
      "external_confirmed_at": "2026-09-29T17:16:08+08:00"
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-23>",
  "action_type": "world_assign",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<task-1>@2",
    "version": 2,
    "assignee": "<principal:eo-ic>",
    "event_id": "<event-26>",
    "domain_id": "<domain:eo>",
    "object_id": "<task-1>",
    "revision_id": "<revision-17>",
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:assign:<run>-027",
      "delegation_event_id": "<event-18>",
      "external_confirmed_at": "2026-09-29T09:16:08Z"
    },
    "contract_version": "tkos.world/0.2",
    "responsible_through": "<mission-1>",
    "referenced_object_ids": ["<task-1>"],
    "required_assignment_ids": ["<assignment:eo-owner:OWNER@eo>"]
  },
  "object_versions": [{"object_id": "<task-1>", "object_version": 2}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:08.982747+00:00"
}
```

**代执行人开始**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_start",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<task-1>", "revision_id": "<revision-17>", "expected_version": 2},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:030",
  "reason": "world-02 天枢接入示例",
  "params": {
    "on_behalf_of": {
      "principal_id": "<principal:eo-ic>",
      "external_record_id": "tianshu:start:<run>-029",
      "external_confirmed_at": "2026-09-29T17:16:09+08:00"
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-24>",
  "action_type": "world_start",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<task-1>@2",
    "version": 2,
    "event_id": "<event-27>",
    "domain_id": "<domain:eo>",
    "object_id": "<task-1>",
    "revision_id": "<revision-17>",
    "on_behalf_of": {
      "principal_id": "<principal:eo-ic>",
      "external_record_id": "tianshu:start:<run>-029",
      "delegation_event_id": "<event-19>",
      "external_confirmed_at": "2026-09-29T09:16:09Z"
    },
    "contract_version": "tkos.world/0.2",
    "responsible_through": "<task-1>",
    "referenced_object_ids": ["<task-1>"],
    "required_assignment_ids": ["<assignment:eo-ic:IC@eo>"]
  },
  "object_versions": [{"object_id": "<task-1>", "object_version": 3}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:09.133522+00:00"
}
```

**代执行人交付（执行事项完成）**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_deliver",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<task-1>", "revision_id": "<revision-17>", "expected_version": 3},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:032",
  "reason": "world-02 天枢接入示例",
  "params": {
    "content": {"text": "执行事项完成"},
    "on_behalf_of": {
      "principal_id": "<principal:eo-ic>",
      "external_record_id": "tianshu:deliver:<run>-031",
      "external_confirmed_at": "2026-09-29T17:16:09+08:00"
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-25>",
  "action_type": "world_deliver",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<task-1>@2",
    "version": 2,
    "event_id": "<event-28>",
    "domain_id": "<domain:eo>",
    "object_id": "<task-1>",
    "revision_id": "<revision-17>",
    "on_behalf_of": {
      "principal_id": "<principal:eo-ic>",
      "external_record_id": "tianshu:deliver:<run>-031",
      "delegation_event_id": "<event-19>",
      "external_confirmed_at": "2026-09-29T09:16:09Z"
    },
    "contract_version": "tkos.world/0.2",
    "responsible_through": "<task-1>",
    "referenced_object_ids": ["<task-1>"],
    "required_assignment_ids": ["<assignment:eo-ic:IC@eo>"]
  },
  "object_versions": [{"object_id": "<task-1>", "object_version": 4}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:09.512028+00:00"
}
```

**代 Mission Owner 打回，Task 进入调整中**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_reject",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<task-1>", "revision_id": "<revision-17>", "expected_version": 4},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:034",
  "reason": "world-02 天枢接入示例",
  "params": {
    "content": {"text": "打回：缺验收材料"},
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:reject:<run>-033",
      "external_confirmed_at": "2026-09-29T17:16:09+08:00"
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-26>",
  "action_type": "world_reject",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<task-1>@2",
    "version": 2,
    "event_id": "<event-29>",
    "domain_id": "<domain:eo>",
    "object_id": "<task-1>",
    "revision_id": "<revision-17>",
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:reject:<run>-033",
      "delegation_event_id": "<event-18>",
      "external_confirmed_at": "2026-09-29T09:16:09Z"
    },
    "contract_version": "tkos.world/0.2",
    "responsible_through": "<mission-1>",
    "referenced_object_ids": ["<task-1>"],
    "required_assignment_ids": ["<assignment:eo-owner:OWNER@eo>"]
  },
  "object_versions": [{"object_id": "<task-1>", "object_version": 5}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:09.678456+00:00"
}
```

**代 Mission Owner 验收通过，Task 进入已关闭**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_accept",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<task-1>", "revision_id": "<revision-17>", "expected_version": 6},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:038",
  "reason": "world-02 天枢接入示例",
  "params": {
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:accept:<run>-037",
      "external_confirmed_at": "2026-09-29T17:16:10+08:00"
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-28>",
  "action_type": "world_accept",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<task-1>@2",
    "version": 2,
    "event_id": "<event-31>",
    "domain_id": "<domain:eo>",
    "object_id": "<task-1>",
    "revision_id": "<revision-17>",
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:accept:<run>-037",
      "delegation_event_id": "<event-18>",
      "external_confirmed_at": "2026-09-29T09:16:10Z"
    },
    "contract_version": "tkos.world/0.2",
    "responsible_through": "<mission-1>",
    "referenced_object_ids": ["<task-1>"],
    "required_assignment_ids": ["<assignment:eo-owner:OWNER@eo>"]
  },
  "object_versions": [{"object_id": "<task-1>", "object_version": 7}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:10.240693+00:00"
}
```

**代 Mission Owner 重开，Task 回到进行中**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_reopen",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<task-1>", "revision_id": "<revision-17>", "expected_version": 7},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:040",
  "reason": "world-02 天枢接入示例",
  "params": {
    "content": {"text": "重开：验收后发现遗漏"},
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:reopen:<run>-039",
      "external_confirmed_at": "2026-09-29T17:16:10+08:00"
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-29>",
  "action_type": "world_reopen",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<task-1>@2",
    "version": 2,
    "event_id": "<event-32>",
    "domain_id": "<domain:eo>",
    "object_id": "<task-1>",
    "revision_id": "<revision-17>",
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:reopen:<run>-039",
      "delegation_event_id": "<event-18>",
      "external_confirmed_at": "2026-09-29T09:16:10Z"
    },
    "contract_version": "tkos.world/0.2",
    "responsible_through": "<mission-1>",
    "referenced_object_ids": ["<task-1>"],
    "required_assignment_ids": ["<assignment:eo-owner:OWNER@eo>"]
  },
  "object_versions": [{"object_id": "<task-1>", "object_version": 8}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:10.640206+00:00"
}
```


## 8. 执行计划：Co-Agent 再加一条

接口清单第二项：E&O 的 Co-Agent 直接修订同一个执行计划块。组件按 id 合并：补丁里只有新条目，天枢写的那条保留。

**Co-Agent 修订 Mission 的执行计划块：补丁里只有新的一条**

`POST /v1/actions`，身份：`eo-coagent`（E&O Co-Agent）

```json
{
  "action_type": "world_revise_object",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<mission-1>", "revision_id": "<revision-15>", "expected_version": 6},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:045",
  "reason": "world-02 天枢接入示例",
  "params": {
    "payload": {
      "blocks": {
        "execution_plan": {
          "text": "按周会结论更新",
          "components": [
            {
              "id": "plan:demo-<run>-1",
              "type": "plan_item",
              "text": "示例：联调与验收",
              "attributes": {
                "responsible": "<principal:eo-owner>"
              }
            }
          ]
        }
      }
    },
    "declaration": {
      "scene": "<mission-1>@4",
      "trigger": "按周会结论更新执行计划",
      "human_acceptance": {"required": false}
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-32>",
  "action_type": "world_revise_object",
  "actor_id": "<principal:eo-coagent>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "ref": "<mission-1>@5",
    "version": 5,
    "event_id": "<event-35>",
    "domain_id": "<domain:eo>",
    "object_id": "<mission-1>",
    "declaration": {
      "scene": {
        "ref": "<mission-1>@4",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-15>",
        "object_version": 4
      },
      "trigger": "按周会结论更新执行计划",
      "human_acceptance": {"required": false}
    },
    "revision_id": "<revision-18>",
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<period-goal-1>", "<mission-1>"],
    "required_assignment_ids": ["<assignment:eo-coagent:AGENT@eo>"]
  },
  "object_versions": [{"object_id": "<mission-1>", "object_version": 7}],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:11.623055+00:00"
}
```


## 9. 议题：提出、路由、承接、处置、退回形成

接口清单第七、八项：五个动作都不带 `target`，以 `params.issue_ref`（快照 `issues` 块里问题组件的组件引用）指明问题。天枢以自己的身份提出、路由（带写入声明）；承接、退回形成与处置由天枢按承接人第 5 节登记的议题族委托代记，不带写入声明。委托的域按问题所在的域（主受影响对象的域）判。

**天枢提出问题（写入声明必带）**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_raise_issue",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:046",
  "reason": "world-02 天枢接入示例",
  "params": {
    "issue_ref": "<snapshot-1>@1#issues/issue:demo-<run>-1",
    "content": {"text": "每周同步提出"},
    "declaration": {
      "scene": "<mission-1>@5",
      "trigger": "每周同步发现的问题",
      "human_acceptance": {"required": false}
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-33>",
  "action_type": "world_raise_issue",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "issue": {
      "status": "pending_routing",
      "component_id": "issue:demo-<run>-1",
      "display_name": "待路由",
      "primary_affected_object_id": "<mission-1>"
    },
    "event_id": "<event-36>",
    "domain_id": "<domain:eo>",
    "declaration": {
      "scene": {
        "ref": "<mission-1>@5",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-18>",
        "object_version": 5
      },
      "trigger": "每周同步发现的问题",
      "human_acceptance": {"required": false}
    },
    "subject_refs": [
      {
        "ref": "<snapshot-1>@1#issues/issue:demo-<run>-1",
        "block": "issues",
        "component": "issue:demo-<run>-1",
        "object_id": "<snapshot-1>",
        "revision_id": "<revision-14>",
        "object_version": 1
      },
      {
        "ref": "<mission-1>@5",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-18>",
        "object_version": 5
      }
    ],
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<mission-1>", "<snapshot-1>"],
    "required_assignment_ids": ["<assignment:tianshu:AGENT@eo>"]
  },
  "object_versions": [],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:12.073809+00:00"
}
```

**天枢把问题路由给 E&O Mission Owner**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_route_issue",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:047",
  "reason": "world-02 天枢接入示例",
  "params": {
    "issue_ref": "<snapshot-1>@1#issues/issue:demo-<run>-1",
    "to_principal_id": "<principal:eo-owner>",
    "declaration": {
      "scene": "<mission-1>@5",
      "trigger": "每周同步发现的问题",
      "human_acceptance": {"required": false}
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-34>",
  "action_type": "world_route_issue",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "issue": {
      "status": "routed",
      "component_id": "issue:demo-<run>-1",
      "display_name": "已路由",
      "primary_affected_object_id": "<mission-1>"
    },
    "detail": {"to_principal_id": "<principal:eo-owner>"},
    "event_id": "<event-37>",
    "domain_id": "<domain:eo>",
    "declaration": {
      "scene": {
        "ref": "<mission-1>@5",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-18>",
        "object_version": 5
      },
      "trigger": "每周同步发现的问题",
      "human_acceptance": {"required": false}
    },
    "subject_refs": [
      {
        "ref": "<snapshot-1>@1#issues/issue:demo-<run>-1",
        "block": "issues",
        "component": "issue:demo-<run>-1",
        "object_id": "<snapshot-1>",
        "revision_id": "<revision-14>",
        "object_version": 1
      },
      {
        "ref": "<mission-1>@5",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-18>",
        "object_version": 5
      }
    ],
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<mission-1>", "<snapshot-1>"],
    "required_assignment_ids": ["<assignment:tianshu:AGENT@eo>"]
  },
  "object_versions": [],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:12.149104+00:00"
}
```

**代承接人承接（不带写入声明）**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_own_issue",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:049",
  "reason": "world-02 天枢接入示例",
  "params": {
    "issue_ref": "<snapshot-1>@1#issues/issue:demo-<run>-1",
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:own_issue:<run>-048",
      "external_confirmed_at": "2026-09-29T17:16:12+08:00"
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-35>",
  "action_type": "world_own_issue",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "issue": {
      "status": "owned",
      "component_id": "issue:demo-<run>-1",
      "display_name": "已承接",
      "primary_affected_object_id": "<mission-1>"
    },
    "event_id": "<event-38>",
    "domain_id": "<domain:eo>",
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:own_issue:<run>-048",
      "delegation_event_id": "<event-18>",
      "external_confirmed_at": "2026-09-29T09:16:12Z"
    },
    "subject_refs": [
      {
        "ref": "<snapshot-1>@1#issues/issue:demo-<run>-1",
        "block": "issues",
        "component": "issue:demo-<run>-1",
        "object_id": "<snapshot-1>",
        "revision_id": "<revision-14>",
        "object_version": 1
      },
      {
        "ref": "<mission-1>@5",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-18>",
        "object_version": 5
      }
    ],
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<mission-1>", "<snapshot-1>"],
    "required_assignment_ids": ["<assignment:eo-owner:IC@eo>"]
  },
  "object_versions": [],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:12.234982+00:00"
}
```

**代承接人退回形成**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_return_issue",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:051",
  "reason": "world-02 天枢接入示例",
  "params": {
    "issue_ref": "<snapshot-1>@1#issues/issue:demo-<run>-1",
    "content": {"text": "核心问题要先补齐再路由"},
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:return_issue:<run>-050",
      "external_confirmed_at": "2026-09-29T17:16:12+08:00"
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-36>",
  "action_type": "world_return_issue",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "issue": {
      "status": "forming",
      "component_id": "issue:demo-<run>-1",
      "display_name": "形成中",
      "primary_affected_object_id": "<mission-1>"
    },
    "event_id": "<event-39>",
    "domain_id": "<domain:eo>",
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:return_issue:<run>-050",
      "delegation_event_id": "<event-18>",
      "external_confirmed_at": "2026-09-29T09:16:12Z"
    },
    "subject_refs": [
      {
        "ref": "<snapshot-1>@1#issues/issue:demo-<run>-1",
        "block": "issues",
        "component": "issue:demo-<run>-1",
        "object_id": "<snapshot-1>",
        "revision_id": "<revision-14>",
        "object_version": 1
      },
      {
        "ref": "<mission-1>@5",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-18>",
        "object_version": 5
      }
    ],
    "contract_version": "tkos.world/0.2",
    "responsible_through": "<mission-1>",
    "referenced_object_ids": ["<mission-1>", "<snapshot-1>"],
    "required_assignment_ids": ["<assignment:eo-owner:OWNER@eo>"]
  },
  "object_versions": [],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:12.327581+00:00"
}
```

**代承接人处置（本层处理，理由写在 `content.text`）**

`POST /v1/actions`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_dispose_issue",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:057",
  "reason": "world-02 天枢接入示例",
  "params": {
    "issue_ref": "<snapshot-1>@1#issues/issue:demo-<run>-1",
    "disposition": "current_layer_action",
    "content": {"text": "本层处理：在执行计划里加一条同步指派的计划条目"},
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:dispose_issue:<run>-056",
      "external_confirmed_at": "2026-09-29T17:16:12+08:00"
    }
  }
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-40>",
  "action_type": "world_dispose_issue",
  "actor_id": "<principal:tianshu>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "issue": {
      "status": "disposed",
      "component_id": "issue:demo-<run>-1",
      "display_name": "已处置",
      "primary_affected_object_id": "<mission-1>"
    },
    "event_id": "<event-43>",
    "domain_id": "<domain:eo>",
    "disposition": "current_layer_action",
    "on_behalf_of": {
      "principal_id": "<principal:eo-owner>",
      "external_record_id": "tianshu:dispose_issue:<run>-056",
      "delegation_event_id": "<event-18>",
      "external_confirmed_at": "2026-09-29T09:16:12Z"
    },
    "subject_refs": [
      {
        "ref": "<snapshot-1>@1#issues/issue:demo-<run>-1",
        "block": "issues",
        "component": "issue:demo-<run>-1",
        "object_id": "<snapshot-1>",
        "revision_id": "<revision-14>",
        "object_version": 1
      },
      {
        "ref": "<mission-1>@5",
        "block": null,
        "component": null,
        "object_id": "<mission-1>",
        "revision_id": "<revision-18>",
        "object_version": 5
      }
    ],
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<mission-1>", "<snapshot-1>"],
    "required_assignment_ids": ["<assignment:eo-owner:IC@eo>"]
  },
  "object_versions": [],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:12.645572+00:00"
}
```

**取 Mission 的事件（`since` 之后）：议题事件，代记的带被代记的人与外部确认记录**

`GET /v1/world/objects/<mission-1>/events?since=2026-09-29T09:19:11.957513Z`，身份：`tianshu`（天枢服务主体）

返回 `200`：

```json
{
  "object_id": "<mission-1>",
  "since": "2026-09-29T09:19:11.957513Z",
  "events": [
    {
      "event_id": "<event-36>",
      "scope_id": "<scope>",
      "kind": "issue.raised",
      "class": "record",
      "category": null,
      "outcome": null,
      "disposition": null,
      "subject_refs": [
        {
          "block": "issues",
          "component": "issue:demo-<run>-1",
          "object_id": "<snapshot-1>",
          "revision_id": "<revision-14>",
          "object_version": 1,
          "ref": "<snapshot-1>@1#issues/issue:demo-<run>-1"
        },
        {
          "block": null,
          "component": null,
          "object_id": "<mission-1>",
          "revision_id": "<revision-18>",
          "object_version": 5,
          "ref": "<mission-1>@5"
        }
      ],
      "principal": {
        "principal_id": "<principal:tianshu>",
        "principal_type": "agent",
        "display_name": "天枢服务主体"
      },
      "on_behalf_of": null,
      "external_confirmation": null,
      "occurred_at": "2026-09-29T09:19:12.073512Z",
      "recorded_at": "2026-09-29T09:19:12.073512Z",
      "late": false,
      "content": {"refs": [], "text": "每周同步提出", "artifacts": [], "components": []},
      "detail": null,
      "action": "world_raise_issue",
      "action_id": "<receipt-33>",
      "supersedes_event_id": null,
      "corrected_by": [],
      "withdrawn_by": []
    },
    {
      "event_id": "<event-37>",
      "scope_id": "<scope>",
      "kind": "issue.routed",
      "class": "record",
      "category": null,
      "outcome": null,
      "disposition": null,
      "subject_refs": [
        {
          "block": "issues",
          "component": "issue:demo-<run>-1",
          "object_id": "<snapshot-1>",
          "revision_id": "<revision-14>",
          "object_version": 1,
          "ref": "<snapshot-1>@1#issues/issue:demo-<run>-1"
        },
        {
          "block": null,
          "component": null,
          "object_id": "<mission-1>",
          "revision_id": "<revision-18>",
          "object_version": 5,
          "ref": "<mission-1>@5"
        }
      ],
      "principal": {
        "principal_id": "<principal:tianshu>",
        "principal_type": "agent",
        "display_name": "天枢服务主体"
      },
      "on_behalf_of": null,
      "external_confirmation": null,
      "occurred_at": "2026-09-29T09:19:12.148758Z",
      "recorded_at": "2026-09-29T09:19:12.148758Z",
      "late": false,
      "content": null,
      "detail": {"to_principal_id": "<principal:eo-owner>"},
      "action": "world_route_issue",
      "action_id": "<receipt-34>",
      "supersedes_event_id": null,
      "corrected_by": [],
      "withdrawn_by": []
    },
    {
      "event_id": "<event-38>",
      "scope_id": "<scope>",
      "kind": "issue.owned",
      "class": "record",
      "category": null,
      "outcome": null,
      "disposition": null,
      "subject_refs": [
        {
          "block": "issues",
          "component": "issue:demo-<run>-1",
          "object_id": "<snapshot-1>",
          "revision_id": "<revision-14>",
          "object_version": 1,
          "ref": "<snapshot-1>@1#issues/issue:demo-<run>-1"
        },
        {
          "block": null,
          "component": null,
          "object_id": "<mission-1>",
          "revision_id": "<revision-18>",
          "object_version": 5,
          "ref": "<mission-1>@5"
        }
      ],
      "principal": {
        "principal_id": "<principal:tianshu>",
        "principal_type": "agent",
        "display_name": "天枢服务主体"
      },
      "on_behalf_of": {"principal_id": "<principal:eo-owner>", "display_name": "E&O Mission Owner"},
      "external_confirmation": {
        "external_record_id": "tianshu:own_issue:<run>-048",
        "external_confirmed_at": "2026-09-29T09:16:12Z"
      },
      "occurred_at": "2026-09-29T09:19:12.234595Z",
      "recorded_at": "2026-09-29T09:19:12.234595Z",
      "late": false,
      "content": null,
      "detail": null,
      "action": "world_own_issue",
      "action_id": "<receipt-35>",
      "supersedes_event_id": null,
      "corrected_by": [],
      "withdrawn_by": []
    },
    "……（截去 5 项）"
  ]
}
```


## 10. 取上下文

接口清单第十项：从 Mission 出发取上下文；有门的对象另带「形成时带入」。返回很长，下面截短了。

**从 Mission 出发取上下文**

`POST /v1/world/objects/<mission-1>/context`，身份：`tianshu`（天枢服务主体）

```json
{"question": "这个 Mission 为什么做、做什么、谁负责、现在怎样？"}
```

返回 `200`：

```json
{
  "contract_version": "tkos.world/0.2",
  "context_pack_id": "<context-pack-1>",
  "created_at": "2026-09-29T09:19:13.483117Z",
  "object_id": "<mission-1>",
  "question": "这个 Mission 为什么做、做什么、谁负责、现在怎样？",
  "context_pack": {
    "contract_version": "tkos.world/0.2",
    "question": "这个 Mission 为什么做、做什么、谁负责、现在怎样？",
    "start": "<mission-1>@5",
    "layers": [
      {
        "level": 0,
        "object": {
          "object_id": "<mission-1>",
          "object_type": "Mission",
          "type_display_name": "Mission",
          "title": "示例 <run> Mission",
          "version": 5,
          "ref": "<mission-1>@5",
          "pinned": {
            "object_id": "<mission-1>",
            "object_version": 5,
            "revision_id": "<revision-18>",
            "block": null,
            "component": null,
            "ref": "<mission-1>@5"
          },
          "lifecycle": {
            "status": "established",
            "display_name": "已成立",
            "event_id": "<event-23>"
          },
          "formal": true,
          "responsible": {
            "roles": {"human": "OWNER"},
            "source": "attribute",
            "principals": [
              {
                "principal_id": "<principal:eo-owner>",
                "principal_type": "human",
                "display_name": "E&O Mission Owner"
              }
            ]
          }
        },
        "blocks": [
          {
            "id": "definition",
            "display_name": "定义",
            "kind": "definition",
            "class": "formal",
            "value": {
              "refs": [],
              "text": "示例：Mission 定义",
              "artifacts": [],
              "components": []
            },
            "empty": false,
            "text": "示例：Mission 定义",
            "components": [],
            "ref": "<mission-1>@5#definition",
            "pinned": {
              "object_id": "<mission-1>",
              "object_version": 5,
              "revision_id": "<revision-18>",
              "block": "definition",
              "component": null,
              "ref": "<mission-1>@5#definition"
            }
          },
          "……（截去 4 项）"
        ],
        "relations": [],
        "referenced_by": [],
        "hop": null,
        "state": {
          "object_id": "<snapshot-1>",
          "object_type": "StateSnapshot",
          "category": {"id": "time_record", "display_name": "时间记录"},
          "version": 1,
          "revision_id": "<revision-14>",
          "ref": "<snapshot-1>@1",
          "title": "示例 <run> 执行状态 2026-W40",
          "subject_ref": {
            "block": null,
            "component": null,
            "object_id": "<mission-1>",
            "revision_id": "<revision-13>",
            "object_version": 3,
            "ref": "<mission-1>@3"
          },
          "as_of": "2026-09-29T09:18:35Z",
          "period": "2026-09",
          "generator": {
            "principal_id": "<principal:tianshu>",
            "principal_type": "agent",
            "display_name": "天枢服务主体"
          },
          "source_event_refs": [
            {
              "event_id": "<event-13>",
              "ref": "event:<event-13>"
            }
          ],
          "payload_type": {"id": "execution_state", "display_name": "执行状态"},
          "blocks": [
            {
              "id": "progress",
              "display_name": "进展",
              "kind": "state",
              "class": null,
              "value": {
                "refs": [],
                "text": "",
                "artifacts": [],
                "components": [
                  {
                    "id": "todo:<todo-uuid>",
                    "refs": [],
                    "text": "接入 0.2 的本周进展",
                    "type": "progress_item",
                    "scope": null,
                    "artifacts": [
                      "https://example.com/tianshu/todo/1",
                      "https://example.com/tianshu/pr/1"
                    ],
                    "attributes": {
                      "entries": [
                        {
                          "at": "2026-09-27T09:19:05Z",
                          "url": null,
                          "text": "对齐 0.2 接口变化清单",
                          "source": "web"
                        },
                        {
                          "at": "2026-09-28T09:19:05Z",
                          "url": "https://example.com/tianshu/pr/1",
                          "text": "提交接入改动",
                          "source": "github"
                        }
                      ],
                      "principal_id": "<principal:eo-owner>",
                      "principal_name": "E&O Mission Owner",
                      "external_status": "进行中"
                    },
                    "ref": "<snapshot-1>@1#progress/todo:<todo-uuid>",
                    "pinned": {
                      "object_id": "<snapshot-1>",
                      "object_version": 1,
                      "revision_id": "<revision-14>",
                      "block": "progress",
                      "component": "todo:<todo-uuid>",
                      "ref": "<snapshot-1>@1#progress/todo:<todo-uuid>"
                    }
                  }
                ]
              },
              "empty": false,
              "text": "",
              "components": [
                {
                  "id": "todo:<todo-uuid>",
                  "refs": [],
                  "text": "接入 0.2 的本周进展",
                  "type": "progress_item",
                  "scope": null,
                  "artifacts": [
                    "https://example.com/tianshu/todo/1",
                    "https://example.com/tianshu/pr/1"
                  ],
                  "attributes": {
                    "entries": [
                      {
                        "at": "2026-09-27T09:19:05Z",
                        "url": null,
                        "text": "对齐 0.2 接口变化清单",
                        "source": "web"
                      },
                      {
                        "at": "2026-09-28T09:19:05Z",
                        "url": "https://example.com/tianshu/pr/1",
                        "text": "提交接入改动",
                        "source": "github"
                      }
                    ],
                    "principal_id": "<principal:eo-owner>",
                    "principal_name": "E&O Mission Owner",
                    "external_status": "进行中"
                  },
                  "ref": "<snapshot-1>@1#progress/todo:<todo-uuid>",
                  "pinned": {
                    "object_id": "<snapshot-1>",
                    "object_version": 1,
                    "revision_id": "<revision-14>",
                    "block": "progress",
                    "component": "todo:<todo-uuid>",
                    "ref": "<snapshot-1>@1#progress/todo:<todo-uuid>"
                  }
                }
              ],
              "ref": "<snapshot-1>@1#progress",
              "pinned": {
                "object_id": "<snapshot-1>",
                "object_version": 1,
                "revision_id": "<revision-14>",
                "block": "progress",
                "component": null,
                "ref": "<snapshot-1>@1#progress"
              }
            },
            "……（截去 3 项）"
          ],
          "unconfirmed": true,
          "pinned": {
            "object_id": "<snapshot-1>",
            "object_version": 1,
            "revision_id": "<revision-14>",
            "block": null,
            "component": null,
            "ref": "<snapshot-1>@1"
          }
        },
        "events": [
          {
            "event_id": "<event-43>",
            "scope_id": "<scope>",
            "kind": "issue.disposed",
            "class": "record",
            "category": null,
            "outcome": null,
            "disposition": "current_layer_action",
            "subject_refs": [
              {
                "block": "issues",
                "component": "issue:demo-<run>-1",
                "object_id": "<snapshot-1>",
                "revision_id": "<revision-14>",
                "object_version": 1,
                "ref": "<snapshot-1>@1#issues/issue:demo-<run>-1"
              },
              {
                "block": null,
                "component": null,
                "object_id": "<mission-1>",
                "revision_id": "<revision-18>",
                "object_version": 5,
                "ref": "<mission-1>@5"
              }
            ],
            "principal": {
              "principal_id": "<principal:tianshu>",
              "principal_type": "agent",
              "display_name": "天枢服务主体"
            },
            "on_behalf_of": {
              "principal_id": "<principal:eo-owner>",
              "display_name": "E&O Mission Owner"
            },
            "external_confirmation": {
              "external_record_id": "tianshu:dispose_issue:<run>-056",
              "external_confirmed_at": "2026-09-29T09:16:12Z"
            },
            "occurred_at": "2026-09-29T09:19:12.645271Z",
            "recorded_at": "2026-09-29T09:19:12.645271Z",
            "late": false,
            "content": {
              "refs": [],
              "text": "本层处理：在执行计划里加一条同步指派的计划条目",
              "artifacts": [],
              "components": []
            },
            "detail": null,
            "action": "world_dispose_issue",
            "action_id": "<receipt-40>",
            "supersedes_event_id": null,
            "corrected_by": [],
            "withdrawn_by": [],
            "ref": "event:<event-43>",
            "assignee": null
          },
          "……（截去 11 项）"
        ]
      },
      "……（截去 5 项）"
    ],
    "carried": {"issues": []},
    "markdown": "# 上下文\n\n问题：这个 Mission 为什么做、做什么、谁负责、现在怎样？\n\n出发对象：`<mission-1>@5`\n\n## 六问指引\n按问题给出处，内容在下文各节。\n- 为什么：周期目标 `<period-goal-1>@1#outcome`、`<period-goal-1>@1#acceptance` → 长期目标 `<long-term-goal-2>@1#outcome` → 公司级长期目标 `<long-term-goal-1>@1#measures` → 责任单元 `<unit:eo>@2#definition` → 战略 `<strategy>@1#responsibility_structure` → 公司 `<company>@1#identity`\n- 做什么：当前对象 `<mission-1>@5#definition`、`<mission-1>@5#acceptance`、`<mission-1>@5#play`、`<mission-1>@5#execution_plan`\n- 谁负责：当前对象 `<mission-1>@5`：E&O Mission Owner，指派事件 `event:<event-8>`（2026-09-29T09:19:04.745479Z，E&O DRI 指派给 E&O Mission Owner）\n- 现在怎样：当前对象 已成立（事件 `event:<event-23>`）；最新快照 当前对象 `<snapshot-1>@1`（快照都未经确认）\n- 发生了什么：窗口内没有外部事件；另有 12 条门、生命周期与其余记录事件\n- 凭什么：验收标准与约束 `<mission-1>@5#acceptance`、`<period-goal-1>@1#acceptance`；上层已确认 周期目标 `<period-goal-1>@1`、长期目标 `<long-term-goal-2>@1`、公司级长期目标 `<long-term-goal-1>@1`；文档链接在快照 `<snapshot-1>@1#materials`\n\n## 为什么\n\n### 周期目标·结果 `<period-goal-1>@1#outcome`\n- 结果 `<period-goal-1>@1#outcome/pg-o1`：示例：本期结果\n  引用：`<long-term-goal-2>@1#outcome/uo-1`\n\n### 周期目标·实现逻辑 `<period-goal-1>@1#realization_logic`\n当前没有实现逻辑\n\n### 周期目标·验收标准 `<period-goal-1>@1#acceptance`\n- 验收条件 `<period-goal-1>@1#acceptance/pg-ac1`：示例：本期验收\n\n### 长期目标·结果 `<long-term-goal-2>@1#outcome`\n- 结果 `<long-term-goal-2>@1#outcome/uo-1`：示例：结果一\n  引用：`<long-term-goal-1>@1#measures/sc-1`\n\n### 长期目标·衡量 `<long-term-goal-2>@1#measures`\n当前没有衡量\n\n### 沿 goal_ref 多取一跳：公司级长期目标《示例 <run> 公司长期目标》 `<long-term-goal-1>@1`\n生命周期：已确认（事件 `event:<event-3>`）\n责任人（来自角色 CEO）：CEO\n正……（截去 4054 字）"
  },
  "plan": {
    "walked": [
      {
        "from": "<mission-1>@5",
        "field": "goal_ref",
        "pinned": "<period-goal-1>@1",
        "read": "<period-goal-1>@1"
      },
      "……（截去 4 项）"
    ],
    "hops": [
      {
        "from": "<long-term-goal-2>@1",
        "field": "goal_ref",
        "pinned": "<long-term-goal-1>@1",
        "read": "<long-term-goal-1>@1"
      }
    ],
    "shown_not_followed": [],
    "taken": [{"kind": "block", "level": 1, "key": "block:<period-goal-1>@1#outcome"}, "……（截去 36 项）"],
    "trimmed": [
      {"kind": "event", "level": 0, "key": "event:<event-22>", "reason": "over_level_cap"},
      "……（截去 5 项）"
    ],
    "state_and_events_from_levels": [0],
    "over_budget": false
  },
  "coverage": {
    "why": {
      "question": "为什么",
      "answered": true,
      "evidence": [{"ref": "<period-goal-1>@1#outcome/pg-o1"}, "……（截去 8 项）"],
      "gap": null
    },
    "what": {
      "question": "做什么",
      "answered": true,
      "evidence": [{"ref": "<mission-1>@5#definition"}, "……（截去 2 项）"],
      "gap": null
    },
    "who": {
      "question": "谁负责",
      "answered": true,
      "evidence": [{"ref": "<mission-1>@5"}],
      "gap": null
    },
    "now": {
      "question": "现在怎样",
      "answered": true,
      "evidence": [{"ref": "<snapshot-1>@1"}],
      "gap": null
    },
    "happened": {
      "question": "发生了什么",
      "answered": true,
      "evidence": [{"ref": "event:<event-43>"}, "……（截去 11 项）"],
      "gap": null
    },
    "basis": {
      "question": "凭什么",
      "answered": true,
      "evidence": [{"ref": "<period-goal-1>@1#outcome/pg-o1"}, "……（截去 4 项）"],
      "gap": null
    }
  },
  "budget": {
    "max_chars": 100000,
    "max_events_per_object": 10,
    "recent_days": 30,
    "window_start": "2026-08-30T09:19:13.465312Z",
    "used_chars": 7926,
    "estimated_tokens": 3963,
    "over_budget": false
  }
}
```


## 11. 典型错误

错误都在 prepare 就返回，库里不留任何记录。返回形状是 `{"error": {"code", "message"}}`。

**块放在 payload 顶层（应在 `payload.blocks` 里）：422**

`POST /v1/actions/prepare`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_refresh_state",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:058",
  "reason": "world-02 天枢接入示例",
  "params": {
    "declaration": {
      "scene": "<mission-1>@5",
      "trigger": "天枢每周同步",
      "human_acceptance": {"required": false}
    },
    "payload": {
      "title": "示例 <run> 执行状态（块放错）",
      "subject_ref": "<mission-1>@5",
      "as_of": "2026-09-29T17:19:09+08:00",
      "period": "2026-09",
      "payload_type": "execution_state",
      "source_event_refs": ["event:<event-13>"],
      "progress": {"text": "块应当放在 payload.blocks 里"}
    }
  }
}
```

返回 `422`：

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "The snapshot does not satisfy the world 0.2 state shell or its payload type."
  }
}
```

**Agent 建对象：403（建对象不在 Agent 面上）**

`POST /v1/actions/prepare`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_create_object",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:059",
  "reason": "world-02 天枢接入示例",
  "params": {
    "domain_id": "<domain:eo>",
    "object_type": "Task",
    "payload": {"title": "示例 <run> 天枢建的 Task", "parent_ref": "<mission-1>@5", "blocks": {}},
    "declaration": {
      "scene": "<mission-1>@5",
      "trigger": "天枢每周同步",
      "human_acceptance": {"required": false}
    }
  }
}
```

返回 `403`：

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "An Agent does not create objects; creation is not on the Agent face."
  }
}
```

**天枢改责任单元的外部引用：403（单元没有门，天枢不是它的责任人；单元的外部引用由 E&O 写）**

`POST /v1/actions/prepare`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_revise_object",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<unit:eo>", "revision_id": "<revision-3>", "expected_version": 3},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:060",
  "reason": "world-02 天枢接入示例",
  "params": {
    "payload": {"external_refs": [{"system": "tianshu", "id": "capability:05"}]},
    "declaration": {
      "scene": "<unit:eo>@2",
      "trigger": "天枢能力域关联本体责任单元",
      "human_acceptance": {"required": false}
    }
  }
}
```

返回 `403`：

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "Only a responsible person up the spine writes this object; the activity blocks and attributes of a gated object also a responsible below it or an Agent of its domain."
  }
}
```

**委托不含该族：E&O DRI 只登记了门，天枢代他指派 Mission Owner 是 403**

`POST /v1/actions/prepare`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_assign",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<mission-1>", "revision_id": "<revision-18>", "expected_version": 7},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:062",
  "reason": "world-02 天枢接入示例",
  "params": {
    "principal_id": "<principal:eo-owner>",
    "on_behalf_of": {
      "principal_id": "<principal:eo-dri>",
      "external_record_id": "tianshu:assign-owner:<run>-061",
      "external_confirmed_at": "2026-09-29T17:16:14+08:00"
    }
  }
}
```

返回 `403`：

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "No delegation in force lets this service principal record this family of actions in this domain on behalf of that person."
  }
}
```

**同一外部引用挂到第二个对象：409（错误信息写出已有这一对的对象）**

`POST /v1/actions/prepare`，身份：`tianshu`（天枢服务主体）

```json
{
  "action_type": "world_revise_object",
  "contract_version": "tkos.world/0.2",
  "target": {"object_id": "<period-goal-1>", "revision_id": "<revision-7>", "expected_version": 3},
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:063",
  "reason": "world-02 天枢接入示例",
  "params": {
    "payload": {"external_refs": [{"system": "tianshu", "id": "mission:demo-<run>"}]},
    "declaration": {
      "scene": "<period-goal-1>@1",
      "trigger": "天枢 Mission 关联本体",
      "human_acceptance": {"required": false}
    }
  }
}
```

返回 `409`：

```json
{
  "error": {
    "code": "INVALID_STATE",
    "message": "The external reference (tianshu, mission:demo-<run>) already points to object <mission-1> in this scope."
  }
}
```


## 收尾：撤销委托

委托人本人撤销委托，引用登记那条事件，即时生效。每个人的撤销请求形状相同，只列第一条。

**CEO 本人撤销委托**

`POST /v1/actions`，身份：`ceo`（CEO）

```json
{
  "action_type": "world_revoke_delegation",
  "contract_version": "tkos.world/0.2",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "world-02-examples:<run>:064",
  "reason": "world-02 天枢接入示例",
  "params": {"delegation_event_id": "<event-16>"}
}
```

返回 `200`：

```json
{
  "receipt_id": "<receipt-41>",
  "action_type": "world_revoke_delegation",
  "actor_id": "<principal:ceo>",
  "auth_epoch": 4,
  "status": "committed",
  "result": {
    "detail": {"delegation_event_id": "<event-16>"},
    "event_id": "<event-45>",
    "domain_id": "<domain:company>",
    "subject_refs": [
      {
        "ref": "<company>@1",
        "block": null,
        "component": null,
        "object_id": "<company>",
        "revision_id": "<revision-4>",
        "object_version": 1
      }
    ],
    "contract_version": "tkos.world/0.2",
    "referenced_object_ids": ["<company>"],
    "required_assignment_ids": []
  },
  "object_versions": [],
  "effect_task_ids": [],
  "recorded_at": "2026-09-29T09:19:14.787460+00:00"
}
```
