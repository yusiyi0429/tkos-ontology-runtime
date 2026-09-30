"""实验 E（票 #67）的场景播种与标准答案：格式与自洽校验、占位引用解析、各对象在播种结束时的版本与组件。纯函数，不连库。

场景文件（scenarios.json）先写五个场景共用的主干播种（base），再按场景写各自的播种；步骤之间用稳定键互相引用。
场景只建、只改自己的对象：它的步骤改到的对象（修订、建关系、指派、门与生命周期的目标，快照的主体，外部事件的主体）
要么是本场景建的，要么是它在 owns 里认领的主干对象，一个对象只归一个场景。共用对象的改动一律写在主干里。

占位写法（整串匹配才替换，正文里出现的 @ 不动）：
- `@键` 为该对象此刻的最新版本，`@键@N` 为第 N 版，后面都可带 `#块` 与 `#块/组件`；
- `$身份键` 为该身份的 principal id；
- `event:键` 为某一步记下的那条事件；
- 快照的 `as_of` 与外部事件的 `occurred_at` 写 `now`，播种时取那一步的数据库时刻。
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = json.loads((ROOT / 'docs/contracts/world-registry-0.2.json').read_text())
OBJECT_SPECS = {item['type']: item for item in REGISTRY['objects']}
PAYLOAD_SPECS = {item['id']: item for item in REGISTRY['state']['payload_types']}

COMPONENT_ID = r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}'
OBJECT = re.compile(rf'^@([a-z][a-z0-9_]*)(?:@([1-9][0-9]*))?(?:#([a-z][a-z0-9_]*)(?:/({COMPONENT_ID}))?)?$')
PRINCIPAL = re.compile(r'^\$([a-z][a-z0-9_]*)$')
EVENT = re.compile(r'^event:([a-z][a-z0-9_]*)$')
KEY = re.compile(r'^[a-z][a-z0-9_]*$')
TIMESTAMP = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$')
NOW = 'now'

# 步骤 -> 必填字段；另外只能有 key 与 note。建对象与写快照的 key 是对象键，其余步骤的 key 是它记下的那条事件的键。
STEPS = {
    'create': ('by', 'key', 'type', 'domain', 'payload'),
    'revise': ('by', 'target', 'payload'),
    'relate': ('by', 'target', 'field', 'refs'),
    'assign': ('by', 'target', 'to'),
    'gate': ('by', 'target', 'action', 'params'),
    'lifecycle': ('by', 'target', 'action', 'params'),
    'refresh': ('by', 'key', 'payload'),
    'record': ('by', 'params'),
}
MAKES_OBJECT = {'create', 'refresh'}
ACTIONS = {'create': 'world_create_object', 'revise': 'world_revise_object', 'relate': 'world_relate',
           'assign': 'world_assign', 'refresh': 'world_refresh_state', 'record': 'world_record_event'}
GATE_ACTIONS = {'world_confirm_long_term_goal', 'world_commit_period_goal', 'world_confirm_period_goal',
                'world_commit_mission', 'world_confirm_mission'}
LIFECYCLE_ACTIONS = {'world_start', 'world_deliver', 'world_accept', 'world_reject', 'world_reopen', 'world_cancel'}
# 指派出新修订写 responsible 的类型（责任单元的指派只记事件，契约第 9.2 节）。
ASSIGN_REVISES = {'Mission', 'Task', 'Activity'}

SCENARIOS = {'cross_unit': '跨责任单元', 'constraint_conflict': '约束冲突', 'version_change': '版本变化',
             'rework_restart': '验收失败重启', 'task_only': '无 Activity 的 Task'}
QUESTIONS = ('why', 'what', 'who', 'now', 'happened', 'basis')
# 反例类别：cite_any 为真时断言引了诱饵就算（诱饵不能同时是应引用的项）；为假时只看特定的断言（见 counterexamples）。
COUNTEREXAMPLES = {
    'other_unit': {'name': '另一责任单元的目标与约束混入', 'cite_any': True},
    'conflict_missed': {'name': '约束冲突没指出或只引一处', 'cite_any': False},
    'stale_version': {'name': '引用旧版本', 'cite_any': True},
    'stale_state': {'name': '引用过时的状态或快照', 'cite_any': True},
    'phantom_activity': {'name': '引用不存在的 Activity', 'cite_any': True},
    'content_as_empty': {'name': '把有内容的块说成空', 'cite_any': False},
}
# 每个场景至少要判的反例类别。
REQUIRED = {'cross_unit': {'other_unit'}, 'constraint_conflict': {'conflict_missed'}, 'version_change': {'stale_version'},
            'rework_restart': {'stale_state'}, 'task_only': {'phantom_activity', 'content_as_empty'}}
APPROVER = 'E&O DRI'


class SeedError(ValueError):
    """场景文件或标准答案不自洽。"""


# ------------------------------------------------------------------ placeholders
def resolve(value: Any, objects: dict[str, dict], principals: dict[str, str], events: dict[str, str] | None = None) -> Any:
    """把占位换成业务形式的引用、principal id 与 `event:<事件 id>`。objects 给出每个对象键此刻的 object_id 与最新版本。"""
    if isinstance(value, dict):
        return {key: resolve(item, objects, principals, events) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, objects, principals, events) for item in value]
    if not isinstance(value, str):
        return value
    if match := OBJECT.fullmatch(value):
        key, version, block, component = match.groups()
        if key not in objects:
            raise SeedError(f'{value}: no object has the key {key}')
        latest = objects[key]['version']
        chosen = int(version) if version else latest
        if chosen > latest:
            raise SeedError(f'{value}: {key} has only {latest} versions so far')
        return (f"{objects[key]['object_id']}@{chosen}" + (f'#{block}' if block else '')
                + (f'/{component}' if component else ''))
    if match := PRINCIPAL.fullmatch(value):
        if match.group(1) not in principals:
            raise SeedError(f'{value}: no identity has the key {match.group(1)}')
        return principals[match.group(1)]
    if (match := EVENT.fullmatch(value)) and events is not None:
        if match.group(1) not in events:
            raise SeedError(f'{value}: no step recorded the event {match.group(1)}')
        return f'event:{events[match.group(1)]}'
    return value


def placeholders(value: Any) -> Iterator[tuple[str, str]]:
    """值里出现的全部占位：('object' | 'principal' | 'event', 键)。"""
    if isinstance(value, dict):
        for item in value.values():
            yield from placeholders(item)
    elif isinstance(value, list):
        for item in value:
            yield from placeholders(item)
    elif isinstance(value, str):
        for kind, pattern in (('object', OBJECT), ('principal', PRINCIPAL), ('event', EVENT)):
            if match := pattern.fullmatch(value):
                yield kind, match.group(1)


def parts(ref: str) -> tuple[str, int | None, str | None, str | None]:
    """对象占位拆成（键、指定的版本、块、组件）。"""
    key, version, block, component = OBJECT.fullmatch(ref).groups()
    return key, int(version) if version else None, block, component


# ------------------------------------------------------------------ steps
def steps(scenarios: dict) -> list[tuple[str | None, dict]]:
    """按播种顺序的全部步骤：先主干（场景为 None），再逐个场景。"""
    return ([(None, step) for step in scenarios['base']['steps']]
            + [(item['id'], step) for item in scenarios['scenarios'] for step in item['steps']])


def object_type(step: dict) -> str:
    """建对象的步骤写明类型；写快照的步骤建的是状态快照。"""
    return step['type'] if step['do'] == 'create' else 'StateSnapshot'


def action(step: dict) -> str:
    return ACTIONS.get(step['do']) or step['action']


def touched(step: dict) -> list[str]:
    """这一步改到的对象键：目标、快照的主体与外部事件的主体。"""
    if 'target' in step:
        return [step['target']]
    if step['do'] == 'refresh':
        return [OBJECT.fullmatch(step['payload']['subject_ref']).group(1)]
    if step['do'] == 'record':
        return [OBJECT.fullmatch(ref).group(1) for ref in step['params']['subject_refs']]
    return []


def _merge(blocks: dict[str, set[str]], patch_blocks: dict) -> dict[str, set[str]]:
    """组件按 id 合并（契约第 12 节）：给出的 id 新增或改写，removed 删除，块给 null 即清空。"""
    merged = {block: set(ids) for block, ids in blocks.items()}
    for block, value in (patch_blocks or {}).items():
        if value is None:
            merged[block] = set()
            continue
        current = merged.setdefault(block, set())
        for item in value.get('components', []):
            if item.get('removed'):
                current.discard(item['id'])
            elif item.get('id'):
                current.add(item['id'])
    return merged


class World:
    """按步骤推演播种结束时的世界：每个对象键的类型、所在域、归属的场景、逐版本的组件与各版本号，每个事件键归属的
    场景，以及版本号的来源（建对象为 1；修订、建关系、Mission／Task／Activity 的指派与写回候选的确认各加 1）。"""

    def __init__(self, scenarios: dict | None = None):
        self.objects: dict[str, dict] = {}
        self.events: dict[str, str | None] = {}
        self.pending: dict[str, dict] = {}  # 一轮或草稿承诺带着、等确认接受时写回的候选
        for scenario, step in steps(scenarios) if scenarios else []:
            self.apply(scenario, step)

    def version(self, key: str) -> int:
        return len(self.objects[key]['components'])

    def bump(self, key: str, patch_blocks: dict | None = None) -> None:
        versions = self.objects[key]['components']
        versions.append(_merge(versions[-1], patch_blocks or {}))

    def apply(self, scenario: str | None, step: dict) -> None:
        do = step['do']
        if do in MAKES_OBJECT:
            kind = object_type(step)
            blocks = {block: {item['id'] for item in (value or {}).get('components', []) if item.get('id')}
                      for block, value in step['payload'].get('blocks', {}).items()}
            payload_type = step['payload'].get('payload_type')
            self.objects[step['key']] = {'type': kind, 'domain': step.get('domain'), 'scenario': scenario,
                                         'payload_type': payload_type, 'title': step['payload']['title'],
                                         'components': [blocks]}
            return
        if step.get('key'):
            self.events[step['key']] = scenario
        target = step.get('target')
        if do == 'revise':
            self.bump(target, step['payload'].get('blocks'))
        elif do == 'relate':
            self.bump(target)
        elif do == 'assign' and self.objects[target]['type'] in ASSIGN_REVISES:
            self.bump(target)
        elif do == 'gate':
            params = step['params']
            if 'payload' in params:
                self.pending[target] = params['payload']
            if params.get('outcome') == 'accepted' and target in self.pending:
                self.bump(target, self.pending.pop(target).get('blocks'))
            elif params.get('outcome') in {'returned', 'withdrawn'}:
                self.pending.pop(target, None)

    def final_versions(self) -> dict[str, int]:
        return {key: self.version(key) for key in self.objects}

    def has_component(self, key: str, version: int, block: str, component: str) -> bool:
        return component in self.objects[key]['components'][version - 1].get(block, set())

    def blocks_of(self, key: str) -> set[str]:
        item = self.objects[key]
        if item['type'] == 'StateSnapshot':
            return {block['id'] for block in PAYLOAD_SPECS[item['payload_type']]['blocks']}
        return {block['id'] for block in OBJECT_SPECS[item['type']]['blocks']}


def final_versions(scenarios: dict) -> dict[str, int]:
    """每个对象键在播种结束时的最新版本号。"""
    return World(scenarios).final_versions()


# ------------------------------------------------------------------ validation
def _check_ref(world: World, ref: str, where: str, *, versions: dict[str, int] | None = None) -> None:
    """一条对象占位：键已建出、指定的版本不超过当时的版本、块是该类型登记的块、组件在那一版的那一块里。"""
    key, version, block, component = parts(ref)
    if key not in world.objects:
        raise SeedError(f'{where}: {ref} names no object built so far')
    latest = (versions or {}).get(key, world.version(key))
    chosen = version or latest
    if chosen > latest:
        raise SeedError(f'{where}: {ref} is newer than {key} is at that point')
    if block is not None and block not in world.blocks_of(key):
        raise SeedError(f'{where}: {ref} names a block {world.objects[key]["type"]} does not have')
    if component is not None and not world.has_component(key, chosen, block, component):
        raise SeedError(f'{where}: {ref} names a component that version does not have')


def validate(scenarios: dict) -> None:
    """格式、身份与域、步骤顺序与引用、场景归属：每一步只引用已经存在的对象、事件与身份，场景只改自己的对象。"""
    if scenarios.get('format') != 'tkos-world-scenarios/0.2' or scenarios.get('contract_version') != 'tkos.world/0.2':
        raise SeedError('unknown scenarios format')
    domains = scenarios['domains']
    for name, identity in scenarios['identities'].items():
        if not KEY.fullmatch(name) or identity.get('type') not in {'human', 'agent'} or not identity.get('display_name'):
            raise SeedError(f'identity {name}: a snake_case key, a type and a role name are needed')
        if not identity['roles'] or not set(identity['roles']) <= set(domains):
            raise SeedError(f'identity {name}: a role in a domain the scope does not have')
    if [item['id'] for item in scenarios['scenarios']] != list(SCENARIOS):
        raise SeedError('the scenarios are the five of the experiment, in order')
    world = World()
    owner: dict[str, str] = {}
    for item in scenarios['scenarios']:
        for key in item['owns']:
            if key in owner:
                raise SeedError(f'{key} is owned by two scenarios')
            owner[key] = item['id']
    used: set[str] = set(scenarios['identities'])
    for number, (scenario, step) in enumerate(steps(scenarios), 1):
        where = f"step {number} ({step.get('do')}{', ' + scenario if scenario else ''})"
        do = step.get('do')
        if do not in STEPS or any(field not in step for field in STEPS[do]):
            raise SeedError(f'{where}: unknown step or missing field')
        if set(step) - set(STEPS[do]) - {'do', 'key', 'note'}:
            raise SeedError(f'{where}: unexpected fields')
        if step['by'] not in scenarios['identities'] or step.get('to', step['by']) not in scenarios['identities']:
            raise SeedError(f'{where}: unknown identity')
        if do == 'create' and (step['domain'] not in domains or step['type'] not in OBJECT_SPECS
                               or step['type'] == 'StateSnapshot'):
            raise SeedError(f'{where}: unknown domain or object type')
        if do == 'gate' and step['action'] not in GATE_ACTIONS or do == 'lifecycle' and step['action'] not in LIFECYCLE_ACTIONS:
            raise SeedError(f'{where}: unknown action {step["action"]}')
        if do == 'refresh' and step['payload'].get('payload_type') not in PAYLOAD_SPECS:
            raise SeedError(f'{where}: unknown payload type')
        for field in ('as_of', 'occurred_at'):
            moment = (step.get('payload') if do == 'refresh' else step.get('params') or {}).get(field)
            if moment is not None and moment != NOW and not TIMESTAMP.fullmatch(moment):
                raise SeedError(f'{where}: {field} is now or a timestamp')
        cited = list(placeholders([step.get('payload'), step.get('params'), step.get('refs')]))
        for kind, key in cited:
            if kind == 'principal' and key not in scenarios['identities']:
                raise SeedError(f'{where}: unknown identity {key}')
            if kind == 'event' and key not in world.events:
                raise SeedError(f'{where}: event {key} has not been recorded yet')
        for text in _object_refs([step.get('payload'), step.get('params'), step.get('refs')]):
            _check_ref(world, text, where)
        for key in touched(step):
            if key not in world.objects:
                raise SeedError(f'{where}: {key} does not exist yet')
            built_by = world.objects[key]['scenario']
            if scenario is None and built_by is not None:
                raise SeedError(f'{where}: the trunk cannot change a scenario object')
            if scenario is not None and built_by != scenario and owner.get(key) != scenario:
                raise SeedError(f'{where}: {key} belongs to another scenario or to the trunk')
        if step.get('key') is not None:
            if not KEY.fullmatch(step['key']) or step['key'] in used:
                raise SeedError(f'{where}: key {step["key"]} is malformed or used twice')
            used.add(step['key'])
        world.apply(scenario, step)
    for key, scenario in owner.items():
        if key not in world.objects or world.objects[key]['scenario'] is not None:
            raise SeedError(f'{scenario} owns {key}, which is not a trunk object')
    for item in scenarios['scenarios']:
        start = world.objects.get(item['start'])
        if start is None or start['type'] == 'StateSnapshot' or item['start'] not in set(item['owns']) | {
                key for key, value in world.objects.items() if value['scenario'] == item['id']}:
            raise SeedError(f"{item['id']}: the start is a business object the scenario owns")
    for slot in scenarios['pending_material']:
        key = parts(slot['slot'])[0]
        if key not in world.objects or world.objects[key]['type'] not in {'Company', 'Strategy'} or not slot['stub']:
            raise SeedError(f"pending material {slot['slot']}: a block or component of the Company or the Strategy")
        _check_ref(world, slot['slot'], 'pending material')


def _object_refs(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _object_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _object_refs(item)
    elif isinstance(value, str) and OBJECT.fullmatch(value):
        yield value


def validate_gold(gold: dict, scenarios: dict) -> None:
    """标准答案：五个场景齐全、起点与场景文件一致；六问齐全、各有应引用的对象、块、组件或事件，Why 追到 Strategy 与
    Company 的块或组件；每个场景有它该判的反例类别，各有规则与诱饵；引用的键、块与组件都在播种里。"""
    if gold.get('format') != 'tkos-world-gold/0.2' or gold.get('scenarios') != 'scenarios.json':
        raise SeedError('unknown gold format')
    if gold.get('approver') != APPROVER or APPROVER not in {item['display_name'] for item in scenarios['identities'].values()}:
        raise SeedError('the gold answers are approved by the E&O DRI role, one of the seeded identities')
    world = World(scenarios)
    starts = {item['id']: item['start'] for item in scenarios['scenarios']}
    if [item['id'] for item in gold['scenario_answers']] != list(SCENARIOS):
        raise SeedError('the gold answers the five scenarios in order')

    def check(ref: str, where: str) -> None:
        found = list(placeholders(ref))
        if len(found) != 1 or found[0][0] == 'principal':
            raise SeedError(f'{where}: {ref} is not an object or event reference')
        kind, key = found[0]
        if kind == 'event' and key not in world.events:
            raise SeedError(f'{where}: {ref} names no recorded event')
        if kind == 'object':
            _check_ref(world, ref, where)

    for item in gold['scenario_answers']:
        name = item['id']
        if item['start'] != starts[name]:
            raise SeedError(f'{name}: the start differs from the scenarios file')
        if [question['id'] for question in item['questions']] != list(QUESTIONS):
            raise SeedError(f'{name}: the six questions in order')
        expected = set()
        for question in item['questions']:
            where = f"{name}.{question['id']}"
            if not question['question'].strip() or not question['answer'].strip() or not question['expected']:
                raise SeedError(f'{where}: a question, an answer and expected references are needed')
            if len(set(question['expected'])) != len(question['expected']):
                raise SeedError(f'{where}: an expected reference is listed once')
            for ref in question['expected']:
                check(ref, where)
            expected.update(question['expected'])
        why = [parts(ref) for ref in item['questions'][0]['expected'] if OBJECT.fullmatch(ref)]
        for top in ('Strategy', 'Company'):
            if not any(world.objects[key]['type'] == top and block for key, _, block, _ in why):
                raise SeedError(f'{name}.why: the answer reaches a block or component of the {top}')
        categories = [counter['category'] for counter in item['counterexamples']]
        if len(set(categories)) != len(categories) or not set(categories) <= set(COUNTEREXAMPLES) \
                or not REQUIRED[name] <= set(categories):
            raise SeedError(f'{name}: counterexample categories are known, listed once and cover {sorted(REQUIRED[name])}')
        for counter in item['counterexamples']:
            where = f"{name}.{counter['category']}"
            if not counter['rule'].strip() or not counter['decoys']:
                raise SeedError(f'{where}: a rule and at least one decoy are needed')
            for ref in counter['decoys']:
                check(ref, where)
            if COUNTEREXAMPLES[counter['category']]['cite_any'] and expected & set(counter['decoys']):
                raise SeedError(f'{where}: a decoy that is wrong to cite cannot also be an expected reference')
            conflict = counter.get('conflict')
            if (counter['category'] == 'conflict_missed') != (conflict is not None):
                raise SeedError(f'{where}: only a missed conflict names the conflict it is about')
            if conflict is not None:
                if not conflict['upper'] or not conflict['lower'] or not conflict['questions'] \
                        or not set(conflict['questions']) <= set(QUESTIONS):
                    raise SeedError(f'{where}: a conflict has an upper side, a lower side and the questions that state it')
                for ref in conflict['upper'] + conflict['lower']:
                    if not OBJECT.fullmatch(ref) or parts(ref)[2] is None:
                        raise SeedError(f'{where}: a side of a conflict is a block or a component')
                    check(ref, where)
                asked = {question['id']: set(question['expected']) for question in item['questions']}
                if not all(set(conflict['upper'] + conflict['lower']) <= asked[name] for name in conflict['questions']):
                    raise SeedError(f'{where}: a question that must state the conflict expects both of its sides')
    if not all(isinstance(text, str) and text.strip() for text in gold.get('to_confirm', [])):
        raise SeedError('to_confirm is a list of sentences')
