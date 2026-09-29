"""触发调整对象结构的四个条件（票 #68，规格 #46 第十节），各是一个有名字的机械检查。每个检查输出 triggered（触发）、
not_triggered（未触发）或 no_data（无数据），并写明数据源、依据与缺什么。纯函数，不连库。

判法的共同规则：已有的数据足以判定触发就判触发；判「未触发」要该有的数据都在；否则无数据，逐条列出缺的数据。

1. activity_to_component：Task-only 线达标则 Activity 降为组件。
   数据源：#66 对照实验 B 的观测结论（b_observe 输出的 observations.json，格式 OBSERVATIONS_FORMAT，看
   conclusion.activity）；本实验参照组（固定路径）在 task_only 场景上的四项门（summary.json）。
   B 的结论是 component（降为组件）且参照组在 task_only 场景四项门全过，触发；B 的结论是 object，或参照组在 task_only
   场景有门没过，未触发。
2. component_ref_instability：组件引用跨修订不稳则拆块或升对象。
   数据源：读投影扫描（scan 的 component_refs）：scope 内全部对象与事件里钉着的组件引用，只看跨修订的——钉的版本早于
   目标对象的最新版；对照目标对象最新版的组件台账，组件已删（removed_in_version 不为空）或不在台账里即悬空。
   跨修订的组件引用至少 MIN_COMPONENT_REFS 条才判；悬空的比例超过 MAX_DANGLING 触发。
3. why_coverage_low：Why 覆盖持续偏低则改主干关系或取法。
   数据源：参照组 Why 问的取到召回（准备结果，不要模型）与回答覆盖（summary.json，要模型运行），逐场景。
   一个场景偏低：取到召回低于召回门（WHY_RECALL），或回答覆盖低于 WHY_COVERAGE。持续：至少 WHY_SCENARIOS 个场景偏低。
4. issue_detached：Issue 常脱离主体快照演进则升为对象。
   数据源：读投影扫描（scan 的 issue_events）：提出之后的问题事件（路由、承接、处置、退回），看主受影响对象在事件
   发生时刻的最新状态快照（as_of 不晚于事件的发生时刻）的 issues 块里还有没有这个问题组件，没有即脱离。
   问题事件至少 MIN_ISSUE_EVENTS 条才判；脱离的比例不低于 DETACHED_SHARE 触发。

阈值（MIN_*、MAX_DANGLING、WHY_COVERAGE、WHY_SCENARIOS、DETACHED_SHARE）是本票定的默认值，待用户确认。
"""
from __future__ import annotations

from typing import Any

from . import counterexamples, metrics
from .retrieval import moment

TRIGGERED, NOT_TRIGGERED, NO_DATA = 'triggered', 'not_triggered', 'no_data'
STATUS_NAMES = {TRIGGERED: '触发', NOT_TRIGGERED: '未触发', NO_DATA: '无数据'}
REFERENCE = 'fixed'  # 参照组：固定路径，即服务自己的取上下文
OBSERVATIONS_FORMAT = 'tkos-world-02-experiment-b-observations/0.1'
MIN_COMPONENT_REFS, MAX_DANGLING = 10, 0.1
WHY_RECALL, WHY_COVERAGE, WHY_SCENARIOS = metrics.GATES['recall'], 0.5, 3
MIN_ISSUE_EVENTS, DETACHED_SHARE = 5, 0.5


# ------------------------------------------------------------------ scan of the read projection
def _pins(value: Any):
    """一段读投影里全部钉定的组件引用（钉定结构带 object_id、object_version 与不为空的 component）。"""
    if isinstance(value, dict):
        if value.get('component') and {'object_id', 'object_version', 'block'} <= value.keys():
            yield value
        for item in value.values():
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
def _result(check: str, status: str, evidence: dict, missing: list[str]) -> dict:
    condition, action, sources = CHECKS[check]
    return {'id': check, 'condition': condition, 'action': action, 'sources': sources,
            'status': status, 'evidence': evidence, 'missing': missing}


def activity_to_component(summary: dict, observations: dict | None) -> dict:
    missing, evidence = [], {}
    b = None
    if observations is None:
        missing.append('#66 对照实验 B 在 10/12–16 试用后的观测结论：两条线执行到关闭、取证之后由 b_observe 算出的 '
                       'observations.json')
    elif observations.get('format') != OBSERVATIONS_FORMAT \
            or observations.get('conclusion', {}).get('activity') not in {'component', 'object'}:
        missing.append(f'观测结论的格式不是 {OBSERVATIONS_FORMAT}，或没有 conclusion.activity')
    else:
        b = observations['conclusion']['activity']
        evidence['b'] = {'activity': b, 'because': observations['conclusion'].get('because', []),
                         'script': (observations.get('script') or {}).get('id')}
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
            missing.append(f'{scenario}：参照组 Why 问的准备结果')
        if coverage is None:
            missing.append(f'{scenario}：参照组 Why 问的有效运行（回答覆盖要模型作答，标准答案批准后才能跑）')
    lows = [scenario for scenario, row in rows.items() if row['low']]
    evidence = {'group': REFERENCE, 'scenarios': rows, 'low': lows}
    if len(lows) >= WHY_SCENARIOS:
        status = TRIGGERED
    elif rows and not missing:
        status = NOT_TRIGGERED
    else:
        status = NO_DATA
    return _result('why_coverage_low', status, evidence, missing if status == NO_DATA else [])


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
        ['#66 对照实验 B 的观测结论（observations.json 的 conclusion.activity）',
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
THRESHOLDS = {'MIN_COMPONENT_REFS': MIN_COMPONENT_REFS, 'MAX_DANGLING': MAX_DANGLING, 'WHY_RECALL': WHY_RECALL,
              'WHY_COVERAGE': WHY_COVERAGE, 'WHY_SCENARIOS': WHY_SCENARIOS, 'MIN_ISSUE_EVENTS': MIN_ISSUE_EVENTS,
              'DETACHED_SHARE': DETACHED_SHARE}


def evaluate(summary: dict, observations: dict | None = None) -> list[dict]:
    """四个检查，按规格里的顺序。扫描结果取自 summary 带着的准备结果。"""
    scanned = summary['prepared']['scan']
    return [activity_to_component(summary, observations), component_ref_instability(scanned),
            why_coverage_low(summary), issue_detached(scanned)]
