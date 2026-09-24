"""E&O 九月回放的播种文件与标准答案：格式与自洽校验、占位引用解析。纯函数，不连库。

播种文件按步骤写：建对象、修订、建关系、指派、门、写快照、记外部事件；步骤之间用稳定键互相引用。
占位写法（整串匹配才替换，正文里出现的 `@` 不动）：
- `@键` 为该对象此刻的最新版本，`@键@N` 为第 N 版，后面都可带 `#块`；
- `$身份键` 为该身份的 principal id；
- 标准答案里 `event:键` 为某一步（记外部事件、门、指派）记下的那条事件。
"""
from __future__ import annotations

import re
from typing import Any, Iterator

OBJECT = re.compile(r'^@([a-z][a-z0-9_]*)(?:@([1-9][0-9]*))?(?:#([a-z][a-z0-9_]*))?$')
PRINCIPAL = re.compile(r'^\$([a-z][a-z0-9_]*)$')
EVENT = re.compile(r'^event:([a-z][a-z0-9_]*)$')

# 步骤 → 必填字段。建对象与写快照产生对象键，记事件、门与指派可带事件键。
STEPS = {
    'create': ('key', 'by', 'type', 'domain', 'payload'),
    'revise': ('by', 'target', 'payload'),
    'relate': ('by', 'target', 'field', 'refs'),
    'assign': ('by', 'target', 'to'),
    'gate': ('by', 'target', 'action', 'params'),
    'refresh': ('key', 'by', 'payload'),
    'record': ('by', 'params'),
}
MAKES_OBJECT = {'create', 'refresh'}
MAKES_EVENT = {'assign', 'gate', 'record'}
QUESTIONS = ('why', 'what', 'who', 'now', 'happened', 'basis')
COUNTEREXAMPLES = {
    'old_version': '旧版本对象',
    'other_unit_constraint': '别的责任单元的约束',
    'unrelated_mission_state': '无关 Mission 的状态',
    'resolved_issue': '已处置的 issue',
    'unrelated_event': '无关事件',
    'empty_block_as_content': '空块被当作有内容',
    'outside_scope': 'scope 外的对象',
    'stale_artifact': '状态里的过期 artifact',
}


class SeedError(ValueError):
    """播种文件或标准答案不自洽。"""


def resolve(value: Any, objects: dict[str, dict], principals: dict[str, str]) -> Any:
    """把占位换成业务形式的引用与 principal id。objects 给出每个对象键此刻的 object_id 与最新版本。"""
    if isinstance(value, dict):
        return {key: resolve(item, objects, principals) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, objects, principals) for item in value]
    if not isinstance(value, str):
        return value
    if match := OBJECT.fullmatch(value):
        key, version, block = match.groups()
        if key not in objects:
            raise SeedError(f'{value}: no object has the key {key}')
        latest = objects[key]['version']
        chosen = int(version) if version else latest
        if chosen > latest:
            raise SeedError(f'{value}: {key} has only {latest} versions so far')
        return f"{objects[key]['object_id']}@{chosen}" + (f'#{block}' if block else '')
    if match := PRINCIPAL.fullmatch(value):
        if match.group(1) not in principals:
            raise SeedError(f'{value}: no identity has the key {match.group(1)}')
        return principals[match.group(1)]
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


def object_type(step: dict) -> str:
    """建对象的步骤写明类型；写快照的步骤建的是状态快照。"""
    return step['type'] if step['do'] == 'create' else 'StateSnapshot'


def produced(seed: dict) -> tuple[dict[str, dict], set[str]]:
    """播种产生的对象键（带类型与 scope）与事件键。"""
    objects, events = {}, set()
    for step in seed['steps']:
        if step['do'] in MAKES_OBJECT:
            scope = seed['identities'][step['by']]['scope']
            objects[step['key']] = {'type': object_type(step), 'scope': scope}
        elif step.get('key'):
            events.add(step['key'])
    return objects, events


def validate(seed: dict) -> None:
    """格式、身份与域、步骤顺序：每一步只引用已经存在的对象与身份，且不跨 scope。"""
    if seed.get('format') != 'tkos-world-seed/0.1':
        raise SeedError('unknown seed format')
    scopes = seed['scopes']
    for name, identity in seed['identities'].items():
        if identity['scope'] not in scopes or identity['type'] not in {'human', 'agent'}:
            raise SeedError(f'identity {name}: unknown scope or type')
        if not set(identity['roles']) <= set(scopes[identity['scope']]['domains']):
            raise SeedError(f'identity {name}: a role in a domain its scope does not have')
    built: dict[str, str] = {}  # 已经建出的对象键 → 所在 scope
    used: set[str] = set()
    for number, step in enumerate(seed['steps'], 1):
        where = f"step {number} ({step.get('do')})"
        if step.get('do') not in STEPS or any(field not in step for field in STEPS[step['do']]):
            raise SeedError(f'{where}: unknown step or missing field')
        if step['by'] not in seed['identities']:
            raise SeedError(f'{where}: unknown identity {step["by"]}')
        scope = seed['identities'][step['by']]['scope']
        if step['do'] == 'create' and step['domain'] not in scopes[scope]['domains']:
            raise SeedError(f'{where}: unknown domain {step["domain"]}')
        if 'to' in step and seed['identities'].get(step['to'], {}).get('scope') != scope:
            raise SeedError(f'{where}: unknown assignee {step["to"]}')
        cited = list(placeholders([step.get('payload'), step.get('params'), step.get('refs'), step.get('declaration')]))
        if 'target' in step:
            cited.append(('object', step['target']))
        for kind, key in cited:
            if kind == 'object' and built.get(key) != scope:
                raise SeedError(f'{where}: {key} does not exist yet in this scope')
            if kind == 'principal' and seed['identities'].get(key, {}).get('scope') != scope:
                raise SeedError(f'{where}: unknown identity {key}')
            if kind == 'event':
                raise SeedError(f'{where}: a step cannot cite an event')
        if step.get('key'):
            if step['key'] in used or step['key'] in seed['identities']:
                raise SeedError(f'{where}: key {step["key"]} is used twice')
            if step['do'] not in MAKES_OBJECT | MAKES_EVENT:
                raise SeedError(f'{where}: this step does not produce anything to key')
            used.add(step['key'])
            if step['do'] in MAKES_OBJECT:
                built[step['key']] = scope


def validate_gold(answers: dict, seed: dict) -> None:
    """标准答案：六问齐全、各有应引用的对象、块或事件；八类反例齐全、各有诱饵；引用的键都在播种里。"""
    if answers.get('format') != 'tkos-world-gold/0.1':
        raise SeedError('unknown gold format')
    objects, events = produced(seed)
    if answers.get('approver') not in {identity['display_name'] for identity in seed['identities'].values()}:
        raise SeedError('the approver is named by the role name of one of the seed identities')
    if objects.get(answers['start'], {}).get('type') != 'Activity':
        raise SeedError('the replay starts from an Activity of the seed')
    if [question['id'] for question in answers['questions']] != list(QUESTIONS):
        raise SeedError('the gold answers the six questions in order')
    if [item['category'] for item in answers['counterexamples']] != list(COUNTEREXAMPLES):
        raise SeedError('the gold lists the eight counterexample categories in order')
    cited = [(question['id'], ref) for question in answers['questions'] for ref in question['expected']]
    cited += [(item['category'], ref) for item in answers['counterexamples'] for ref in item['decoys']]
    for question in answers['questions']:
        if not question['question'].strip() or not question['answer'].strip() or not question['expected']:
            raise SeedError(f"{question['id']}: a question, an answer and expected references are needed")
    for item in answers['counterexamples']:
        if not item['decoys'] or not item['rule'].strip():
            raise SeedError(f"{item['category']}: a rule and at least one decoy are needed")
    expected = {ref for question in answers['questions'] for ref in question['expected']}
    if expected & {ref for item in answers['counterexamples'] for ref in item['decoys']}:
        raise SeedError('a decoy cannot also be an expected reference')
    for owner, ref in cited:
        found = list(placeholders(ref))
        if len(found) != 1 or found[0][0] == 'principal':
            raise SeedError(f'{owner}: {ref} is not an object or event reference')
        kind, key = found[0]
        if (kind == 'object' and key not in objects) or (kind == 'event' and key not in events):
            raise SeedError(f'{owner}: {ref} names nothing the seed produces')
