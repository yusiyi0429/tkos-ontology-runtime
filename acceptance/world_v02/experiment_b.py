"""tkos.world/0.2 独立验收的对照实验 B（票 #66）：两条线在隔离库上播种，按预置的冒烟脚本推到 Mission 关闭，经读投影与
只读 SQL 核对。

放在撤销 CEO 指派之后跑，不碰前面场景的 scope：Task-only 线与 Task+Activity 线各新开一个随机 tenant 的 scope（按
experiments/world_v02/b_spec.json 的角色名单，owner SQL 播种身份与凭证，控制面 CLI 装 0.2），用实验本身的代码
（b_seed、b_drive、b_observe，打本次起的 API）播种、读回核对、驱动、取证与算观测，与实例上跑的是同一段代码。

- 播种：两条线读回都照冒烟的 b_smoke_lines.json（b_seed 的默认；Mission 已成立、Owner 与 Task 责任人照计划、执行计划每个 Task 一条带责任人的
  计划条目；Task-only 线每段是计划条目、scope 里没有 Activity；Task+Activity 线每段是已指派的 Activity），Task 之上的
  对象在两条线上同形。
- 驱动：两条线的 Mission 都已关闭（读投影 records.lifecycle），Task 都已关闭，Activity 都已关闭。
- 日志与事件对得上：运行日志里每个提交了的动作（播种与脚本），在该 scope 的事件表里恰好有一条事件——种类是登记里这个
  动作的事件种类、记录者是日志写的行动者、action_id 是日志记下的回执；scope 里的事件与回执数都正好等于日志提交的动作
  数（被拒的动作什么也没留下）；取证经取事件读到的事件覆盖日志里的全部事件。
- 表达结果：Task+Activity 线每一步都是原生；Task-only 线原生、粗粒度、被拒都有，被拒的步骤带错误码。
- 重跑：播种与驱动再跑一遍从运行日志接着做，一步不做、库里不多一条。
- 观测：五项观测由运行日志机械算出，依据的事件都在 Task+Activity 线的 scope 里；冒烟的结论与文档写的一致（执行与
  管理在 Task-only 线表达不了，Activity 留作对象）。
"""
from __future__ import annotations

import json

from experiments.world_v02 import b_drive, b_observe, b_seed
from experiments.world_v02.b_http import Line

from .fixture import REGISTRY, owner_rows

EVENT_KIND = {item['action']: item['event_kind'] for item in json.loads(REGISTRY.read_text())['actions']}


def experiment_b(book, h, url, source):
    check = book.check
    spec = json.loads(b_seed.SPEC_FILE.read_text(encoding='utf-8'))
    script = json.loads(b_seed.SMOKE_FILE.read_text(encoding='utf-8'))
    names = b_drive.LINES
    outs = {name: h.private / f'experiment-b-{name}' for name in names}
    ids = {name: b_seed.provision(h, source, spec, name, outs[name]) for name in names}
    lines = {name: Line(url, outs[name]) for name in names}

    # ---------------------------------------------------------------- 播种与读回
    logs, problems = {}, {}
    for name in names:
        logs[name] = b_seed.seed(lines[name], outs[name], name)
        problems[name] = b_seed.check(lines[name], logs[name])
    check('experiment_b_both_lines_seed_the_trial_mission_and_read_back_as_b_lines_says',
          problems == {name: [] for name in names} and all(logs[name]['seed_done'] for name in names)
          and ids['task_only']['scope_id'] != ids['task_activity']['scope_id']
          and ids['task_only']['tenant_id'] != ids['task_activity']['tenant_id'])

    def shape(name, key):
        """对象在一条线上的形状：类型、标题与各块（Task 的计划块除外，两条线就差在它）的文字与组件。"""
        business = lines[name].view(logs[name]['objects'][key]['object_id'], 'ceo')['business']
        return (business['object_type'], business['title'],
                [(block['id'], block['empty'], block['text'],
                  [(item['id'], item['type'], item['text'], item['attributes'].get('responsible') and
                    logs[name]['principals'][item['attributes']['responsible']]['key'])
                   for item in block['components']])
                 for block in business['blocks'] if not (business['object_type'] == 'Task' and block['id'] == 'plan')])
    shared = [key for key in logs['task_only']['objects'] if not key.startswith('segment:')]
    check('experiment_b_the_objects_down_to_the_tasks_have_the_same_shape_on_both_lines',
          shared == [key for key in logs['task_activity']['objects'] if not key.startswith('segment:')]
          and all(shape('task_only', key) == shape('task_activity', key) for key in shared))

    # ---------------------------------------------------------------- 按冒烟脚本驱动到 Mission 关闭
    for name in names:
        b_drive.drive(lines[name], outs[name], script)
        logs[name] = b_drive.collect(lines[name], outs[name])

    def status(name, key):
        return lines[name].view(logs[name]['objects'][key]['object_id'], 'ceo')['records']['lifecycle']['status']
    check('experiment_b_both_missions_are_closed_on_the_read_projection',
          all(status(name, logs[name]['mission']) == 'closed' for name in names))
    activities = [key for key in logs['task_activity']['objects'] if key.startswith('segment:')]
    check('experiment_b_the_tasks_are_closed_on_both_lines_and_the_activities_on_the_task_activity_line',
          all(status(name, task) == 'closed' for name in names for task in logs[name]['tasks'])
          and len(activities) == len(logs['task_activity']['segments'])
          and all(status('task_activity', key) == 'closed' for key in activities))

    # ---------------------------------------------------------------- 运行日志与事件对得上
    def committed(log):
        """日志里提交了的动作：(事件 id, 动作, 行动者主体 id, 回执 id)。"""
        principal = {item['key']: principal_id for principal_id, item in log['principals'].items()}
        rows = [(item['event_id'], item['action'], principal[item['by']], item['receipt_id'])
                for item in log['seed'].values()]
        rows += [(attempt['event_id'], attempt['action'], principal[attempt['by']], attempt['receipt_id'])
                 for record in log['steps'] for attempt in record['attempts'] if attempt['committed']]
        return rows

    def stored(name):
        scope = ids[name]['scope_id']
        events = {str(row['event_id']): row for row in owner_rows(
            h.env, scope, 'SELECT event_id, kind, principal_id, action_id FROM gov_world_events WHERE scope_id=%s',
            (scope,))}
        receipts = owner_rows(h.env, scope, 'SELECT count(*) AS n FROM gov_action_receipts WHERE scope_id=%s',
                              (scope,))[0]['n']
        return events, receipts

    matched, counted, read = True, True, True
    for name in names:
        rows, (events, receipts) = committed(logs[name]), stored(name)
        matched = matched and all(
            event_id in events and events[event_id]['kind'] == EVENT_KIND[action]
            and str(events[event_id]['principal_id']) == by and str(events[event_id]['action_id']) == receipt
            for event_id, action, by, receipt in rows)
        counted = counted and len(rows) == len(set(row[0] for row in rows)) == len(events) == receipts
        read = read and {row[0] for row in rows} <= {item['event_id'] for item in logs[name]['evidence']['events']}
    check('experiment_b_every_committed_action_in_the_logs_has_its_event_with_the_same_kind_recorder_and_receipt',
          matched)
    check('experiment_b_each_scope_holds_exactly_the_events_and_receipts_the_logs_committed', counted)
    check('experiment_b_the_evidence_read_over_http_covers_every_event_of_the_logs', read)

    # ---------------------------------------------------------------- 表达结果
    expressions = {name: b_observe.expression_counts(logs[name]) for name in names}
    rejected = [record for record in logs['task_only']['steps'] if record['expression'] == 'rejected']
    check('experiment_b_the_smoke_is_native_on_task_activity_and_mixes_native_coarse_and_rejected_on_task_only',
          expressions['task_activity']['coarse'] == expressions['task_activity']['rejected'] == 0
          and all(expressions['task_only'][kind] > 0 for kind in ('native', 'coarse', 'rejected'))
          and all(record['error_codes'] for record in rejected)
          and any(record['merged_into'] for record in logs['task_only']['steps']))

    # ---------------------------------------------------------------- 重跑不多记
    before = {name: stored(name) for name in names}
    for name in names:
        b_seed.seed(lines[name], outs[name], name)
        b_drive.drive(lines[name], outs[name], script)
    after = {name: stored(name) for name in names}
    check('experiment_b_seeding_and_driving_again_resume_from_the_log_and_write_nothing',
          all(set(before[name][0]) == set(after[name][0]) and before[name][1] == after[name][1] for name in names)
          and all(len(b_drive.load_log(outs[name])['steps']) == len(logs[name]['steps']) for name in names))

    # ---------------------------------------------------------------- 五项观测
    result = b_observe.observe(logs['task_only'], logs['task_activity'])
    events = stored('task_activity')[0]
    check('experiment_b_the_five_observations_come_out_of_the_logs_with_events_of_the_task_activity_scope',
          list(result['observations']) == list(b_observe.OBSERVATIONS)
          and all(item['occurred'] == bool(item['evidence']['steps']) for item in result['observations'].values())
          and all(event_id in events for item in result['observations'].values()
                  for event_id in item['evidence']['task_activity_event_ids']))
    verdicts = {name: item['task_only'] for name, item in result['observations'].items()}
    check('experiment_b_the_smoke_observations_match_the_documented_outcome',
          all(item['occurred'] for item in result['observations'].values())
          and verdicts == {'assign': 'coarse', 'execute': 'inexpressible', 'retry': 'coarse', 'accept': 'coarse',
                           'manage': 'inexpressible'}
          and result['conclusion']['activity'] == 'object' and result['conclusion']['because'] == ['execute', 'manage']
          and result['agent_subject']['needs'] == 'Activity')
