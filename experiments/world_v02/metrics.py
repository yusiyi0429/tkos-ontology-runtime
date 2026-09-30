"""四种取法对照（票 #68，规格 #46 第十节）的指标：召回、可追溯、确定性与反例作门，成本只报告。纯函数，不连库、不接模型。
结构沿用 0.1 的 experiments/world_v01/metrics.py，每组同一口径、各自判定；0.1 的模块一行不改。

一次运行的记录（load_runs）：run.json（场景、问题、第几次、状态）、answer.json（断言列表），以及取到的集合、交给模型的
材料里出现过的引用与交给模型的字符数——全量、固定路径、RAG 三组由跑器写进 context.json（与这一问的准备结果相同），
模型遍历组取 mcp/ 下的运行日志。

各项门用哪个集合（BASIS，原样写进 summary.json，报告从那里取）：

- 取到：全量组是全部分块（scope 内每个对象的最新版与每条事件，按构造最大）；固定路径组是上下文包里带着内容回来的
  （tkos_world_mcp.server._content 的口径）；RAG 组是装入的分块；模型遍历组是 MCP 运行日志的 read_refs 与
  read_event_ids。全量、固定路径、RAG 三组每次运行取到的集合相同。
- 召回（门 ≥ 0.9）：标准答案应引项里被取到的比例，所有有效运行合计。全量组按构造最大（应引项都在 scope 里就是 1）。
- 可追溯（门 100%）：refs 非空、且每条引用（counterexamples.normalize 归一后）都在这次运行交给模型的材料里出现过的
  断言的比例，所有有效运行合计。出现过：带着内容，或只以引用形式（上层生命周期后面写的推出事件、块内引用等）；全量、
  固定路径、RAG 三组按写进提示词的文本（context.json 的 shown），模型遍历组按 MCP 运行日志的 refs 与 event_ids。
  它包含取到的集合；召回仍按取到的集合（2026-09-29 定，#74 彩排之后）。
- 投影项（#79 的 Mission「Task 预期结果与质量标准」、责任单元「战役引用」，读取时从下级对象投影；2026-09-30 定，#84）：
  召回只算取到，可追溯算看过。投影里给了正文的组件（Task 定义块里的工作结果与成功 / 验收标准：读投影给完整组件视图，
  取上下文的 Markdown 逐条写正文）算取到；只给标题与引用的下级对象（Task、Mission 本身）只算看过——进可追溯的材料，
  不进召回。全量、RAG 的对象表头只列下级对象的引用，组件正文在下级对象自己的分块里，已是这个口径。固定路径与模型
  遍历的取到由 MCP 的 _content 判（context.json 的 refs、运行日志的 read_refs），它现在把投影项的下级对象引用也算
  取到；按这条口径要改 src/tkos_world_mcp/server.py 的 _content，待定。load_runs 不另做修正：取到只有 _content
  这一个落点，两组口径一致。实验 E 五个场景都从 Task 出发，取上下文只给出发对象投影项，固定路径组的包里没有投影项。
- 确定性（门 ≥ 0.9）：同一问各次有效运行取到的集合两两 Jaccard 的平均，再对各问平均。全量、固定路径、RAG 三组按构造
  为 1，不是实验发现。有效运行不足规定次数的问列为 short，确定性就不算达标。
- 反例（门：五个场景零出现）：experiments.world_v02.counterexamples.judge 按这一问判回答的断言（所引）；每次运行每问
  每类出现记一次。
- 另报（不作门）：所引集合一致率（同一问各次运行所引集合的 Jaccard，比回答的稳定性）；回答覆盖（应引项里被回答引到的
  比例：完全相同；应引项是对象时，引了同一版本的该对象的块或组件也算；是块时，引了同一版本这一块里的组件也算）。
- 成本（只报告）：交给模型的字符数。模型遍历组是一次运行里工具返回给模型的字符数（运行日志 chars 之和）；其余三组是
  写进提示词的上下文字符数。逐问平均，再对各问平均；另给与全量组的比。

没有有效运行的组或场景，各项门都不算达标（verdict 为假），valid 为假。
"""
from __future__ import annotations

from itertools import combinations
import json
from pathlib import Path
from statistics import mean

from . import counterexamples, spec

GROUPS = {'full': '全量塞入', 'fixed': '固定路径', 'traverse': '模型遍历', 'rag': 'RAG'}
QUESTION_NAMES = {'why': '为什么', 'what': '做什么', 'who': '谁负责', 'now': '现在怎样', 'happened': '发生了什么',
                  'basis': '凭什么'}
GATES = {'recall': 0.9, 'traceability': 1.0, 'determinism': 0.9}
TAKEN = {'full': '全部分块：scope 内每个对象的最新版与每条事件（按构造最大）',
         'fixed': '上下文包里带着内容回来的对象、块、组件与事件（_content 口径）',
         'traverse': 'MCP 运行日志的 read_refs 与 read_event_ids',
         'rag': '装入的分块'}
BASIS = {
    'taken': TAKEN,
    'recall': '标准答案应引项里被取到的比例（取到的集合），所有有效运行合计',
    'traceability': '断言所引（归一后）都在这次运行交给模型的材料里出现过（带内容或只以引用形式）的比例，所有有效运行合计',
    'projection': ('投影项（Mission 的「Task 预期结果与质量标准」、责任单元的「战役引用」）：给了正文的组件（Task 的工作结果'
                   '与成功 / 验收标准）算取到；只给标题与引用的下级对象（Task、Mission 本身）只算看过，进可追溯、不进召回。'
                   '全量、RAG 的表头只列下级对象的引用，已是这个口径；固定路径与模型遍历按 MCP 的 _content 判，它现在把'
                   '下级对象的引用也算取到，要改 src/tkos_world_mcp/server.py，待定（#84）'),
    'determinism': '同一问各次运行取到的集合两两 Jaccard 的平均，再对各问平均；全量、固定路径、RAG 按构造为 1',
    'counterexamples': '断言所引，按 counterexamples.judge 逐问判；五个场景零出现',
    'determinism_cited': '另报：同一问各次运行所引集合的 Jaccard',
    'answer_coverage': '另报：应引项里被回答引到的比例（所引覆盖）',
    'cost': '只报告：模型遍历组是工具返回给模型的字符数，其余三组是写进提示词的上下文字符数',
    'by_construction': ['全量、固定路径、RAG 三组每次运行取到的集合相同，确定性按构造为 1',
                        '全量组的召回按构造最大：取到的就是 scope 里的全部内容'],
}


def key(scenario: str, question: str) -> str:
    return f'{scenario}.{question}'


def resolve(answers: dict, manifest: dict) -> dict[str, dict]:
    """各场景的标准答案换成这次播种的引用（counterexamples.resolve），按场景 id。"""
    return {item['id']: counterexamples.resolve(item, manifest) for item in answers['scenario_answers']}


# ------------------------------------------------------------------ runs
def _log(folder: Path) -> list[dict]:
    return [json.loads(line) for log in sorted((folder / 'mcp').glob('*.jsonl'))
            for line in log.read_text().splitlines() if line.strip()]


def load_runs(folder: Path) -> list[dict]:
    """一组的全部运行：每个子目录一次。取到的集合与字符数取自 context.json（全量、固定路径、RAG），没有就取 mcp/ 下的
    运行日志（模型遍历）。这一组没跑（目录不在）就没有运行。投影项在这里不另做处理：取到是 context.json 的 refs 或
    日志的 read_refs（_content 口径），看过另加 context.json 的 shown 或日志的 refs；投影项怎么分由 _content 定（见
    模块说明与 BASIS 的 projection）。"""
    runs = []
    for path in sorted(item for item in (folder.iterdir() if folder.is_dir() else ()) if (item / 'run.json').exists()):
        run = json.loads((path / 'run.json').read_text())
        answer = path / 'answer.json'
        run['answer'] = json.loads(answer.read_text()) if answer.exists() else None
        context = path / 'context.json'
        if context.exists():
            given = json.loads(context.read_text())
            run['taken'] = set(given['refs']) | {f'event:{event}' for event in given['event_ids']}
            run['shown'] = run['taken'] | set(given.get('shown', ()))
            run['chars'] = given['chars']
        else:
            log = _log(path)
            run['taken'] = {ref for line in log for ref in line.get('read_refs', [])} \
                | {f'event:{event}' for line in log for event in line.get('read_event_ids', [])}
            run['shown'] = run['taken'] | {ref for line in log for ref in line.get('refs', [])} \
                | {f'event:{event}' for line in log for event in line.get('event_ids', [])}
            run['chars'] = sum(line['chars'] for line in log)
            run['calls'] = len(log)
        runs.append(run)
    return runs


# ------------------------------------------------------------------ one run
def covers(expected: str, cited: set[str]) -> bool:
    """回答引到了一条应引项：完全相同；应引项是对象时，引了同一版本该对象的块或组件也算；是块时，引了同一版本这一块里
    的组件也算。事件与组件要完全相同。"""
    if expected in cited:
        return True
    want = counterexamples.parse(expected)
    if want is None or 'event_id' in want or want['component'] is not None:
        return False
    for ref in cited:
        got = counterexamples.parse(ref)
        if got and got.get('object_id') == want['object_id'] and got['version'] == want['version'] \
                and (want['block'] is None or got['block'] == want['block']):
            return True
    return False


def score(run: dict, expected: list[str], gold: dict, question: str) -> dict:
    """一次有效运行在一问上的得分：召回、可追溯（分子、分母）、所引集合、回答覆盖、各类反例是否出现、字符数。"""
    got = run['taken']
    result = {'recall': [sum(ref in got for ref in expected), len(expected)], 'taken': sorted(got),
              'chars': run['chars']}
    claims = (run['answer'] or {}).get('claims', [])
    refs = [{counterexamples.normalize(ref) for ref in claim['refs']} for claim in claims]
    cited = set().union(*refs) if refs else set()
    judged = counterexamples.judge(claims, gold, question)
    seen = run.get('shown', got)  # 交给模型的材料里出现过的，包含取到的集合
    result.update(traceability=[sum(bool(item) and item <= seen for item in refs), len(claims)], cited=sorted(cited),
                  answer_coverage=[sum(covers(ref, cited) for ref in expected), len(expected)],
                  counterexamples={category: int(item['occurred']) for category, item in judged.items()})
    return result


# ------------------------------------------------------------------ aggregation
def jaccard(sets: list[set[str]]) -> float | None:
    """多次运行的集合两两 Jaccard 的平均；不足两次为 None，两边都空算一致。"""
    if len(sets) < 2:
        return None
    return mean(len(a & b) / len(a | b) if a | b else 1.0 for a, b in combinations(sets, 2))


def pooled(parts: list[list[int]]) -> list[int]:
    return [sum(part[0] for part in parts), sum(part[1] for part in parts)]


def ratio(part: list[int]) -> float | None:
    return part[0] / part[1] if part[1] else None


def average(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return mean(present) if present else None


def added(counts: list[dict[str, int]]) -> dict[str, int]:
    total: dict[str, int] = {}
    for count in counts:
        for category, value in count.items():
            total[category] = total.get(category, 0) + value
    return total


def question(scored: list[dict]) -> dict:
    """一问的各次有效运行合在一起。"""
    return {'runs': len(scored), 'recall': pooled([item['recall'] for item in scored]),
            'traceability': pooled([item['traceability'] for item in scored]),
            'determinism': jaccard([set(item['taken']) for item in scored]),
            'determinism_cited': jaccard([set(item['cited']) for item in scored]),
            'answer_coverage': pooled([item['answer_coverage'] for item in scored]),
            'counterexamples': added([item['counterexamples'] for item in scored]),
            'chars': average([item['chars'] for item in scored])}


def aggregate(questions: dict[str, dict], attempts: int) -> dict:
    """若干问合成一份（一个场景或整组）：比例按分子分母合计，确定性与字符对各问平均；对照门给出判定。"""
    items = list(questions.values())
    result = {'recall': ratio(pooled([item['recall'] for item in items])),
              'recall_counts': pooled([item['recall'] for item in items]),
              'traceability': ratio(pooled([item['traceability'] for item in items])),
              'traceability_counts': pooled([item['traceability'] for item in items]),
              'determinism': average([item['determinism'] for item in items]),
              'determinism_cited': average([item['determinism_cited'] for item in items]),
              'answer_coverage': ratio(pooled([item['answer_coverage'] for item in items])),
              'answer_coverage_counts': pooled([item['answer_coverage'] for item in items]),
              'counterexamples': added([item['counterexamples'] for item in items]),
              'chars': average([item['chars'] for item in items]),
              'short': {name: item['runs'] for name, item in questions.items() if item['runs'] < attempts},
              'valid': any(item['runs'] for item in items)}
    result['verdict'] = verdict(result)
    return result


def verdict(result: dict) -> dict[str, bool]:
    """四项门；没有有效运行的一律不达标，有效运行不足的问（short）让确定性不达标。"""
    valid = result['valid']
    gates = {'recall': valid and (result['recall'] or 0) >= GATES['recall'],
             'traceability': valid and (result['traceability'] or 0) >= GATES['traceability'],
             'determinism': valid and not result['short'] and (result['determinism'] or 0) >= GATES['determinism'],
             'counterexamples': valid and sum(result['counterexamples'].values()) == 0}
    return {**gates, 'passed': all(gates.values())}


def statuses(runs: list[dict]) -> dict[str, int]:
    counted: dict[str, int] = {}
    for run in runs:
        counted[run['status']] = counted.get(run['status'], 0) + 1
    return counted


def group(golds: dict[str, dict], runs: list[dict], attempts: int, full_chars: int | None) -> dict:
    """一组：逐问、逐场景与合计，各有对照门的判定；成本另给与全量组的比。"""
    scenarios, every = {}, {}
    for scenario, gold in golds.items():
        questions = {}
        for name, expected in gold['questions'].items():
            valid = [run for run in runs if run['scenario'] == scenario and run['question'] == name
                     and run['status'] == 'ok']
            questions[name] = question([score(run, expected, gold, name) for run in valid])
            every[key(scenario, name)] = questions[name]
        scenarios[scenario] = {**aggregate(questions, attempts), 'questions': questions}
    total = aggregate(every, attempts)
    total['of_full'] = total['chars'] / full_chars if total['chars'] is not None and full_chars else None
    return {'runs': statuses(runs), **total, 'scenarios': scenarios}


def summarize(golds: dict[str, dict], runs: dict[str, list[dict]], attempts: int, prepared: dict) -> dict:
    """各组（组名 -> 运行）的指标。prepared 是准备结果（每问三组的上下文、裁剪与取到召回），原样带进 summary，
    报告与触发检查都从这里读。"""
    full_chars = prepared['full']['chars']
    return {'format': 'tkos-world-02-retrieval-summary/0.1',
            'groups': {name: group(golds, runs.get(name, []), attempts, full_chars) for name in GROUPS},
            'names': {'groups': GROUPS, 'scenarios': spec.SCENARIOS, 'questions': QUESTION_NAMES,
                      'counterexamples': {key: item['name'] for key, item in spec.COUNTEREXAMPLES.items()}},
            'gates': GATES, 'basis': BASIS, 'attempts': attempts, 'prepared': prepared}
