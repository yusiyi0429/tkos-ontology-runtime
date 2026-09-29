"""实验 E（票 #67）标准答案的 DRI 批准、取用与中文审阅稿。

批准记下批准人（标准答案里写明的角色名「E&O DRI」，不写真实姓名）、时间，以及场景文件与标准答案（批准段除外）合在
一起的内容哈希；两份文件任何改动都让批准失效。实验跑器（票 #68）只能经 load_approved 取用，未批准或已失效就拒绝。
批准只由 E&O DRI 本人运行 approve 命令。

    python -m experiments.world_v02.gold status
    python -m experiments.world_v02.gold approve --by "E&O DRI"
    python -m experiments.world_v02.gold render   # 重写 docs/world-v02-scenarios-review.md
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from . import spec

FOLDER = Path(__file__).resolve().parent
ROOT = FOLDER.parents[1]
GOLD = FOLDER / 'gold.json'
REVIEW = ROOT / 'docs/world-v02-scenarios-review.md'


class NotApproved(Exception):
    """标准答案没有经 E&O DRI 批准，或批准后场景文件、标准答案改过。"""


def _read(path: Path) -> tuple[dict, dict]:
    answers = json.loads(path.read_text())
    return answers, json.loads((path.parent / answers['scenarios']).read_text())


def content_sha256(answers: dict, scenarios: dict) -> str:
    body = {'gold': {key: value for key, value in answers.items() if key != 'approval'}, 'scenarios': scenarios}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load_approved(path: Path = GOLD, manifest: dict | None = None) -> dict:
    """取用经批准的标准答案与它对应的场景文件；未批准或内容已变就抛 NotApproved。给了某次播种的 manifest 时，
    还要核对那次播种用的正是批准的内容。"""
    answers, scenarios = _read(path)
    approval = answers.get('approval') or {}
    if approval.get('approved_by') != answers['approver']:
        raise NotApproved('the gold answers have not been approved by the E&O DRI')
    if approval.get('content_sha256') != content_sha256(answers, scenarios):
        raise NotApproved('the scenarios or the gold answers changed after the E&O DRI approved them')
    if manifest is not None and manifest.get('content_sha256') != approval['content_sha256']:
        raise NotApproved('this world was seeded from content other than what the E&O DRI approved')
    spec.validate(scenarios)
    spec.validate_gold(answers, scenarios)
    return {'gold': answers, 'scenarios': scenarios}


def load_for_rehearsal(path: Path = GOLD, manifest: dict | None = None) -> dict:
    """彩排用（票 #74）：不要求 E&O DRI 批准，结果不作数；但给了播种的 manifest 时，那次播种用的必须正是当前的场景与
    标准答案。"""
    answers, scenarios = _read(path)
    if manifest is not None and manifest.get('content_sha256') != content_sha256(answers, scenarios):
        raise NotApproved('this world was seeded from content other than the current scenarios and gold answers')
    spec.validate(scenarios)
    spec.validate_gold(answers, scenarios)
    return {'gold': answers, 'scenarios': scenarios}


def approve(path: Path, by: str) -> dict:
    """由 E&O DRI 签：先校验两份文件自洽，再写批准段。"""
    answers, scenarios = _read(path)
    if by.strip() != answers['approver']:
        raise ValueError(f"the gold answers are approved by the role {answers['approver']!r}")
    spec.validate(scenarios)
    spec.validate_gold(answers, scenarios)
    answers['approval'] = {'approved_by': by.strip(), 'approved_at': datetime.now(timezone.utc).isoformat(),
                           'content_sha256': content_sha256(answers, scenarios)}
    path.write_text(json.dumps(answers, ensure_ascii=False, indent=2) + '\n')
    return answers['approval']


# ------------------------------------------------------------------ review document
_TYPES = {item['type']: item for item in spec.REGISTRY['objects']}
_ACTIONS = {item['action']: item['display_name'] for item in spec.REGISTRY['actions']}


class _Describer:
    """把占位引用与步骤写成审阅者看得懂的一句。版本写播种结束时的版本号。"""

    def __init__(self, scenarios: dict):
        self.scenarios = scenarios
        self.world = spec.World(scenarios)
        self.versions = self.world.final_versions()
        self.steps = {step['key']: step for _, step in spec.steps(scenarios) if step.get('key')}
        self.pending = {item['slot'] for item in scenarios['pending_material']}
        self.names = {key: item['display_name'] for key, item in scenarios['identities'].items()}

    def type_name(self, key: str) -> str:
        return _TYPES[self.world.objects[key]['type']]['display_name']

    def block_name(self, key: str, block: str) -> str:
        item = self.world.objects[key]
        blocks = (spec.PAYLOAD_SPECS[item['payload_type']]['blocks'] if item['type'] == 'StateSnapshot'
                  else _TYPES[item['type']]['blocks'])
        return next(entry['display_name'] for entry in blocks if entry['id'] == block)

    def ref(self, ref: str) -> str:
        kind, key = next(spec.placeholders(ref))
        if kind == 'event':
            return f"{self.step_title(self.steps[key])}（`{ref}`）"
        _, version, block, component = spec.parts(ref)
        text = f"{self.type_name(key)}「{self.world.objects[key]['title']}」"
        text += f'第 {version} 版' if version else f'（最新版，第 {self.versions[key]} 版）'
        if block:
            text += f'的「{self.block_name(key, block)}」块'
        if component:
            text += f'里的组件 `{component}`'
        slot = f'@{key}' + (f'#{block}' if block else '') + (f'/{component}' if component else '')
        pending = '　**待真实战略材料**' if slot in self.pending else ''
        return text + f'（`{ref}`）' + pending

    def step_title(self, step: dict) -> str:
        who = self.names[step['by']]
        do = step['do']
        if do == 'create':
            name = _TYPES[step['type']]['display_name']
            return f"{who} 建{' ' if name[:1].isascii() else ''}{name}「{step['payload']['title']}」"
        if do == 'refresh':
            return f"{who} 写状态快照「{step['payload']['title']}」（主体 `{step['payload']['subject_ref']}`）"
        if do == 'record':
            return f"{who} 记外部事件（{step['params']['category']}）"
        if do == 'assign':
            return f"{who} 把 `{step['target']}` 指派给 {self.names[step['to']]}"
        if do in {'gate', 'lifecycle'}:
            outcome = step['params'].get('outcome')
            candidate = '，带候选' if 'payload' in step['params'] else ''
            return (f"{who} {_ACTIONS[step['action']]} `{step['target']}`"
                    + (f'（{outcome}{candidate}）' if outcome else f'（{candidate[1:]}）' if candidate else ''))
        if do == 'relate':
            return f"{who} 给 `{step['target']}` 建关系 {step['field']}，指向 {', '.join(f'`{ref}`' for ref in step['refs'])}"
        return f"{who} 修订 `{step['target']}`"


def _block_lines(values: dict, names: dict[str, str]) -> list[str]:
    lines = []
    for block, value in values.items():
        if value is None:
            lines.append(f'  - {names.get(block, block)}：（清空）')
            continue
        extra = [f"引用 {', '.join(f'`{ref}`' for ref in value['refs'])}"] if value.get('refs') else []
        lines.append(f"  - {names.get(block, block)}：{value.get('text', '')}" + (f"（{'；'.join(extra)}）" if extra else ''))
        for item in value.get('components', []):
            attributes = item.get('attributes') or {}
            who = f"（责任人 `{attributes['responsible']}`）" if attributes.get('responsible') else ''
            refs = f"（引用 {', '.join(f'`{ref}`' for ref in item['refs'])}）" if item.get('refs') else ''
            lines.append(f"    - 组件 `{item['id']}`：{item.get('text', '')}{who}{refs}")
    return lines


def _step_lines(number: int, step: dict, describer: _Describer, decoys: dict[str, list[str]]) -> list[str]:
    head = f"{number}. {describer.step_title(step)}" + (f"（键 `{step['key']}`）" if step.get('key') else '')
    if step.get('key') in decoys:
        head += f"　**诱饵：{'、'.join(decoys[step['key']])}**"
    lines = [head]
    payload = step.get('payload') or {}
    if step['do'] in spec.MAKES_OBJECT:
        kind = spec.object_type(step)
    elif step['do'] == 'revise':
        kind = describer.world.objects[step['target']]['type']
    else:
        kind = None
    if kind == 'StateSnapshot':
        names = {block['id']: block['display_name'] for block in spec.PAYLOAD_SPECS[payload['payload_type']]['blocks']}
        lines.append(f"  - 时点 `{payload['as_of']}`，来源事件 {', '.join(f'`{ref}`' for ref in payload['source_event_refs'])}")
    elif kind:
        names = {block['id']: block['display_name'] for block in _TYPES[kind]['blocks']}
    if kind and payload.get('blocks'):
        lines += _block_lines(payload['blocks'], names)
    fields = {k: v for k, v in payload.items()
              if k not in {'title', 'blocks', 'subject_ref', 'as_of', 'payload_type', 'source_event_refs'}}
    if fields:
        lines.append('  - 字段：' + '，'.join(f'{k} = `{v}`' for k, v in fields.items()))
    params = step.get('params') or {}
    if step['do'] == 'record':
        lines.append(f"  - 主体 {', '.join(f'`{ref}`' for ref in params['subject_refs'])}，发生于 `{params['occurred_at']}`")
        lines += _block_lines({'content': params['content']}, {'content': '内容'})
    elif params.get('payload'):
        names = {block['id']: block['display_name'] for block in _TYPES[describer.world.objects[step['target']]['type']]['blocks']}
        lines.append('  - 候选：')
        lines += ['  ' + line for line in _block_lines(params['payload'].get('blocks', {}), names)]
    if params.get('content') and step['do'] != 'record':
        lines.append(f"  - 事件内容：{params['content']['text']}")
    if step.get('note'):
        lines.append(f"  - 说明：{step['note']}")
    return lines


def render(path: Path = GOLD) -> str:
    answers, scenarios = _read(path)
    describer = _Describer(scenarios)
    approval = answers.get('approval') or {}
    current = content_sha256(answers, scenarios)
    if approval.get('approved_by') and approval.get('content_sha256') == current:
        status = f"已由 {approval['approved_by']} 于 {approval['approved_at']} 批准"
    elif approval.get('approved_by'):
        status = '批准已失效（批准后场景文件或标准答案改过），需要重新批准'
    else:
        status = '未批准：实验跑器会拒绝使用'
    decoys: dict[str, list[str]] = {}
    for item in answers['scenario_answers']:
        for counter in item['counterexamples']:
            for ref in counter['decoys']:
                decoys.setdefault(next(spec.placeholders(ref))[1], []).append(
                    f"{spec.SCENARIOS[item['id']]}·{spec.COUNTEREXAMPLES[counter['category']]['name']}")
    lines = [
        f"# {scenarios['title']}：播种与标准答案审阅稿", '',
        '> 由 `experiments/world_v02/scenarios.json` 与 `gold.json` 生成（`python -m experiments.world_v02.gold render`），不要手改。',
        f"> 业务真实性请你审；标准答案经 {answers['approver']} 运行 `python -m experiments.world_v02.gold approve --by \"{answers['approver']}\"` 批准后，",
        '> 实验跑器（票 #68）才能使用。', '',
        f'- 批准状态：{status}',
        f'- 当前内容哈希：`{current}`',
        '- 占位：`@键` 是该对象播种结束时的最新版本，`@键@N` 是第 N 版，`#块`、`#块/组件` 指到块与组件；`$身份键` 是身份；'
        '`event:键` 是那一步记下的事件；时刻写 `now` 的取播种那一步的数据库时刻。', '',
        '## 一、身份', '', '| 键 | 显示名（角色名） | 类型 | 角色（域） |', '|---|---|---|---|',
    ]
    for key, identity in scenarios['identities'].items():
        roles = '；'.join(f"{', '.join(names)}（{scenarios['domains'][domain]}）" for domain, names in identity['roles'].items())
        lines.append(f"| `{key}` | {identity['display_name']} | {identity['type']} | {roles} |")
    lines += ['', '## 二、待真实战略材料', '',
              'Company 与 Strategy 的真实材料还没到，下面这些块与组件先用现有存根；材料到了按位置替换正文（组件 id 不变），'
              '重新播种，再请 E&O DRI 批准。各场景 Why 的标准答案引到其中几处。', '',
              '| 位置 | 现在的存根 |', '|---|---|']
    lines += [f"| {describer.ref(item['slot'])} | {item['stub']} |" for item in scenarios['pending_material']]
    lines += ['', '## 三、共用主干的播种', '', scenarios['notes'], '']
    for number, step in enumerate(scenarios['base']['steps'], 1):
        lines += _step_lines(number, step, describer, decoys)
    answered = {item['id']: item for item in answers['scenario_answers']}
    for index, item in enumerate(scenarios['scenarios'], 1):
        gold = answered[item['id']]
        lines += ['', f"## 四之{index}、{item['title']}（`{item['id']}`）", '', item['summary'], '',
                  f"- 起点：{describer.ref('@' + item['start'])}",
                  f"- 认领的主干对象：{'、'.join(f'`{key}`' for key in item['owns'])}", '', '### 播种步骤', '']
        for number, step in enumerate(item['steps'], 1):
            lines += _step_lines(number, step, describer, decoys)
        lines += ['', '### 六问标准答案', '']
        for question in gold['questions']:
            lines += [f"#### {question['id']}：{question['question']}", '', question['answer'], '', '应引用：']
            lines += [f'- {describer.ref(ref)}' for ref in question['expected']]
            lines.append('')
        lines += ['### 反例', '']
        for counter in gold['counterexamples']:
            lines += [f"#### {spec.COUNTEREXAMPLES[counter['category']]['name']}（`{counter['category']}`）", '',
                      counter['rule'], '', '诱饵：']
            lines += [f'- {describer.ref(ref)}' for ref in counter['decoys']]
            if counter.get('conflict'):
                conflict = counter['conflict']
                lines += ['', '冲突的两处（conflict 断言要同时引到）：']
                lines += [f'- 上层：{describer.ref(ref)}' for ref in conflict['upper']]
                lines += [f'- 本层：{describer.ref(ref)}' for ref in conflict['lower']]
                lines.append(f"- 要求指出冲突的问题：{'、'.join(conflict['questions'])}")
            lines.append('')
    lines += ['## 五、回答的形状与反例判定', '',
              '回答是断言的列表 `{claim, kind, refs}`：kind 为 fact（陈述内容）、gap（说明为空或取不到）或 conflict（指出冲突，'
              'refs 同时引冲突的两处）。判定是纯函数 `experiments.world_v02.counterexamples.judge`，口径见该模块与 README。', '']
    if answers.get('to_confirm'):
        lines += ['## 六、请你确认', '']
        lines += [f'{number}. {text}' for number, text in enumerate(answers['to_confirm'], 1)]
        lines.append('')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    signed = sub.add_parser('approve')
    signed.add_argument('--by', required=True)
    sub.add_parser('render')
    args = parser.parse_args()
    if args.command == 'approve':
        print(json.dumps(approve(GOLD, args.by), ensure_ascii=False))
        REVIEW.write_text(render(GOLD))
    elif args.command == 'render':
        REVIEW.write_text(render(GOLD))
    else:
        try:
            load_approved(GOLD)
            print('approved')
        except NotApproved as exc:
            raise SystemExit(f'not approved: {exc}')


if __name__ == '__main__':
    main()
