"""触发调整对象结构的四个条件（票 #68，规格 #46 第十节），各是一个有名字的机械检查。每个检查输出 triggered（触发）、
not_triggered（未触发）或 no_data（无数据），并写明数据源、依据与缺什么。纯函数，不连库。

判法的共同规则：已有的数据足以判定触发就判触发；判「未触发」要该有的数据都在；否则无数据，逐条列出缺的数据。

1. activity_to_component：Task-only 线达标则 Activity 降为组件。
   数据源：#66 对照实验 B 的观测结论（b_observe 输出的 observations.json，格式 OBSERVATIONS_FORMAT，经 b_conclusions
   摘成 summary.json 的 b，看 #66 口径的 conclusion.activity）；本实验参照组（固定路径）在 task_only 场景上的四项门。
   B 的结论是 component（降为组件）且参照组在 task_only 场景四项门全过，触发；B 的结论是 object，或参照组在 task_only
   场景有门没过，未触发。更严口径（粗粒度也算表达不了）的结论只列在依据里，不改判定。
2. component_ref_instability：组件引用跨修订不稳则拆块或升对象。
   数据源：读投影扫描（scan 的 component_refs）：scope 内全部对象与事件里钉着的组件引用，只看跨修订的——钉的版本早于
   目标对象的最新版；对照目标对象最新版的组件台账，组件已删（removed_in_version 不为空）或不在台账里即悬空。
   Mission 与责任单元的投影项不算钉着的引用：读取时从下级对象最新修订投影、不存（#84）。
   跨修订的组件引用至少 MIN_COMPONENT_REFS 条才判；悬空的比例超过 MAX_DANGLING 触发。
3. why_coverage_low：Why 覆盖持续偏低则改主干关系或取法。
   数据源：参照组 Why 问的取到召回（准备结果，不要模型）与回答覆盖（summary.json，要模型运行），逐场景。
   一个场景偏低：取到召回或回答覆盖低于 0.9（WHY_RECALL、WHY_COVERAGE，都等于召回门）。持续：至少 WHY_SCENARIOS
   个场景偏低。
4. issue_detached：Issue 常脱离主体快照演进则升为对象。
   数据源：读投影扫描（scan 的 issue_events）：提出之后的问题事件（路由、承接、处置、退回），看主受影响对象在事件
   发生时刻的最新状态快照（as_of 不晚于事件的发生时刻）的 issues 块里还有没有这个问题组件，没有即脱离。
   问题事件至少 MIN_ISSUE_EVENTS 条才判；脱离的比例不低于 DETACHED_SHARE 触发。

阈值是模块常量（THRESHOLDS 列出全部，写进 summary.json 与报告），已经用户确认：
- MIN_COMPONENT_REFS = 10、MAX_DANGLING = 0.1：跨修订的组件引用至少 10 条，悬空超过 0.1 触发；
- WHY_RECALL = WHY_COVERAGE = 0.9（召回门）、WHY_SCENARIOS = 3：五个场景里至少 3 个低于 0.9 触发；
- MIN_ISSUE_EVENTS = 5、DETACHED_SHARE = 0.5：问题事件至少 5 条，脱离快照的比例至少 0.5 触发；
- REFERENCE = fixed：第 1、3 条以固定路径组为参照。

对照实验 B 的两种口径（b_conclusions）：#66 的结论规则是「五项中有独立发生、且 Task-only 线表达不了（被拒）的，Activity
留作对象」，粗粒度算能表达；更严的口径把粗粒度也算表达不了——一项观测在 #66 口径下留对象，或它的 coarse 清单不为空，
就让 Activity 留作对象。两种都由 observations.json 机械算出，报告都列。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import counterexamples, metrics, spec
from .retrieval import moment

TRIGGERED, NOT_TRIGGERED, NO_DATA = 'triggered', 'not_triggered', 'no_data'
STATUS_NAMES = {TRIGGERED: '触发', NOT_TRIGGERED: '未触发', NO_DATA: '无数据'}
REFERENCE = 'fixed'  # 参照组：固定路径，即服务自己的取上下文
OBSERVATIONS_FORMAT = 'tkos-world-02-experiment-b-observations/0.1'
SMOKE_SCRIPT = json.loads((Path(__file__).with_name('b_smoke.json')).read_text(encoding='utf-8'))['id']
STRICT_RULE = ('五项中有独立发生、且 Task-only 线表达不了（被拒）或只能粗粒度表达的，Activity 留作对象；'
               '否则降为组件。')
MIN_COMPONENT_REFS, MAX_DANGLING = 10, 0.1
WHY_RECALL = WHY_COVERAGE = metrics.GATES['recall']
WHY_SCENARIOS = 3
MIN_ISSUE_EVENTS, DETACHED_SHARE = 5, 0.5


# ------------------------------------------------------------------ experiment B
def b_conclusions(observations: dict | None) -> dict | None:
    """#66 的观测结论（b_observe.observe 的输出）摘成报告与第 1 条检查用的形状：五项观测各自的发生、Task-only 线的表达与
    计数、依据的步骤与 coarse 清单，Agent 写入的主体，#66 口径的结论（粗粒度算能表达），以及由 coarse 清单机械算出的
    更严口径的结论（粗粒度也算表达不了）。没有观测为 None；格式不对抛 ValueError。"""
    if observations is None:
        return None
    if observations.get('format') != OBSERVATIONS_FORMAT \
            or (observations.get('conclusion') or {}).get('activity') not in {'component', 'object'}:
        raise ValueError(f'the observations are not {OBSERVATIONS_FORMAT} with conclusion.activity')
    items = observations['observations']
    strict = [name for name, item in items.items() if item['keeps_activity'] or item['coarse']]
    return {
        'format': observations['format'], 'script': observations['script'],
        'smoke': observations['script']['id'] == SMOKE_SCRIPT, 'expressions': observations['expressions'],
        'observations': {name: {'name': item['name'], 'question': item['question'], 'occurred': item['occurred'],
                                'task_only': item['task_only'], 'task_only_counts': item['task_only_counts'],
                                'keeps_activity': item['keeps_activity'], 'steps': item['evidence']['steps'],
                                'coarse': [{key: step[key] for key in ('step', 'writing', 'event_ids')}
                                           for step in item['coarse']]}
                         for name, item in items.items()},
        'agent_subject': observations['agent_subject']['needs'],
        'conclusion': {key: observations['conclusion'][key] for key in ('activity', 'because', 'rule')},
        'strict': {'activity': 'object' if strict else 'component', 'because': strict, 'rule': STRICT_RULE},
    }


# ------------------------------------------------------------------ scan of the read projection
def _pins(value: Any):
    """一段读投影里全部钉定的组件引用（钉定结构带 object_id、object_version 与不为空的 component）。投影项（#79）
    不算：它是读取时对着下级对象最新修订算出来的，不存；给了正文的组件是取到的内容，只列引用的下级对象是看过的引用
    （同 metrics.BASIS 的 projection，#84），都不是存着、会随修订悬空的钉定引用。"""
    if isinstance(value, dict):
        if value.get('component') and {'object_id', 'object_version', 'block'} <= value.keys():
            yield value
        for key, item in value.items():
            if key != 'projection':
                yield from _pins(item)
    elif isinstance(value, list):
        for item in value:
            yield from _pins(item)


def scan(corpus: dict[str, Any]) -> dict[str, Any]:
    """从读投影（retrieval.read_corpus 的结果）取第二、四条检查的输入。

    - component_refs：每条钉着的组件引用（按引用去重），目标对象在 scope 里的最新版、是否跨修订、组件在最新版的台账里
      是否还在（台账里 removed_in_version 为空）；
    - issue_events：提出之后的每条问题事件，问题组件、主受影响对象、事件时刻主受影响对象的最新快照，以及那条快照的
      issues 块里还有没有这个问题。"""
    latest, ledgers, snapshots = {}, {}, {}
    for view in corpus['objects']:
        if 'business' in view:
            business = view['business']
            latest[business['object_id']] = business['version']
            ledgers[business['object_id']] = {entry['id']: entry['removed_in_version']
                                              for entry in business['component_ledger']}
        else:
            latest[view['object_id']] = view['version']
            ledgers[view['object_id']] = {component['id']: None for block in view['blocks']
                                          for component in block['components']}  # 快照只有一版，没有台账
            snapshots.setdefault(view['subject_ref']['object_id'], []).append(view)
    pinned: dict[str, dict] = {}
    for item in list(_pins(corpus['objects'])) + list(_pins(corpus['events'])):
        ref = counterexamples.parse(item.get('ref') or '')
        if ref is None or item['object_id'] not in latest:
            continue
        target = item['object_id']
        pinned[item['ref']] = {'ref': item['ref'], 'latest_version': latest[target],
                               'cross_revision': item['object_version'] < latest[target],
                               'present': item['component'] in ledgers[target]
                               and ledgers[target][item['component']] is None}
    issues = []
    for event in corpus['events']:
        if not event['kind'].startswith('issue.') or event['kind'] == 'issue.raised':
            continue
        component, primary = event['subject_refs'][0]['component'], event['subject_refs'][1]['object_id']
        earlier = [view for view in snapshots.get(primary, []) if moment(view['as_of']) <= moment(event['occurred_at'])]
        current = max(earlier, key=lambda view: moment(view['as_of']), default=None)
        carried = bool(current) and any(item['id'] == component for block in current['blocks']
                                        if block['id'] == 'issues' for item in block['components'])
        issues.append({'event': f"event:{event['event_id']}", 'kind': event['kind'], 'component': component,
                       'primary': primary, 'snapshot': current and current['ref'], 'in_snapshot': carried})
    return {'component_refs': sorted(pinned.values(), key=lambda item: item['ref']), 'issue_events': issues}


# ------------------------------------------------------------------ the four checks
def _result(check: str, status: str, evidence: dict, missing: list[str], action: str | None = None) -> dict:
    condition, default, sources = CHECKS[check]
    return {'id': check, 'condition': condition, 'action': action or default, 'sources': sources,
            'status': status, 'evidence': evidence, 'missing': missing}


def activity_to_component(summary: dict) -> dict:
    """第 1 条：summary 的 b（b_conclusions 的摘要）按 #66 的口径，加参照组在 task_only 场景的四项门。"""
    missing, evidence = [], {}
    b = None
    digest = summary.get('b')
    if digest is None:
        missing.append('#66 对照实验 B 在 10/12–16 试用后的观测结论：两条线执行到关闭、取证之后由 b_observe 算出的 '
                       'observations.json')
    else:
        b = digest['conclusion']['activity']
        evidence['b'] = {'activity': b, 'because': digest['conclusion']['because'],
                         'strict': digest['strict'], 'script': digest['script']['id'], 'smoke': digest['smoke']}
    scenario = summary['groups'][REFERENCE]['scenarios'].get('task_only')
    e = None
    if scenario is None or not scenario['valid']:
        missing.append('标准答案经 E&O DRI 批准后，参照组（固定路径）在 task_only 场景的有效运行')
    else:
        e = scenario['verdict']['passed']
        evidence['e'] = {'group': REFERENCE, 'verdict': scenario['verdict']}
    if b == 'component' and e is True:
        status = TRIGGERED
    elif b == 'object' or e is False:
        status = NOT_TRIGGERED
    else:
        status = NO_DATA
    return _result('activity_to_component', status, evidence, missing if status == NO_DATA else [])


def component_ref_instability(scanned: dict) -> dict:
    sample = [item for item in scanned['component_refs'] if item['cross_revision']]
    dangling = [item['ref'] for item in sample if not item['present']]
    evidence = {'component_refs': len(scanned['component_refs']), 'cross_revision': len(sample),
                'dangling': dangling, 'share': len(dangling) / len(sample) if sample else None}
    if len(sample) < MIN_COMPONENT_REFS:
        return _result('component_ref_instability', NO_DATA, evidence, [
            f'跨修订的组件引用只有 {len(sample)} 条，至少要 {MIN_COMPONENT_REFS} 条。实验 E 的播种是编写的修订；要的是'
            '试用期间（10/12–16）经人与 Agent 修订过组件、又被别处钉住的记录（#66 两条线与实验实例），用同一扫描读回'])
    status = TRIGGERED if evidence['share'] > MAX_DANGLING else NOT_TRIGGERED
    return _result('component_ref_instability', status, evidence, [])


def why_coverage_low(summary: dict) -> dict:
    prepared = summary['prepared']['questions']
    group = summary['groups'][REFERENCE]['scenarios']
    rows, missing = {}, []
    for scenario, item in group.items():
        context = prepared.get(metrics.key(scenario, 'why'), {}).get('contexts', {}).get(REFERENCE)
        retrieval = metrics.ratio(context['recall']) if context else None
        answered = item['questions']['why']
        coverage = metrics.ratio(answered['answer_coverage']) if answered['runs'] else None
        low = (retrieval is not None and retrieval < WHY_RECALL) or (coverage is not None and coverage < WHY_COVERAGE)
        rows[scenario] = {'retrieval_recall': retrieval, 'answer_coverage': coverage, 'low': low}
        if retrieval is None:
            missing.append(f'{spec.SCENARIOS.get(scenario, scenario)}：参照组 Why 问的准备结果')
        if coverage is None:
            missing.append(f'{spec.SCENARIOS.get(scenario, scenario)}：参照组 Why 问的有效运行（回答覆盖要模型作答，'
                           '标准答案批准后才能跑）')
    lows = [scenario for scenario, row in rows.items() if row['low']]
    # 读法（2026-09-29 定，#74 彩排之后）：偏低的场景里有取到召回不够的，是取法或主干关系的问题；都取到了、只是回答
    # 没引全的，要改的是怎么让模型答全（提示与上下文 Markdown 的组织），不改主干关系。
    fetched = any(rows[scenario]['retrieval_recall'] is not None and rows[scenario]['retrieval_recall'] < WHY_RECALL
                  for scenario in lows)
    reading = 'retrieval' if fetched else 'answering' if lows else None
    evidence = {'group': REFERENCE, 'scenarios': rows, 'low': lows, 'reading': reading}
    if len(lows) >= WHY_SCENARIOS:
        status = TRIGGERED
    elif rows and not missing:
        status = NOT_TRIGGERED
    else:
        status = NO_DATA
    return _result('why_coverage_low', status, evidence, missing if status == NO_DATA else [],
                   WHY_ANSWERING if status == TRIGGERED and reading == 'answering' else None)


def issue_detached(scanned: dict) -> dict:
    sample = scanned['issue_events']
    detached = [item['event'] for item in sample if not item['in_snapshot']]
    evidence = {'issue_events': len(sample), 'detached': detached,
                'share': len(detached) / len(sample) if sample else None}
    if len(sample) < MIN_ISSUE_EVENTS:
        return _result('issue_detached', NO_DATA, evidence, [
            f'提出之后的问题事件只有 {len(sample)} 条，至少要 {MIN_ISSUE_EVENTS} 条。实验 E 的五个场景没有问题流转；'
            '要的是 #66 两条线在试用期间（10/12–16）的问题事件与状态快照，用同一扫描读回'])
    status = TRIGGERED if evidence['share'] >= DETACHED_SHARE else NOT_TRIGGERED
    return _result('issue_detached', status, evidence, [])


# 检查 -> (条件, 触发后的调整, 数据源)
CHECKS = {
    'activity_to_component': (
        'Task-only 线达标', 'Activity 降为组件',
        ['#66 对照实验 B 的观测结论（observations.json 经 b_conclusions 摘成 summary.json 的 b，按 #66 口径的 '
         'conclusion.activity）',
         f'本实验参照组（{metrics.GROUPS[REFERENCE]}）在 task_only 场景上的四项门（summary.json）']),
    'component_ref_instability': (
        '组件引用跨修订不稳', '拆块或升对象',
        ['读投影扫描：scope 内对象与事件里钉着的组件引用，对照目标对象最新版的组件台账（prepared.json 的 scan）']),
    'why_coverage_low': (
        'Why 覆盖持续偏低', '改主干关系或取法',
        [f'参照组（{metrics.GROUPS[REFERENCE]}）Why 问的取到召回（prepared.json，不要模型）',
         f'参照组（{metrics.GROUPS[REFERENCE]}）Why 问的回答覆盖（summary.json，要模型运行）']),
    'issue_detached': (
        'Issue 常脱离主体快照演进', 'Issue 升为对象',
        ['读投影扫描：提出之后的问题事件，与主受影响对象在事件时刻的最新状态快照（prepared.json 的 scan）']),
}
WHY_ANSWERING = '改怎么让模型答全（提示与上下文 Markdown 的组织），不改主干关系'
THRESHOLDS = {'MIN_COMPONENT_REFS': MIN_COMPONENT_REFS, 'MAX_DANGLING': MAX_DANGLING, 'WHY_RECALL': WHY_RECALL,
              'WHY_COVERAGE': WHY_COVERAGE, 'WHY_SCENARIOS': WHY_SCENARIOS, 'MIN_ISSUE_EVENTS': MIN_ISSUE_EVENTS,
              'DETACHED_SHARE': DETACHED_SHARE, 'REFERENCE': REFERENCE}


def evaluate(summary: dict) -> list[dict]:
    """四个检查，按规格里的顺序。扫描结果取自 summary 带着的准备结果，B 的结论取自 summary 的 b。"""
    scanned = summary['prepared']['scan']
    return [activity_to_component(summary), component_ref_instability(scanned),
            why_coverage_low(summary), issue_detached(scanned)]
