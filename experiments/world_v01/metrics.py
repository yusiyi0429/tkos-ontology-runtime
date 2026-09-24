"""静态截面实验的指标（票 #29）：从 MCP 运行日志与结构化回答算召回、可追溯、预算、确定性与八类反例。
纯函数，不连库、不接模型。模型遍历可以有几组（A 给四个读工具，A0 不给取上下文，#32 D），每组同一口径、各自判定；
B 组是固定路径的取上下文，作预算的对照。

- 取到：运行日志的 read_refs 与 read_event_ids，即带着内容回来的对象版本、块与事件；只以引用形式出现过的不算。
- 召回：标准答案应引用的对象、块与事件里被取到的比例（所有有效运行合计）。
- 可追溯：refs 非空且每条引用都被这次运行取到的断言的比例（所有有效运行合计）；gap 断言也要引出显示为空的块或对象。
- 预算：模型遍历组一次运行里工具返回给模型的字符数；B 组按渲染后 Markdown 的字符数（规格的预算口径），另记经
  MCP 返回的 JSON 字符数。都先逐问平均、再对各问平均；逐问比，任一问超过全量塞入的一半或超过 B 即不达标。
- 确定性：同一问各次运行取到的集合（运行日志）两两 Jaccard 的平均，再对各问取平均；另记所引集合的一致率。
  有效运行不足规定次数的问列为 short，确定性就不算达标。
- 反例：断言引了该类诱饵（诱饵不带块时指整个对象的任何版本与块）；引了比这次播种更旧的版本一律记为旧版本对象；
  fact 断言引空块一律记为「空块被当作有内容」，gap 断言引空块不算。B 组没有回答，只记包里混进的诱饵。
- 回答引用的覆盖（另算，不是通过标准，#32 D）：标准答案应引用的里被回答引到的比例（所有有效运行合计）；
  期望引用是整个对象时，引了它的某个版本或块也算，块与事件要完全相同。
- 调用模式（另算）：每次运行的工具调用数、与同一运行之前某次完全相同的重复调用、取上下文的次数、
  请求过的对象数，以及读了起点以外对象的运行数（给了起点才算）。
"""
from __future__ import annotations

from itertools import combinations
import json
from pathlib import Path
import re
from statistics import mean

from . import spec

_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
OLD, EMPTY = 'old_version', 'empty_block_as_content'
# 通过标准（规格；写进报告，不作为 0.1 验收门）：召回、可追溯、确定性的下限，模型遍历组长度与全量塞入、B 组之比的上限。
PASS = {'recall': 0.9, 'traceability': 1.0, 'determinism': 0.9, 'of_full': 0.5, 'of_b': 1.0}


def resolve(answers: dict, manifest: dict) -> dict:
    """标准答案的占位换成这次播种的引用：`@键` 为播种结束时的最新版本，`event:键` 为 `event:<event_id>`；
    另给出每个对象播种结束时的最新版本，用来认旧版本。"""
    objects, events = manifest['objects'], manifest['events']

    def concrete(ref: str) -> str:
        kind, key = next(spec.placeholders(ref))
        return f'event:{events[key]}' if kind == 'event' else spec.resolve(ref, objects, {})

    return {'questions': {item['id']: {'question': item['question'], 'expected': [concrete(ref) for ref in item['expected']]}
                          for item in answers['questions']},
            'decoys': {item['category']: [concrete(ref) for ref in item['decoys']] for item in answers['counterexamples']},
            'latest': {item['object_id']: item['version'] for item in objects.values()}}


def load_runs(folder: Path) -> list[dict]:
    """实验输出目录下的运行：每个子目录一次，含 run.json、mcp/ 下的运行日志与（模型遍历组的）answer.json。
    这一组没跑（目录不在）就没有运行。"""
    runs = []
    for path in sorted(item for item in (folder.iterdir() if folder.is_dir() else ()) if (item / 'run.json').exists()):
        run = json.loads((path / 'run.json').read_text())
        run['log'] = [json.loads(line) for log in sorted((path / 'mcp').glob('*.jsonl'))
                      for line in log.read_text().splitlines() if line.strip()]
        answer = path / 'answer.json'
        run['answer'] = json.loads(answer.read_text()) if answer.exists() else None
        runs.append(run)
    return runs


def normalize(ref: str) -> str:
    """回答里的引用写法归一：去空白、id 小写；不带 `@` 的裸 UUID 当作事件。"""
    ref = ref.strip()
    if ref.lower().startswith('event:'):
        return 'event:' + ref[6:].strip().lower()
    head, _, block = ref.partition('#')
    head = head.lower()
    if _UUID.fullmatch(head):
        return f'event:{head}'
    return head + (f'#{block}' if block else '')


def taken(log: list[dict]) -> set[str]:
    """一次运行取到的对象版本、块（`<id>@<版本>[#块]`）与事件（`event:<id>`）。"""
    found: set[str] = set()
    for line in log:
        found.update(line.get('read_refs', []))
        found.update(f'event:{event}' for event in line.get('read_event_ids', []))
    return found


def _hits(ref: str, category: str, gold: dict) -> bool:
    """引用落在该类诱饵上：诱饵带块或是事件时要完全相同，诱饵是对象时该对象的任何版本与块都算；
    旧版本对象另认一切比这次播种更旧的版本。"""
    object_id, _, rest = ref.partition('@')
    version = rest.partition('#')[0]
    if category == OLD and version.isdigit() and int(version) < gold['latest'].get(object_id, 0):
        return True
    return any(ref == decoy if decoy.startswith('event:') or '#' in decoy else object_id == decoy.partition('@')[0]
               for decoy in gold['decoys'][category])


def covers(expected: str, cited: set[str]) -> bool:
    """回答引用覆盖了一条期望引用：完全相同；期望是整个对象（`<id>@<版本>`）时，引了该对象的任何版本或块也算。"""
    if expected in cited:
        return True
    if expected.startswith('event:') or '#' in expected:
        return False
    object_id = expected.partition('@')[0]
    return any(not ref.startswith('event:') and ref.partition('@')[0] == object_id for ref in cited)


def calls(log: list[dict]) -> dict:
    """一次运行的调用模式：调用数、与之前某次完全相同的重复调用数、取上下文次数、请求过的对象。"""
    seen: list[tuple[str, str]] = []
    repeated = 0
    for line in log:
        key = (line.get('tool'), json.dumps(line.get('arguments'), sort_keys=True, ensure_ascii=False))
        repeated += key in seen
        seen.append(key)
    return {'calls': len(log), 'repeated_calls': repeated,
            'context_calls': sum(line.get('tool') == 'world_get_context' for line in log),
            'objects': sorted({(line.get('arguments') or {}).get('object_id') for line in log} - {None})}


def score(run: dict, expected: list[str], gold: dict, empty_blocks: set[str]) -> dict:
    """一次运行：召回、可追溯（分子、分母）、各类反例次数、取到的诱饵、返回字符数、取到与所引的集合、
    回答引用的覆盖与调用模式。"""
    got = taken(run['log'])
    kinds = [category for category in gold['decoys'] if category != EMPTY]
    result = {'recall': [sum(ref in got for ref in expected), len(expected)],
              'chars': sum(line['chars'] for line in run['log']),
              'used_chars': sum(line.get('used_chars') or 0 for line in run['log']),
              'taken': sorted(got),
              'decoys_taken': {category: sum(_hits(ref, category, gold) for ref in got) for category in kinds},
              **calls(run['log'])}
    if run.get('answer') is None:
        return result
    claims = [{'kind': claim['kind'], 'refs': {normalize(ref) for ref in claim['refs']}} for claim in run['answer']['claims']]
    counted = {category: sum(any(_hits(ref, category, gold) for ref in claim['refs']) for claim in claims)
               for category in kinds}
    if EMPTY in gold['decoys']:
        counted[EMPTY] = sum(claim['kind'] == 'fact' and bool(claim['refs'] & empty_blocks) for claim in claims)
    cited = set().union(*(claim['refs'] for claim in claims))
    result.update(traceability=[sum(bool(claim['refs']) and claim['refs'] <= got for claim in claims), len(claims)],
                  counterexamples={category: counted[category] for category in gold['decoys']},
                  cited=sorted(cited), answer_coverage=[sum(covers(ref, cited) for ref in expected), len(expected)])
    return result


def jaccard(sets: list[set[str]]) -> float | None:
    """多次运行的集合两两 Jaccard 的平均；不足两次为 None，两边都空算一致。"""
    if len(sets) < 2:
        return None
    return mean(len(a & b) / len(a | b) if a | b else 1.0 for a, b in combinations(sets, 2))


def _pooled(parts: list[list[int]]) -> list[int]:
    """[命中, 总数] 逐项相加。"""
    return [sum(part[0] for part in parts), sum(part[1] for part in parts)]


def _added(counts: list[dict[str, int]]) -> dict[str, int]:
    """各类计数逐类相加。"""
    return {key: sum(count[key] for count in counts) for key in counts[0]} if counts else {}


def _ratio(part: list[int]) -> float | None:
    return part[0] / part[1] if part[1] else None


def _mean(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return mean(present) if present else None


def _statuses(runs: list[dict]) -> dict[str, int]:
    counted: dict[str, int] = {}
    for run in runs:
        counted[run['status']] = counted.get(run['status'], 0) + 1
    return counted


def _traversal(gold: dict, runs: list[dict], b: dict, world: dict, attempts: int, empty_blocks: set[str],
               start: str | None = None) -> dict:
    """一个模型作答组：逐问与合计指标、与全量塞入及 B 组的预算比值、对照通过标准的结论，另算回答引用的覆盖与调用模式。"""
    questions, scored_all = {}, []
    for key, item in gold['questions'].items():
        scored = [score(run, item['expected'], gold, empty_blocks)
                  for run in runs if run['question'] == key and run['status'] == 'ok']
        scored_all += scored
        questions[key] = {'runs': len(scored), 'recall': _pooled([run['recall'] for run in scored]),
                          'traceability': _pooled([run['traceability'] for run in scored]),
                          'determinism': jaccard([set(run['taken']) for run in scored]),
                          'determinism_cited': jaccard([set(run['cited']) for run in scored]),
                          'chars': _mean([run['chars'] for run in scored]),
                          'counterexamples': _added([run['counterexamples'] for run in scored]),
                          'decoys_taken': _added([run['decoys_taken'] for run in scored]),
                          'answer_coverage': _pooled([run['answer_coverage'] for run in scored])}
    chars, b_chars = _mean([item['chars'] for item in questions.values()]), _mean([item['used_chars'] for item in b.values()])
    b_json = _mean([item['chars'] for item in b.values()])
    half = PASS['of_full'] * world['full_chars']
    over = [key for key, item in questions.items() if item['chars'] is None or b[key]['used_chars'] is None
            or item['chars'] > half or item['chars'] > PASS['of_b'] * b[key]['used_chars']]
    group = {'runs': _statuses(runs), 'questions': questions,
             'short': {key: item['runs'] for key, item in questions.items() if item['runs'] < attempts},
             'recall': _ratio(_pooled([run['recall'] for run in scored_all])),
             'traceability': _ratio(_pooled([run['traceability'] for run in scored_all])),
             'determinism': _mean([item['determinism'] for item in questions.values()]),
             'determinism_cited': _mean([item['determinism_cited'] for item in questions.values()]),
             'chars': chars,
             'counterexamples': _added([run['counterexamples'] for run in scored_all]),
             'decoys_taken': _added([run['decoys_taken'] for run in scored_all]),
             'budget': {'of_full': chars and chars / world['full_chars'], 'of_b': chars and b_chars and chars / b_chars,
                        'of_b_json': chars and b_json and chars / b_json, 'over': over},
             'answer_coverage': _ratio(_pooled([run['answer_coverage'] for run in scored_all])),
             'answer_coverage_counts': _pooled([run['answer_coverage'] for run in scored_all]),
             'calls': {'runs': len(scored_all), 'per_run': _mean([run['calls'] for run in scored_all]),
                       'repeated': sum(run['repeated_calls'] for run in scored_all),
                       'context_per_run': _mean([run['context_calls'] for run in scored_all]),
                       'left_start': None if start is None else sum(bool(set(run['objects']) - {start})
                                                                    for run in scored_all),
                       'objects_per_run': _mean([len(run['objects']) for run in scored_all])}}
    group['verdict'] = {  # 没有有效运行、或有效运行不足的，相应各项不算达标
        'recall': (group['recall'] or 0) >= PASS['recall'],
        'traceability': (group['traceability'] or 0) >= PASS['traceability'],
        'budget': not over,
        'determinism': not group['short'] and (group['determinism'] or 0) >= PASS['determinism'],
        'counterexamples': bool(scored_all) and sum(group['counterexamples'].values()) == 0,
    }
    return group


def summarize(gold: dict, traversals: dict[str, list[dict]], b_runs: list[dict], world: dict, attempts: int,
              start: str | None = None) -> dict:
    """各模型作答组（组名 -> 运行）与 B 组的逐问与合计指标；每个模型作答组各有预算比值与对照通过标准的结论
    （报告用，不作为 0.1 验收门）。start 是起点对象的 id，给了才算「离开起点」的运行数。"""
    empty_blocks = set(world['empty_blocks'])
    b = {}
    for key, item in gold['questions'].items():
        packs = [score(run, item['expected'], gold, empty_blocks)
                 for run in b_runs if run['question'] == key and run['status'] == 'ok']
        b[key] = {'runs': len(packs), 'recall': _pooled([pack['recall'] for pack in packs]),
                  'chars': _mean([pack['chars'] for pack in packs]),
                  'used_chars': _mean([pack['used_chars'] for pack in packs]),
                  'decoys_taken': _added([pack['decoys_taken'] for pack in packs])}
    summary = {name: _traversal(gold, runs, b, world, attempts, empty_blocks, start) for name, runs in traversals.items()}
    summary['b'] = {'runs': _statuses(b_runs), 'questions': b,
                    'recall': _ratio(_pooled([item['recall'] for item in b.values()])),
                    'chars': _mean([item['chars'] for item in b.values()]),
                    'used_chars': _mean([item['used_chars'] for item in b.values()]),
                    'decoys_taken': _added([item['decoys_taken'] for item in b.values() if item['runs']])}
    summary['full_chars'] = world['full_chars']
    return summary
