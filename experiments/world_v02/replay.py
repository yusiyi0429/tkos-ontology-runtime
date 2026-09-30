"""实验 E（票 #67）的回放检查：从各场景的起点出发，只经 HTTP 读投影、取上下文与只读 SQL，不写任何业务记录（取上下文
按读接口的既定行为落一行上下文包，它是时间记录）。逐场景核对：

- 六问：应引用的每一项都读得到，并且从起点可达：
  - 对象、块与组件按版本读得到，块非空、组件在那一版里；对象在起点的主干上，或是主干上某个对象的关系所指的对象
    （例如单元长期目标经 goal_ref 指到的公司级目标）；快照的主体、事件的某个主体在主干上，事件在主体的取事件里列得出。
  - 另外三条：Who 问的对象已有责任人；现状问的快照仍是其主体的最新一条，起点当前的生命周期由现状问所引的某条事件
    推出；应引用的对象若是有门对象，引到的就是它生效的修订。
  - 取上下文可恢复：从起点取一次上下文（默认预算），应引用的每一项都在包里带着内容回来（与 MCP 运行日志的「取到」
    同一口径），这一问在包的六问覆盖里答得了。
- 诱饵：都在，且各有该有的样子：
  - 另一单元的目标与约束：非空、不在主干上、在另一个域，并且经依赖主干对象的对象（referenced_by）引得到；
  - 冲突的两处：上层那处在主干上、本层那处在起点上，都非空；
  - 旧版本：确实不是最新，最新版就是生效的修订，并且主干上有对象钉着这个旧版本；
  - 过时的状态：是起点的快照，但已不是最新一条；
  - 不存在的 Activity：诱饵 Activity 在，但不在起点下面，起点下面没有任何 Activity；
  - 有内容的块：在起点上、非空。

只有全部通过，recoverable 才为真。
"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from tkos_world_mcp.server import _content
from tkos_world_mcp.tools_v02 import FACE

from . import counterexamples, spec

RECENT_DAYS = 30  # 取上下文的近期窗口，同 0.2 默认值（契约第 15.3 节），显式给出


def _pinned_refs(value: Any) -> set[str]:
    """一段读投影里出现的全部引用的业务形式（关系、块与组件里钉住的引用都算）。"""
    found: set[str] = set()
    if isinstance(value, dict):
        if isinstance(value.get('ref'), str):
            found.add(value['ref'])
        for item in value.values():
            found |= _pinned_refs(item)
    elif isinstance(value, list):
        for item in value:
            found |= _pinned_refs(item)
    return found


class Replay:
    def __init__(self, manifest: dict, reader, h, scope: dict):
        self.manifest, self.reader, self.h, self.scope = manifest, reader, h, scope
        self.types = {item['object_id']: item['type'] for item in manifest['objects'].values()}

    # ------------------------------------------------------------ reads
    def view(self, object_id: str, version: int | None = None) -> dict:
        suffix = f'?version={version}' if version else ''
        return self.reader.json('GET', f'/v1/world/objects/{object_id}{suffix}', expected={200, 404})

    def sql(self, statement: str, params: tuple) -> list[dict]:
        return self.h.sql(self.scope, statement, params)

    def spine(self, object_id: str) -> tuple[list[str], set[str], list[dict]]:
        """沿主干向上：主干上的对象、它们关系所指的对象，以及主干各层的读投影（business 组）。"""
        chain, related, views = [], set(), []
        while object_id:
            business = self.view(object_id)['business']
            chain.append(object_id)
            views.append(business)
            parent_field = spec.OBJECT_SPECS[business['object_type']]['spine_parent_field']
            parent = None
            for relation in business['relations']:
                values = relation['value'] if isinstance(relation['value'], list) else [relation['value']]
                related |= {value['object_id'] for value in values if value}
                if relation['field'] == parent_field and relation['value']:
                    parent = relation['value']['object_id']
            object_id = parent
        return chain, related, views

    def domain(self, object_id: str) -> str:
        return str(self.sql('SELECT domain_id FROM gov_objects WHERE scope_id=%s AND object_id=%s',
                            (self.scope['scope_id'], object_id))[0]['domain_id'])

    def dependents(self, chain: list[str]) -> set[str]:
        """最新修订的跨链关系列表里钉着主干上某个对象的对象（它们在主干各层的 referenced_by 里）。"""
        found = set()
        for object_id in chain:
            probe = Jsonb([{'object_id': object_id}])
            rows = self.sql("""SELECT o.object_id FROM gov_objects o JOIN gov_object_revisions r
                                 ON r.scope_id=o.scope_id AND r.revision_id=o.latest_revision_id
                                WHERE o.scope_id=%s AND (r.payload->'depends_on' @> %s OR r.payload->'contributes_to' @> %s)""",
                            (self.scope['scope_id'], probe, probe))
            found |= {str(row['object_id']) for row in rows}
        return found

    def look(self, ref: str, chain: list[str], related: set[str]) -> dict:
        """一条具体引用：读不读得到、是否非空、是否最新、是否可达；快照另给主体与是否仍是主体的最新一条。"""
        parsed = counterexamples.parse(ref)
        if 'event_id' in parsed:
            rows = self.sql('SELECT subject_refs FROM gov_world_events WHERE scope_id=%s AND event_id=%s',
                            (self.scope['scope_id'], parsed['event_id']))
            subjects = [item['object_id'] for item in rows[0]['subject_refs']] if rows else []
            listed = bool(subjects) and any(event['event_id'] == parsed['event_id'] for event in self.reader.json(
                'GET', f'/v1/world/objects/{subjects[0]}/events')['events'])
            return {'ref': ref, 'exists': listed, 'subjects': subjects, 'on_path': bool(set(subjects) & set(chain))}
        object_id, version = parsed['object_id'], parsed['version']
        shown = self.view(object_id, version)
        found = {'ref': ref, 'exists': 'object_id' in shown, 'type': self.types.get(object_id),
                 'latest': version == self.manifest_version(object_id), 'reachable': object_id in set(chain) | related}
        if not found['exists']:
            return {**found, 'on_path': False}
        snapshot = shown.get('business') is None
        body = shown if snapshot else shown['business']
        if not snapshot:
            found['responsible'] = bool(shown['identity']['responsible']['principals'])
            # 有门对象：有生效修订的引到的就是它；还没有正式内容的（例如草稿的 Strategy）引到的是最新版。
            effective = body['formal']['effective_revision_id']
            found['effective'] = (not spec.OBJECT_SPECS[body['object_type']]['gated']
                                  or effective == body['revision_id'] or (effective is None and found['latest']))
        if parsed['block']:
            block = next((item for item in body['blocks'] if item['id'] == parsed['block']), None)
            found.update(exists=block is not None, empty=bool(block and block['empty']))
            if block is not None and parsed['component']:
                found['exists'] = any(item['id'] == parsed['component'] for item in block['components'])
        if snapshot:
            subject = shown['subject_ref']['object_id']
            state = self.reader.json('GET', f'/v1/world/objects/{subject}/state')['snapshot']
            found.update(subject=subject, latest_state=state['object_id'] == object_id)
        found['on_path'] = found['reachable'] or found.get('subject') in chain
        return found

    def manifest_version(self, object_id: str) -> int | None:
        return next((item['version'] for item in self.manifest['objects'].values() if item['object_id'] == object_id), None)

    # ------------------------------------------------------------ one scenario
    def scenario(self, answers: dict) -> dict:
        gold = counterexamples.resolve(answers, self.manifest)
        start = counterexamples.parse(gold['start'])['object_id']
        chain, related, views = self.spine(start)
        started = self.view(start)
        lifecycle = started['records']['lifecycle']
        pack = self.reader.json('POST', f'/v1/world/objects/{start}/context',
                                {'question': f"实验 E 回放：{spec.SCENARIOS[answers['id']]}的六问",
                                 'recent_days': RECENT_DAYS})
        read_refs, read_events = _content(pack['context_pack'], FACE)
        taken = read_refs | {f'event:{event_id}' for event_id in read_events}
        questions = {}
        for question in answers['questions']:
            expected = gold['questions'][question['id']]
            items = [self.look(ref, chain, related) for ref in expected]
            read = all(item['exists'] and item['on_path'] and not item.get('empty') and item.get('effective', True)
                       for item in items)
            if question['id'] == 'who':  # 责任人要真指派过
                read = read and all(item['responsible'] for item in items if 'responsible' in item and '#' not in item['ref'])
            if question['id'] == 'now':  # 快照仍是最新一条；起点当前的生命周期由所引的某条事件推出
                read = read and all(item['latest_state'] for item in items if 'latest_state' in item)
                read = read and (lifecycle is None or f"event:{lifecycle['event_id']}" in expected)
            missing = [ref for ref in expected if ref not in taken]
            answered = pack['coverage'][question['id']]['answered']
            questions[question['id']] = {'recoverable': read and not missing and answered, 'read': read,
                                         'missing_from_context': missing, 'answered_in_context': answered,
                                         'items': items}
        decoys = {}
        for counter in answers['counterexamples']:
            category = counter['category']
            resolved = gold['counterexamples'][category]
            items = [self.look(item['ref'], chain, related) for item in resolved['decoys']]
            decoys[category] = self.decoy_check(category, resolved, items, start, chain, related)
        return {'start': gold['start'], 'spine': chain, 'lifecycle': lifecycle,
                'questions': questions, 'decoys': decoys,
                'context': {'context_pack_id': pack['context_pack_id'], 'used_chars': pack['budget']['used_chars'],
                            'max_chars': pack['budget']['max_chars'], 'over_budget': pack['budget']['over_budget'],
                            'trimmed': len(pack['plan']['trimmed'])}}

    def decoy_check(self, category: str, resolved: dict, items: list[dict], start: str, chain: list[str],
                    related: set[str]) -> dict:
        present = [item['exists'] and not item.get('empty') for item in items]
        ids = [counterexamples.parse(item['ref']).get('object_id') for item in items]
        if category == 'other_unit':
            reach = set()
            for dependent in self.dependents(chain) - set(chain):
                reach |= set(self.spine(dependent)[0])
            home = self.domain(start)
            for item, ok, object_id in zip(items, present, ids):
                item['as_intended'] = (ok and object_id not in chain and object_id in reach
                                       and self.domain(object_id) != home)
        elif category == 'conflict_missed':
            sides = {side: [self.look(ref, chain, related) for ref in resolved['conflict'][side]]
                     for side in ('upper', 'lower')}
            fine = (all(item['exists'] and not item.get('empty') and item['on_path'] for item in sides['upper'])
                    and all(item['exists'] and not item.get('empty')
                            and counterexamples.parse(item['ref'])['object_id'] == start for item in sides['lower']))
            for item, ok in zip(items, present):
                item['as_intended'] = ok and fine
            items = items + [{**item, 'side': side, 'as_intended': fine} for side, found in sides.items() for item in found]
        elif category == 'stale_version':
            pinned = set().union(*(_pinned_refs(view) for view in self.spine(start)[2]))
            for item, ok, object_id in zip(items, present, ids):
                latest = self.view(object_id)['business']
                version = counterexamples.parse(item['ref'])['version']
                item['as_intended'] = (ok and not item['latest']
                                       and latest['formal']['effective_revision_id'] == latest['revision_id']
                                       and any(ref.split('#')[0] == f'{object_id}@{version}' for ref in pinned))
        elif category == 'stale_state':
            for item, ok in zip(items, present):
                item['as_intended'] = ok and item.get('subject') == start and item.get('latest_state') is False
        elif category == 'phantom_activity':
            children = self.sql("""SELECT count(*) AS n FROM gov_objects o JOIN gov_object_revisions r
                                     ON r.scope_id=o.scope_id AND r.revision_id=o.latest_revision_id
                                    WHERE o.scope_id=%s AND o.object_type='Activity'
                                      AND r.payload->'parent_ref'->>'object_id'=%s""",
                                (self.scope['scope_id'], start))[0]['n']
            for item, ok, object_id in zip(items, present, ids):
                parent = self.view(object_id)['business']
                parent_id = next(relation['value']['object_id'] for relation in parent['relations']
                                 if relation['field'] == 'parent_ref')
                item['as_intended'] = ok and item['type'] == 'Activity' and parent_id != start and children == 0
        elif category == 'content_as_empty':
            for item, ok, object_id in zip(items, present, ids):
                item['as_intended'] = ok and object_id == start
        else:
            raise ValueError(f'unknown counterexample category {category}')
        return {'as_intended': all(item['as_intended'] for item in items), 'items': items}


def check(answers: dict, scenarios: dict, manifest: dict, reader, h, scope: dict) -> dict:
    """五个场景逐一回放。reader 是 scope 内一名有生效指派的人的 HTTP 客户端；h 给只读 SQL。"""
    replay = Replay(manifest, reader, h, scope)
    results = {item['id']: replay.scenario(item) for item in answers['scenario_answers']}
    summary = {name: {'questions': {key: value['recoverable'] for key, value in result['questions'].items()},
                      'decoys': {key: value['as_intended'] for key, value in result['decoys'].items()}}
               for name, result in results.items()}
    recoverable = all(all(item['questions'].values()) and all(item['decoys'].values()) for item in summary.values())
    return {'recoverable': recoverable, 'summary': summary, 'scenarios': results}
