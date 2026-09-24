"""关键回答的人工核验材料（#32 D）：每问、每个作答组各取第 1 次尝试里有效的那次运行，引用一律还原成播种里的
对象名与块、事件的日期与内容，交 E&O DRI 对照标准答案判定内容对不对（机器指标只看引用）。

    python -m experiments.world_v01.review --seeded-output O2 --output O3 --material M.json

材料里只有标准答案、期望引用、各组回答的断言与引用，以及页面顶部用的指标；人名一律是角色名。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from . import gold, metrics

ROOT = Path(__file__).resolve().parents[2]
GROUPS = ('f', 'a', 'a0')  # 作答组；B 组不作答
TYPE_NAMES = {'Company': '公司', 'Strategy': '战略', 'Unit': '责任单元', 'ResponsibilityUnit': '责任单元',
              'LongTermGoal': '长期目标', 'PeriodGoal': '周期目标', 'Mission': 'Mission', 'Task': 'Task',
              'Activity': 'Activity', 'StateSnapshot': '状态快照'}
ROLES = {'ceo': 'CEO', 'eo_dri': 'E&O DRI', 'agents_dri': 'Agents DRI', 'design_ic': '方案 IC', 'eo_agent': 'E&O Agent'}
GATES = {'world_confirm_long_term_goal': '确认长期目标', 'world_commit_period_goal': '承诺周期目标',
         'world_confirm_period_goal': '确认周期目标', 'world_commit_mission': '承诺 Mission',
         'world_confirm_mission': '确认 Mission'}
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def first_valid_run(folder: Path, question: str) -> Path | None:
    """第 1 次尝试里有效的那次运行：`<问>-1`，或它的重试 `<问>-1-retryN` 里状态为 ok 的那个。"""
    for path in sorted(folder.glob(f'{question}-1*'), key=lambda item: (len(item.name), item.name)):
        if path.name != f'{question}-1' and not path.name.startswith(f'{question}-1-retry'):
            continue
        run = path / 'run.json'
        if run.exists() and json.loads(run.read_text())['status'] == 'ok':
            return path
    return None


class Labels:
    """把回答里的 UUID 引用与标准答案的占位还原成人读得懂的说明。"""

    def __init__(self, manifest: dict) -> None:
        seed = json.loads((ROOT / 'experiments/world_v01/seed.json').read_text())
        self.steps = {step['key']: step for step in seed['steps'] if 'key' in step}
        self.titles = {key: step['payload']['title'] for key, step in self.steps.items()
                       if step.get('do') == 'create' and 'payload' in step}
        self.types = {key: item['type'] for key, item in manifest['objects'].items()}
        self.by_object = {item['object_id']: key for key, item in manifest['objects'].items()}
        self.by_event = {(item['event_id'] if isinstance(item, dict) else item): key
                         for key, item in manifest['events'].items()}

    def object(self, key: str) -> str:
        kind = self.types.get(key, '')
        return f"{TYPE_NAMES.get(kind, kind)}「{self.titles.get(key, key)}」"

    def event(self, key: str) -> str:
        step = self.steps.get(key, {})
        who = ROLES.get(step.get('by'), step.get('by', ''))
        if step.get('do') == 'record':
            params = step['params']
            return f"事件 {params['occurred_at'][:10]}（{who} 记）：{params['content']['text'][:80]}…"
        if step.get('do') == 'assign':
            return f"指派：{who} 把 {self.object(step['target'])} 指派给 {ROLES.get(step['to'], step['to'])}"
        if step.get('do') == 'gate':
            return f"门：{who} {GATES.get(step['action'], step['action'])} {self.object(step['target'])}"
        return f'事件：{key}'

    def expected(self, ref: str) -> str:
        if ref.startswith('event:'):
            return self.event(ref.split(':', 1)[1])
        key, _, block = ref.lstrip('@').partition('#')
        return self.object(key) + (f' · {block}' if block else '')

    def cited(self, ref: str) -> dict:
        """回答里的一条引用：原文、对应的占位写法与说明。"""
        normal = metrics.normalize(ref)
        if normal.startswith('event:'):
            key = self.by_event.get(normal.split(':', 1)[1])
            return {'raw': ref, 'key': f'event:{key}' if key else None,
                    'label': self.event(key) if key else '（本 scope 以外的事件）'}
        match = _UUID.match(normal)
        key = self.by_object.get(match.group(0)) if match else None
        if key is None:
            return {'raw': ref, 'key': None, 'label': '（无法对应到播种对象）'}
        head, _, block = normal.partition('#')
        version = head.partition('@')[2]
        return {'raw': ref, 'key': f'@{key}' + (f'#{block}' if block else ''),
                'label': self.object(key) + (f' 第 {version} 版' if version else '') + (f' · {block}' if block else '')}


def material(seeded_output: Path, output: Path) -> dict:
    manifest = json.loads((seeded_output / 'manifest.json').read_text())
    approved = gold.load_approved(gold.GOLD, manifest)
    resolved = metrics.resolve(approved['gold'], manifest)
    labels = Labels(manifest)
    summary = json.loads((output / 'summary.json').read_text())
    questions = []
    for item in approved['gold']['questions']:
        expected = resolved['questions'][item['id']]['expected']
        answers = {}
        for group in GROUPS:
            run = first_valid_run(output / group, item['id'])
            if run is None:
                continue
            claims = []
            for claim in json.loads((run / 'answer.json').read_text())['claims']:
                refs = [labels.cited(ref) for ref in claim['refs']]
                cited = {metrics.normalize(ref) for ref in claim['refs']}
                for ref, raw in zip(refs, claim['refs']):
                    ref['expected'] = any(metrics.covers(want, {metrics.normalize(raw)}) for want in expected)
                claims.append({'text': claim['claim'], 'kind': claim['kind'], 'refs': refs,
                               'covers': sum(metrics.covers(want, cited) for want in expected)})
            answers[group] = {'run': run.name, 'claims': claims}
        questions.append({'id': item['id'], 'question': item['question'], 'gold_answer': item['answer'],
                          'expected': [{'ref': ref, 'label': labels.expected(ref)} for ref in item['expected']],
                          'answers': answers})
    shown = {group: {'recall': summary[group]['recall'], 'answer_coverage': summary[group]['answer_coverage'],
                     'chars': summary[group]['chars'], 'of_full': summary[group]['budget']['of_full']}
             for group in GROUPS if group in summary}
    shown['b'] = {'recall': summary['b']['recall'], 'markdown': summary['b']['used_chars'], 'mcp': summary['b']['chars']}
    return {'source': {'experiment': output.name, 'content_sha256': manifest['content_sha256'],
                       'selection': '每问、每个作答组取第 1 次尝试里有效的那次运行'},
            'metrics': shown, 'questions': questions}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--seeded-output', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--material', type=Path, required=True)
    args = parser.parse_args()
    data = material(args.seeded_output, args.output)
    args.material.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'questions': len(data['questions']),
                      'answers': sum(len(item['answers']) for item in data['questions'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
