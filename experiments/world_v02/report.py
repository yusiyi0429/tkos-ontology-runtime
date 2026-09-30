"""四种取法对照的实验报告（票 #68）：只由 summary.json 生成 Markdown，不连库、不读别的文件。报告的每个数字都取自
summary。对照实验 B 一节读 summary 的 b（#66 的观测结论经 triggers.b_conclusions 摘成）：五项观测、#66 口径的结论
（粗粒度算能表达）与由 coarse 清单算出的更严口径的结论（粗粒度也算表达不了），两种都列；没有 b 时写还缺什么。

    python -m experiments.world_v02.report --summary O3/summary.json [--output docs/world-v02-retrieval-report.md]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'docs/world-v02-retrieval-report.md'
STATUS = {'triggered': '**触发**', 'not_triggered': '未触发', 'no_data': '无数据'}
REASONS = {'over_level_cap': '超每层条数上限', 'over_budget': '超预算'}  # 取上下文检索计划里裁剪的原因
B_MISSING = ('还没有 #66 的观测结论：试用（10/12–16）后两条线执行到关闭、取证，由 b_observe 算出 observations.json，'
             '再以 `summarize --b-observations` 重算 summary。')
# #66 驱动器记下的写法（运行日志 steps[].writing，docs/world-v02-experiment-b.md 第 4 节）
WRITINGS = {'plan_item': '改计划条目', 'task_subject': '以 Task 为主体记下', 'task_level': 'Task 一级的同名动作',
            'activity': 'Activity', 'task': 'Task', 'mission': 'Mission', 'issue': '问题'}
EXPRESSIONS = {'native': '原生', 'coarse': '粗粒度', 'inexpressible': '表达不了', None: '—'}
ACTIVITY = {'object': 'Activity 留作对象', 'component': 'Activity 降为组件'}
REHEARSAL = ('> **彩排（票 #74），结果不作数**：标准答案未经 E&O DRI 批准，Company 与 Strategy 仍是占位材料；'
             '这次运行只用来检验跑器、量耗时与成本。')


def _ratio(value: float | None, counts: list[int] | None = None) -> str:
    if value is None:
        return '—'
    return f'{value:.2f}' + (f'（{counts[0]}/{counts[1]}）' if counts else '')


def _number(value: float | int | None) -> str:
    return '—' if value is None else f'{round(value):,}'


def _mark(passed: bool) -> str:
    return '✓' if passed else '✗'


def _table(head: list[str], rows: list[list[str]]) -> list[str]:
    return ['| ' + ' | '.join(head) + ' |', '|' + '-|' * len(head)] + ['| ' + ' | '.join(row) + ' |' for row in rows]


def _setup(summary: dict) -> list[str]:
    setup, prepared = summary['setup'], summary['prepared']
    corpus = prepared['corpus']
    lines = ['## 一、实验设置', '']
    if setup:
        lines += [f"- 模型：{setup['model']}，推理档 {setup['effort']}；{setup['codex']}；{setup['started_at']}（UTC）开跑。",
                  f"- 代码：提交 `{setup['source']['commit'][:12]}`"
                  + ('；src/ 或 experiments/ 有未提交改动：' + '、'.join(f'`{path}`' for path in setup['source']['modified_paths'])
                     if setup['source']['src_or_experiments_modified'] else '，src/ 与 experiments/ 没有未提交改动。'),
                  f"- 身份：{setup['identity']}，契约 {setup['contract_version']}；每问 {setup['attempts']} 次。"]
    else:
        lines.append('- 模型：**未运行**。这份 summary 只有准备结果（不调模型），模型作答的各项都是「—」。')
    lines += [f"- 数据：实验 E 的播种（内容哈希 `{prepared['content_sha256'][:12]}…`）。scope 里业务对象 {corpus['objects']} 个、"
              f"状态快照 {corpus['snapshots']} 条、事件 {corpus['events']} 条，切成 "
              + '、'.join(f'{kind} {count}' for kind, count in corpus['chunks'].items()) + ' 个分块。',
              f"- 预算：固定路径用取上下文的默认预算，这次返回的是 {_budgets(prepared)} 字符；RAG 同成本对照，每问装入"
              f"上限等于固定路径这一问交给模型的字符数（写进提示词的取上下文返回）；全量文本 "
              f"{prepared['full']['chars']:,} 字符。",
              f"- 准备阶段的核对：每个分块只带一条自己的引用 {_mark(prepared['checks']['chunks_single_ref'])}；"
              f"取到的每一项都写在交给模型的文本里 {_mark(prepared['checks']['refs_in_text'])}；"
              f"都能经读投影读回 {_mark(prepared['checks']['refs_read_back'])}。", '']
    return lines


def _budgets(prepared: dict) -> str:
    """这次取上下文返回的预算（各问的 budget.max_chars，不写死）。"""
    return '、'.join(f'{value:,}' for value in sorted({item['contexts']['fixed']['max_chars']
                                                       for item in prepared['questions'].values()}))


def _conclusion(summary: dict) -> list[str]:
    names, groups = summary['names']['groups'], summary['groups']
    gates = summary['gates']
    rows = [
        ['有效运行 / 全部'] + [f"{sum(run['runs'] for run in _questions(item))} / {sum(item['runs'].values())}"
                          for item in groups.values()],
        [f"召回（≥ {gates['recall']}）"] + [f"{_ratio(item['recall'], item['recall_counts'])} {_mark(item['verdict']['recall'])}"
                                           for item in groups.values()],
        ['可追溯（100%）'] + [f"{_ratio(item['traceability'], item['traceability_counts'])} "
                            f"{_mark(item['verdict']['traceability'])}" for item in groups.values()],
        [f"确定性（≥ {gates['determinism']}，取到的集合）"]
        + [f"{_ratio(item['determinism'])}{'，short ' + str(len(item['short'])) + ' 问' if item['short'] else ''} "
           f"{_mark(item['verdict']['determinism'])}" for item in groups.values()],
        ['反例（五个场景零出现）'] + [f"{sum(item['counterexamples'].values())} {_mark(item['verdict']['counterexamples'])}"
                              for item in groups.values()],
        ['**四项门**'] + [('**通过**' if item['verdict']['passed'] else '不通过') for item in groups.values()],
        ['所引集合一致率（另报）'] + [_ratio(item['determinism_cited']) for item in groups.values()],
        ['回答覆盖（另报）'] + [_ratio(item['answer_coverage'], item['answer_coverage_counts']) for item in groups.values()],
        ['交给模型的字符（成本，只报告）'] + [_number(item['chars']) for item in groups.values()],
        ['相对全量塞入'] + [_ratio(item['of_full']) for item in groups.values()],
    ]
    lines = ['## 二、结论', '', '四组各自判定。没有有效运行的组各项门都不算达标。', '']
    lines += _table(['指标'] + list(names.values()), rows)
    lines += ['', '按构造的结果（不是实验发现）：', '']
    lines += [f'- {text}。' for text in summary['basis']['by_construction']]
    return lines + ['']


def _questions(group: dict) -> list[dict]:
    return [question for scenario in group['scenarios'].values() for question in scenario['questions'].values()]


def _basis(summary: dict) -> list[str]:
    basis, names = summary['basis'], summary['names']['groups']
    lines = ['## 三、口径：每组、每项门用哪个集合', '']
    lines += _table(['组', '取到的集合'], [[names[name], text] for name, text in basis['taken'].items()])
    lines += ['']
    lines += [f"- **{label}**：{basis[key]}。" for key, label in (
        ('recall', '召回'), ('traceability', '可追溯'), ('projection', '投影项'), ('determinism', '确定性'),
        ('counterexamples', '反例'), ('determinism_cited', '所引集合一致率'), ('answer_coverage', '回答覆盖'),
        ('cost', '成本')) if key in basis]  # #84 之前的 summary.json 没有 projection
    return lines + ['']


def _scenarios(summary: dict) -> list[str]:
    names = summary['names']
    rows = []
    for scenario, title in names['scenarios'].items():
        for group, item in summary['groups'].items():
            found = item['scenarios'][scenario]
            rows.append([title, names['groups'][group], _ratio(found['recall'], found['recall_counts']),
                         _ratio(found['traceability'], found['traceability_counts']), _ratio(found['determinism']),
                         str(sum(found['counterexamples'].values())),
                         '通过' if found['verdict']['passed'] else '不通过'])
    lines = ['## 四、逐场景', '']
    lines += _table(['场景', '组', '召回', '可追溯', '确定性', '反例', '四项门'], rows)
    lines += ['', '反例按类别（每次运行每问每类出现记一次）：', '']
    categories = sorted({category for item in summary['groups'].values() for scenario in item['scenarios'].values()
                         for category in scenario['counterexamples']})
    rows = [[names['counterexamples'][category]] + [str(sum(scenario['counterexamples'].get(category, 0)
                                                            for scenario in item['scenarios'].values()))
                                                    for item in summary['groups'].values()] for category in categories]
    lines += _table(['类别'] + list(names['groups'].values()), rows) if rows else ['（没有有效运行，没有判过反例）']
    return lines + ['']


def _budget(summary: dict) -> list[str]:
    """预算余量与逐问裁剪：固定路径每问用了多少、裁掉了什么、六问覆盖、取到召回与缺的应引项；RAG 每问装了多少。"""
    prepared, names = summary['prepared'], summary['names']
    lines = ['## 五、预算余量与逐问裁剪', '',
             f"固定路径（取上下文）用服务端的默认预算，这次返回的是 {_budgets(prepared)} 字符。超预算时按规格先裁跨链"
             '关系、多取的一跳与上层的块，从主干最远层起裁，也就是先裁 Company 与 Strategy；而五个场景 Why 的应引项都追到'
             '这两层。真实战略材料换上之后，预算一紧，最先坏的是固定路径组的 Why 召回。RAG 每问的装入上限等于固定路径这一问'
             '交给模型的字符数（写进提示词的取上下文返回，比 Markdown 多一层 JSON 外壳；同成本对照）。下表是这次播种每问的'
             '实测：', '']
    rows = []
    for key, item in prepared['questions'].items():
        fixed, rag = item['contexts']['fixed'], item['contexts']['rag']
        unanswered = [names['questions'][name] for name, answered in fixed['coverage'].items() if not answered]
        rows.append([f"{names['scenarios'][item['scenario']]}·{names['questions'][item['question']]}",
                     f"{fixed['used_chars']:,} / {fixed['max_chars']:,}", f"{fixed['max_chars'] - fixed['used_chars']:,}",
                     '、'.join(f"{entry['label']}（{REASONS.get(entry['reason'], entry['reason'])}）"
                              for entry in fixed['trimmed']) or '无',
                     '、'.join(unanswered) or '都答得了',
                     _ratio(fixed['recall'][0] / fixed['recall'][1], fixed['recall']), f"{fixed['chars']:,}",
                     f"{rag['chars']:,}（{rag['kept']} 块）", _ratio(rag['recall'][0] / rag['recall'][1], rag['recall'])])
    lines += _table(['问', '固定路径 Markdown / 预算', '余量', '被裁的项', '包里答不了的问', '固定路径取到召回',
                     '固定路径交给模型（RAG 上限）', 'RAG 装入', 'RAG 取到召回'], rows)
    top = []
    for key, item in prepared['questions'].items():
        if item['question'] != 'why':
            continue
        upper = [entry['label'] for entry in item['expected'] if entry['object_type'] in {'Company', 'Strategy'}]
        top.append([names['scenarios'][item['scenario']], str(len(item['expected'])), str(len(upper)), '、'.join(upper)])
    lines += ['', 'Why 的应引项里落在 Company 与 Strategy 的（预算不够时最先被裁）：', '']
    lines += _table(['场景', 'Why 应引项', '其中在 Company、Strategy', '是哪些'], top)
    missing = [(key, group, entry) for key, item in prepared['questions'].items()
               for group, context in item['contexts'].items() for entry in item['expected'] if entry['ref'] in context['missing']]
    lines += ['', '各组没取到的应引项：', '']
    if missing:
        lines += [f"- {names['groups'][group]}·{names['scenarios'][prepared['questions'][key]['scenario']]}·"
                  f"{names['questions'][prepared['questions'][key]['question']]}：{entry['label']}（`{entry['ref']}`）"
                  for key, group, entry in missing]
    else:
        lines.append('- 无：三组每问都取到了全部应引项。')
    return lines + ['']


def _evidence(item: dict, scenarios: dict[str, str]) -> str:
    evidence = item['evidence']
    if item['id'] == 'activity_to_component':
        parts = []
        if 'b' in evidence:
            parts.append(f"B 的结论（#66 口径）：{ACTIVITY[evidence['b']['activity']]}；更严口径："
                         f"{ACTIVITY[evidence['b']['strict']['activity']]}"
                         + ('（冒烟脚本的观测，不是实验结论）' if evidence['b']['smoke'] else ''))
        if 'e' in evidence:
            parts.append(f"task_only 场景参照组四项门：{'全过' if evidence['e']['verdict']['passed'] else '有门没过'}")
        return '；'.join(parts) or '—'
    if item['id'] == 'component_ref_instability':
        return (f"钉着的组件引用 {evidence['component_refs']} 条，其中跨修订 {evidence['cross_revision']} 条，"
                f"悬空 {len(evidence['dangling'])} 条")
    if item['id'] == 'why_coverage_low':
        rows = [f"{scenarios[scenario]}（取到召回 {_ratio(row['retrieval_recall'])}，"
                f"回答覆盖 {_ratio(row['answer_coverage'])}）" for scenario, row in evidence['scenarios'].items()]
        reading = {'retrieval': '有场景取到召回就不够，问题在取法或主干关系',
                   'answering': 'Why 链都取到了，差在回答没引全，问题在怎么让模型答全'}.get(evidence.get('reading'))
        return ('偏低的场景：' + ('、'.join(scenarios[name] for name in evidence['low']) or '无') + '；' + '，'.join(rows)
                + (f'。读法：{reading}' if reading else ''))
    return f"提出之后的问题事件 {evidence['issue_events']} 条，脱离主体快照 {len(evidence['detached'])} 条"


def _triggers(summary: dict) -> list[str]:
    lines = ['## 六、触发调整对象结构的四个条件', '',
             '每个条件是一个机械检查（`experiments/world_v02/triggers.py`），输出触发、未触发或无数据。阈值：'
             + '，'.join(f'`{key}` = {value}' for key, value in summary['thresholds'].items()) + '。', '']
    for number, item in enumerate(summary['triggers'], 1):
        lines += [f"### {number}. {item['condition']} → {item['action']}：{STATUS[item['status']]}", '',
                  '- 数据源：' + '；'.join(item['sources']) + '。',
                  f"- 依据：{_evidence(item, summary['names']['scenarios'])}。"]
        lines += [f'- 缺：{text}。' for text in item['missing']]
        lines.append('')
    return lines


def _b(summary: dict) -> list[str]:
    """对照实验 B：#66 的五项观测与两种口径的结论。"""
    lines = ['## 七、对照实验 B', '']
    b = summary['b']
    if b is None:
        return lines + [B_MISSING, '']
    if b['smoke']:
        lines += [f"> 这是 #66 预置冒烟脚本（`{b['script']['id']}`）的观测，内容是合成的，只证明代码与口径跑得通，"
                  '不是实验结论；结论看试用期间转写的真实记录。', '']
    counts = {line: '、'.join(f"{EXPRESSIONS.get(kind, '被拒')} {count}" for kind, count in b['expressions'][line].items())
              for line in ('task_only', 'task_activity')}
    lines += [f"数据：#66 的观测结论（`{b['format']}`，执行脚本 `{b['script']['id']}`，sha256 "
              f"`{b['script']['sha256'][:12]}…`）。两条线逐步的表达结果：Task-only 线{counts['task_only']}；"
              f"Task+Activity 线{counts['task_activity']}。", '']
    rows = [[item['name'], item['question'], '是' if item['occurred'] else '否', EXPRESSIONS[item['task_only']],
             ' / '.join(str(item['task_only_counts'][kind]) for kind in ('native', 'coarse', 'inexpressible')),
             '、'.join(f'`{step}`' for step in item['steps']) or '—'] for item in b['observations'].values()]
    lines += _table(['观测', '问题', '独立发生', 'Task-only 线的表达', '原生 / 粗粒度 / 表达不了', '依据的步骤'], rows)

    def verdict(conclusion):
        names = '、'.join(b['observations'][name]['name'] for name in conclusion['because'])
        return ACTIVITY[conclusion['activity']] + (f'（{names}）' if names else '')

    lines += ['', f"**按 #66 的口径（粗粒度算能表达）**：{verdict(b['conclusion'])}。规则：{b['conclusion']['rule']}", '',
              f"**按更严的口径（粗粒度也算表达不了，由 coarse 清单算出）**：{verdict(b['strict'])}。规则：{b['strict']['rule']}",
              '', f"Agent 写入需要以谁为主体：{b['agent_subject'] or '没有 Agent 的写入'}。", '',
              '粗粒度清单（两种口径只差在这里）：', '']
    coarse = [f"- {item['name']}：" + '、'.join(f"`{step['step']}`（{WRITINGS.get(step['writing'], step['writing'])}）"
                                               for step in item['coarse'])
              for item in b['observations'].values() if item['coarse']]
    return lines + (coarse or ['- 无']) + ['']


def _runs(summary: dict) -> list[str]:
    names, groups = summary['names']['groups'], summary['groups']
    ran = [name for name, item in groups.items() if item['runs']]
    lines = ['## 八、已执行、跳过与未验证', '', '**已执行**', '',
             f"- 准备：三组每问的上下文与引用核对（{'全部通过' if summary['prepared']['verified'] else '有不通过的'}）。"]
    lines += [f"- {names[name]}：" + '，'.join(f'{status} {count}' for status, count in groups[name]['runs'].items())
              for name in ran]
    lines += ['', '**跳过**', '']
    lines += [f'- {names[name]}：没有运行。' for name in groups if name not in ran] or ['- 无。']
    lines += ['', '**未验证**', '',
              '- 对照实验 B 与试用期的数据：见第七节。']
    return lines + ['']


def render(summary: dict) -> str:
    lines = ['# tkos.world/0.2 四种取法对照（含 RAG）实验报告', '',
             '> 由 `experiments/world_v02/report.py` 从 summary.json 生成，不要手改。', '']
    if summary.get('rehearsal'):
        lines += [REHEARSAL, '']
    lines += ['票 #68（规格 #46 第十节，用户故事 81、82）。同一批问题——实验 E 五个场景的六问——全量塞入、固定路径、'
              f"模型遍历与 RAG 四组都作答，同一模型、同一推理档、同一回答形状，每问 {summary['attempts']} 次；"
              '召回、可追溯、确定性与反例作门，成本按交给模型的字符数只报告。', '']
    lines += _setup(summary) + _conclusion(summary) + _basis(summary) + _scenarios(summary) + _budget(summary)
    lines += _triggers(summary) + _b(summary) + _runs(summary)
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=REPORT)
    args = parser.parse_args()
    args.output.write_text(render(json.loads(args.summary.read_text())))


if __name__ == '__main__':
    main()
