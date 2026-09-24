"""回放检查：从起点 Activity 出发，标准答案六问应引用的对象版本、块与事件都经读投影取得到，并且从起点可达（对象在
主干上、或是主干上某个对象的关系引用所指的对象，例如单元长期目标经 goal_ref 指到的公司级目标；快照的主体、事件的
某个主体在主干上，事件经该主体的取事件列得出来）。Who 问的对象要已有责任人；现状问的快照要仍是其主体的最新一条。
八类反例的诱饵都在，且各有该有的样子：旧版本确实不是最新；别的单元的约束不在主干上；无关 Mission 的快照主体不在
主干上；已处置的 issue 与过期 artifact 所在的快照都已有更新的快照；无关事件没有落在起点到 Mission 这段执行链上；
空块确实为空；scope 外的对象在本 scope 读不到。只经 HTTP 读投影与只读 SQL，不写任何东西。"""
from __future__ import annotations

import json
from pathlib import Path

from . import spec

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = {item['type']: item for item in json.loads((ROOT / 'docs/contracts/world-registry-0.1.json').read_text())['objects']}


def check(answers: dict, seed: dict, manifest: dict, clients: dict, h, scopes: dict) -> dict:
    objects, events = manifest['objects'], manifest['events']
    start = objects[answers['start']]
    readers = {}
    for key, identity in seed['identities'].items():
        readers.setdefault(identity['scope'], clients[key])
    reader = readers[start['scope']]

    def view(obj: dict, version: int | None = None, client=None) -> dict:
        suffix = f'?version={version}' if version else ''
        return (client or readers[obj['scope']]).json('GET', f"/v1/world/objects/{obj['object_id']}{suffix}",
                                                      expected={200, 404})

    spine, types, related, node = [], [], set(), view(start)
    while True:
        spine.append(node['object_id'])
        types.append(node['object_type'])
        for item in node['relations']:
            values = item['value'] if isinstance(item['value'], list) else [item['value']]
            related |= {value['object_id'] for value in values if value}
        field = REGISTRY[node['object_type']]['spine_parent_field']
        relation = next((item['value'] for item in node['relations'] if item['field'] == field), None) if field else None
        if not relation:
            break
        node = reader.json('GET', f"/v1/world/objects/{relation['object_id']}")
    reachable = set(spine) | related
    chain = set(spine[:types.index('Mission') + 1])  # 执行链：起点、所属 Task 到 Mission

    def scope_name(key: str) -> str:
        return seed['identities'][next(step['by'] for step in seed['steps'] if step.get('key') == key)]['scope']

    def look(ref: str) -> dict:
        kind, key = next(spec.placeholders(ref))
        if kind == 'event':
            scope = scopes[scope_name(key)]
            rows = h.sql(scope, 'SELECT subject_refs FROM gov_world_events WHERE scope_id=%s AND event_id=%s',
                         (scope['scope_id'], events[key]))
            subjects = [item['object_id'] for item in rows[0]['subject_refs']] if rows else []
            listed = subjects and any(event['event_id'] == events[key] for event in readers[scope_name(key)].json(
                'GET', f'/v1/world/objects/{subjects[0]}/events')['events'])
            return {'ref': ref, 'resolved': events[key], 'exists': bool(listed), 'subjects': subjects,
                    'on_path': bool(set(subjects) & set(spine))}
        _, version, block = spec.OBJECT.fullmatch(ref).groups()
        obj = objects[key]
        chosen = int(version) if version else obj['version']
        shown = view(obj, chosen)
        found = {'ref': ref, 'resolved': f"{obj['object_id']}@{chosen}" + (f'#{block}' if block else ''),
                 'exists': 'object_id' in shown, 'latest': chosen == obj['version'], 'scope': obj['scope'],
                 'reachable': obj['object_id'] in reachable}
        if found['exists']:
            found['responsible'] = shown['attributes'].get('responsible')
        if found['exists'] and block:
            item = next((b for b in shown['blocks'] if b['id'] == block), None)
            found.update(exists=item is not None, empty=bool(item and item['empty']))
        if obj['type'] == 'StateSnapshot' and found['exists']:
            subject = shown['attributes']['subject_ref']['object_id']
            state = readers[obj['scope']].json('GET', f'/v1/world/objects/{subject}/state')['snapshot']
            found.update(subject=subject, latest_state=state['object_id'] == obj['object_id'])
        found['on_path'] = found['reachable'] or found.get('subject') in spine
        return found

    questions = {}
    for question in answers['questions']:
        items = [look(ref) for ref in question['expected']]
        ok = all(item['exists'] and item['on_path'] and not item.get('empty') for item in items)
        if question['id'] == 'who':  # 责任人要真指派过
            ok = ok and all(item['responsible'] for item in items if 'responsible' in item and '#' not in item['ref'])
        if question['id'] == 'now':  # 现状引用的快照要仍是其主体的最新一条
            ok = ok and all(item['latest_state'] for item in items if 'latest_state' in item)
        questions[question['id']] = {'recoverable': ok, 'items': items}
    decoys = {}
    for counter in answers['counterexamples']:
        items = [look(ref) for ref in counter['decoys']]
        category = counter['category']
        for item in items:
            present = item['exists'] and not item.get('empty')
            if category == 'old_version':
                item['as_intended'] = present and not item['latest']
            elif category == 'other_unit_constraint':
                item['as_intended'] = present and item['resolved'].split('@')[0] not in spine
            elif category == 'unrelated_mission_state':
                item['as_intended'] = present and item.get('subject') is not None and item['subject'] not in spine
            elif category in {'resolved_issue', 'stale_artifact'}:
                item['as_intended'] = present and item.get('latest_state') is False
            elif category == 'unrelated_event':
                item['as_intended'] = item['exists'] and not set(item['subjects']) & chain
            elif category == 'empty_block_as_content':
                item['as_intended'] = item['exists'] and item.get('empty') is True
            else:  # outside_scope
                missing = reader.json('GET', f"/v1/world/objects/{item['resolved'].split('@')[0]}", expected={404})
                item['as_intended'] = (item['exists'] and item['scope'] != start['scope']
                                       and missing['error']['code'] == 'NOT_FOUND')
        decoys[category] = {'as_intended': all(item['as_intended'] for item in items), 'items': items}
    return {
        'recoverable': all(q['recoverable'] for q in questions.values()) and all(d['as_intended'] for d in decoys.values()),
        'questions': {key: value['recoverable'] for key, value in questions.items()},
        'decoys': {key: value['as_intended'] for key, value in decoys.items()},
        'spine': spine, 'details': {'questions': questions, 'decoys': decoys},
    }
