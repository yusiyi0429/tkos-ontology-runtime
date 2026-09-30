"""实验 E（票 #67）的反例判定：输入结构化回答与某个场景的标准答案，输出每类反例是否出现、依据哪条断言。纯函数，
不连库、不接模型，供 #68 的指标代码调用。

回答是断言的列表，沿用 0.1 的形状 ``{"claim": 文字, "kind": 种类, "refs": [引用]}``，种类三种：

- ``fact``：陈述内容；
- ``gap``：说明为空或取不到，要引出显示为空的块或对象；
- ``conflict``：指出冲突，``refs`` 同时引冲突的两处（上层的约束与本层与之相抵的计划或执行上下文），``claim`` 写冲突
  是什么。#82 起约束是带约束角色的组件，标准答案里冲突的两侧都写到组件；判定只看标准答案给的引用，不认块 id。

引用是 0.2 的四种业务形式：``<对象 id>@<版本>``、``…#<块>``、``…#<块>/<组件>``、``event:<事件 id>``；不带 ``@`` 的裸 UUID
当作事件（同 0.1）。

判定口径（标准答案的占位先经 ``resolve`` 换成这次播种的引用）：

- 诱饵的范围：诱饵是事件时引用须完全相同；是对象时，该对象的任何块与组件都算；是块时，这一块与其中的组件都算；
  是组件时只算这一条。诱饵写了版本（``@键@N``）只算那一版，没写的任何版本都算。只引对象本身不落在块或组件诱饵上。
- ``other_unit``、``stale_state``：任何种类的断言引了诱饵即出现。
- ``stale_version``：任何种类的断言引了诱饵，或引了比这次播种结束时更旧的任一对象版本，即出现。
- ``phantom_activity``：任何种类的断言引了 Activity 类型的对象，或引了 scope 里不存在的对象，即出现。
- ``content_as_empty``：``gap`` 断言以块级引用引了诱饵（有内容的块）即出现；引块里的某个组件（以某条计划或验收标准
  为依据说它还没满足）不算，``fact`` 与 ``conflict`` 也不算（2026-09-29 定，#74 彩排之后）。
- ``conflict_missed``：冲突的每一侧是一组可以接受的块或组件引用，不看版本；侧是块时其中的组件也算。一条
  ``conflict`` 断言只引到一侧即出现（原因 ``one_sided``）；判某一问时，若这一问在冲突要求的问题里，却没有一条
  ``conflict`` 断言同时引到两侧，也出现（原因 ``not_stated``，此时没有依据的断言）。不给问题即按整份回答判。

输出：类别 -> ``{"occurred": 是否出现, "claims": [断言的序号], "refs": [落在反例上的引用], "reason": 原因或 None}``，
只列这个场景要判的类别。
"""
from __future__ import annotations

import re
from typing import Any

from . import spec

KINDS = ('fact', 'gap', 'conflict')
_UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
_REF = re.compile(rf'^({_UUID})@([1-9][0-9]*)(?:#([a-z][a-z0-9_]*)(?:/({spec.COMPONENT_ID}))?)?$')
_EVENT = re.compile(rf'^event:({_UUID})$')


def normalize(ref: str) -> str:
    """回答里的引用写法归一：去空白、id 小写；不带 ``@`` 的裸 UUID 当作事件。"""
    ref = ref.strip()
    if ref.lower().startswith('event:'):
        return 'event:' + ref[6:].strip().lower()
    head, mark, rest = ref.partition('#')
    head = head.lower()
    if re.fullmatch(_UUID, head):
        return f'event:{head}'
    return head + mark + rest


def parse(ref: str) -> dict[str, Any] | None:
    """业务形式拆开：事件给事件 id；对象、块与组件给对象 id、版本、块与组件。认不出的为 None。"""
    if match := _EVENT.fullmatch(ref):
        return {'event_id': match.group(1)}
    if match := _REF.fullmatch(ref):
        object_id, version, block, component = match.groups()
        return {'object_id': object_id, 'version': int(version), 'block': block, 'component': component}
    return None


def resolve(answers: dict, manifest: dict) -> dict[str, Any]:
    """一个场景的标准答案换成这次播种的引用：``@键`` 为播种结束时的最新版本，``event:键`` 为 ``event:<事件 id>``。
    另给出每个对象播种结束时的最新版本与类型，用来认旧版本、Activity 与不存在的对象。"""
    objects, events = manifest['objects'], manifest['events']

    def concrete(ref: str) -> str:
        kind, key = next(spec.placeholders(ref))
        return f'event:{events[key]}' if kind == 'event' else spec.resolve(ref, objects, {})

    def decoy(ref: str) -> dict[str, Any]:
        pinned = bool(spec.OBJECT.fullmatch(ref)) and spec.parts(ref)[1] is not None
        return {'ref': concrete(ref), 'any_version': not pinned}

    counters = {}
    for item in answers['counterexamples']:
        counters[item['category']] = {'decoys': [decoy(ref) for ref in item['decoys']]}
        if 'conflict' in item:
            conflict = item['conflict']
            counters[item['category']]['conflict'] = {
                'upper': [concrete(ref) for ref in conflict['upper']],
                'lower': [concrete(ref) for ref in conflict['lower']], 'questions': list(conflict['questions'])}
    return {'id': answers['id'], 'start': concrete('@' + answers['start']),
            'questions': {item['id']: [concrete(ref) for ref in item['expected']] for item in answers['questions']},
            'counterexamples': counters,
            'latest': {item['object_id']: item['version'] for item in objects.values()},
            'types': {item['object_id']: item['type'] for item in objects.values()}}


def _within(ref: dict[str, Any] | None, target: dict[str, Any], *, any_version: bool) -> bool:
    """引用落在目标上：目标是事件时要同一条；是对象、块或组件时，同一对象、（写了版本时）同一版本，且落在目标的
    块或组件里。"""
    if ref is None:
        return False
    if 'event_id' in target:
        return ref.get('event_id') == target['event_id']
    if ref.get('object_id') != target['object_id'] or (not any_version and ref['version'] != target['version']):
        return False
    if target['block'] is None:
        return True
    return ref['block'] == target['block'] and (target['component'] is None or ref['component'] == target['component'])


def _checked(claims: Any) -> list[dict[str, Any]]:
    """断言的形状：claim 为文字，kind 为三种之一，refs 为引用的列表。不合形状抛 ValueError。"""
    if not isinstance(claims, list):
        raise ValueError('an answer is a list of claims')
    checked = []
    for index, claim in enumerate(claims):
        if (not isinstance(claim, dict) or not isinstance(claim.get('claim'), str) or claim.get('kind') not in KINDS
                or not isinstance(claim.get('refs'), list) or not all(isinstance(ref, str) for ref in claim['refs'])):
            raise ValueError(f'claim {index} is not {{claim, kind, refs}} with kind in {KINDS}')
        refs = [normalize(ref) for ref in claim['refs']]
        checked.append({'kind': claim['kind'], 'refs': refs, 'parsed': [parse(ref) for ref in refs]})
    return checked


def _hit(claims: list[dict[str, Any]], test, kinds=KINDS) -> dict[str, Any]:
    found, refs = [], []
    for index, claim in enumerate(claims):
        matched = [ref for ref, parsed in zip(claim['refs'], claim['parsed']) if claim['kind'] in kinds and test(parsed)]
        if matched:
            found.append(index)
            refs += [ref for ref in matched if ref not in refs]
    return {'occurred': bool(found), 'claims': found, 'refs': refs, 'reason': None}


def judge(claims: Any, gold: dict[str, Any], question: str | None = None) -> dict[str, dict[str, Any]]:
    """判一份回答（给 question 时是这一问的回答）在这个场景里出现了哪些反例。gold 是 ``resolve`` 的结果。"""
    if question is not None and question not in spec.QUESTIONS:
        raise ValueError(f'question is one of {spec.QUESTIONS}')
    checked = _checked(claims)
    latest, types = gold['latest'], gold['types']
    result = {}
    for category, counter in gold['counterexamples'].items():
        decoys = [(parse(item['ref']), item['any_version']) for item in counter['decoys']]

        def on_decoy(parsed, decoys=decoys):
            return any(_within(parsed, target, any_version=any_version) for target, any_version in decoys)

        if category in {'other_unit', 'stale_state'}:
            result[category] = _hit(checked, on_decoy)
        elif category == 'stale_version':
            result[category] = _hit(checked, lambda parsed, on_decoy=on_decoy: on_decoy(parsed) or bool(
                parsed and 'object_id' in parsed and parsed['version'] < latest.get(parsed['object_id'], 0)))
        elif category == 'phantom_activity':
            result[category] = _hit(checked, lambda parsed, on_decoy=on_decoy: on_decoy(parsed) or bool(
                parsed and 'object_id' in parsed and types.get(parsed['object_id']) in {None, 'Activity'}))
        elif category == 'content_as_empty':
            result[category] = _hit(checked, lambda parsed, on_decoy=on_decoy: on_decoy(parsed)
                                    and parsed.get('component') is None, kinds=('gap',))
        elif category == 'conflict_missed':
            result[category] = _conflict(checked, counter['conflict'], question)
        else:
            raise ValueError(f'unknown counterexample category {category}')
    return result


def _conflict(claims: list[dict[str, Any]], conflict: dict[str, Any], question: str | None) -> dict[str, Any]:
    sides = {side: [parse(ref) for ref in conflict[side]] for side in ('upper', 'lower')}

    def touches(claim, side):
        return [ref for ref, parsed in zip(claim['refs'], claim['parsed'])
                if any(_within(parsed, target, any_version=True) for target in sides[side])]

    one_sided, refs, stated = [], [], False
    for index, claim in enumerate(claims):
        if claim['kind'] != 'conflict':
            continue
        upper, lower = touches(claim, 'upper'), touches(claim, 'lower')
        if upper and lower:
            stated = True
        elif upper or lower:
            one_sided.append(index)
            refs += [ref for ref in upper + lower if ref not in refs]
    required = question is None or question in conflict['questions']
    if one_sided:
        return {'occurred': True, 'claims': one_sided, 'refs': refs, 'reason': 'one_sided'}
    if required and not stated:
        return {'occurred': True, 'claims': [], 'refs': [], 'reason': 'not_stated'}
    return {'occurred': False, 'claims': [], 'refs': [], 'reason': None}
