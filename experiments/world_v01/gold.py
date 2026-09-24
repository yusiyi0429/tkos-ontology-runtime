"""标准答案的 DRI 批准与中文审阅稿。

批准记下批准人（标准答案里写明的角色名，不写真实姓名）、时间，以及播种文件与标准答案（批准段除外）合在一起的
内容哈希；两份文件任何改动都让批准失效。实验跑器（票 #29）只能经 load_approved 取用，未批准或已失效就拒绝。
批准只由 E&O DRI 本人运行 approve 命令。

    python -m experiments.world_v01.gold status
    python -m experiments.world_v01.gold approve --by "E&O DRI"
    python -m experiments.world_v01.gold render   # 重写 docs/world-v01-eo-september-review.md
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
REVIEW = ROOT / 'docs/world-v01-eo-september-review.md'


class NotApproved(Exception):
    """标准答案没有经 DRI 批准，或批准后播种文件、标准答案改过。"""


def _read(path: Path) -> tuple[dict, dict]:
    answers = json.loads(path.read_text())
    return answers, json.loads((path.parent / answers['seed']).read_text())


def content_sha256(answers: dict, seed: dict) -> str:
    body = {'gold': {key: value for key, value in answers.items() if key != 'approval'}, 'seed': seed}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load_approved(path: Path = GOLD, manifest: dict | None = None) -> dict:
    """取用经批准的标准答案与它对应的播种文件；未批准或内容已变就抛 NotApproved。给了播种清单时，还要核对那次
    播种用的正是批准的内容。"""
    answers, seed = _read(path)
    approval = answers.get('approval') or {}
    if approval.get('approved_by') != answers['approver']:
        raise NotApproved('the gold answers have not been approved by the DRI')
    if approval.get('content_sha256') != content_sha256(answers, seed):
        raise NotApproved('the seed or the gold answers changed after the DRI approved them')
    if manifest is not None and manifest.get('content_sha256') != approval['content_sha256']:
        raise NotApproved('this seeded world was seeded from content other than what the DRI approved')
    spec.validate(seed)
    spec.validate_gold(answers, seed)
    return {'gold': answers, 'seed': seed}


def approve(path: Path, by: str) -> dict:
    """由 DRI 签：先校验两份文件自洽，再写批准段。"""
    answers, seed = _read(path)
    if by.strip() != answers['approver']:
        raise ValueError(f"the gold answers are approved by the role {answers['approver']!r}")
    spec.validate(seed)
    spec.validate_gold(answers, seed)
    answers['approval'] = {'approved_by': by.strip(), 'approved_at': datetime.now(timezone.utc).isoformat(),
                           'content_sha256': content_sha256(answers, seed)}
    path.write_text(json.dumps(answers, ensure_ascii=False, indent=2) + '\n')
    return answers['approval']


# ------------------------------------------------------------------ review document
def _registry() -> dict:
    registry = json.loads((ROOT / 'docs/contracts/world-registry-0.1.json').read_text())
    return {'objects': {item['type']: item for item in registry['objects']},
            'actions': {item['action']: item for item in registry['actions']}}


def _describe(ref: str, seed: dict, registry: dict) -> str:
    """把占位引用写成审阅者看得懂的一句：对象类型与标题、版本、块的中文名，或事件的时间与内容。"""
    steps = {step['key']: step for step in seed['steps'] if step.get('key')}
    kind, key = next(spec.placeholders(ref))
    step = steps[key]
    if kind == 'event':
        if step['do'] == 'record':
            return f"事件 {step['params']['occurred_at']}「{step['params']['content']['text'][:40]}」（`{ref}`）"
        return f"{_step_title(step, seed, registry)}（`{ref}`）"
    _, version, block = spec.OBJECT.fullmatch(ref).groups()
    described = registry['objects'][spec.object_type(step)]
    text = f"{described['display_name']}「{step['payload']['title']}」" + (f'第 {version} 版' if version else '（最新版）')
    if block:
        names = {item['id']: item['display_name'] for item in described['blocks']}
        text += f'的「{names.get(block, block)}」块'
    return text + f'（`{ref}`）'


def _step_title(step: dict, seed: dict, registry: dict) -> str:
    who = seed['identities'][step['by']]['display_name']
    if step['do'] == 'create':
        return f"{who} 建 {registry['objects'][step['type']]['display_name']}「{step['payload']['title']}」"
    if step['do'] == 'refresh':
        return f"{who} 写状态快照「{step['payload']['title']}」，时点 {step['payload']['as_of']}"
    if step['do'] == 'record':
        return f"{who} 记外部事件（{step['params']['category']}），发生于 {step['params']['occurred_at']}"
    if step['do'] == 'assign':
        return f"{who} 把 `{step['target']}` 指派给 {seed['identities'][step['to']]['display_name']}"
    if step['do'] == 'gate':
        params = ', '.join(f'{k}={v}' for k, v in step['params'].items() if k in {'phase', 'outcome'})
        return f"{who} {registry['actions'][step['action']]['display_name']} `{step['target']}`" + (f'（{params}）' if params else '')
    if step['do'] == 'relate':
        return f"{who} 给 `{step['target']}` 建关系 {step['field']}"
    return f"{who} 修订 `{step['target']}`"


def _blocks(values: dict, registry_blocks: list[dict]) -> list[str]:
    names = {item['id']: item['display_name'] for item in registry_blocks}
    lines = []
    for block, value in values.items():
        if value is None:
            lines.append(f'  - {names.get(block, block)}：（清空）')
            continue
        extra = [f'引用 {", ".join(f"`{ref}`" for ref in value.get("refs", []))}'] if value.get('refs') else []
        extra += [f'链接 {", ".join(value.get("artifacts", []))}'] if value.get('artifacts') else []
        lines.append(f"  - {names.get(block, block)}：{value.get('text', '')}" + (f"（{'；'.join(extra)}）" if extra else ''))
    return lines


def render(path: Path = GOLD) -> str:
    answers, seed = _read(path)
    registry = _registry()
    decoys: dict[str, list[str]] = {}
    for item in answers['counterexamples']:
        for ref in item['decoys']:
            decoys.setdefault(next(spec.placeholders(ref))[1], []).append(spec.COUNTEREXAMPLES[item['category']])
    approval = answers.get('approval') or {}
    current = content_sha256(answers, seed)
    if approval.get('approved_by') and approval.get('content_sha256') == current:
        status = f"已由 {approval['approved_by']} 于 {approval['approved_at']} 批准"
    elif approval.get('approved_by'):
        status = '批准已失效（批准后播种文件或标准答案改过），需要重新批准'
    else:
        status = '未批准：实验跑器会拒绝使用'
    lines = [
        f"# {seed['title']}：播种与标准答案审阅稿", '',
        '> 由 `experiments/world_v01/seed.json` 与 `gold.json` 生成（`python -m experiments.world_v01.gold render`），不要手改。',
        f"> 业务真实性请你审；标准答案经 {answers['approver']} 运行 `python -m experiments.world_v01.gold approve --by \"{answers['approver']}\"` 批准后，",
        '> 实验（票 #29）才能使用。', '',
        f'- 批准状态：{status}',
        f'- 当前内容哈希：`{current}`', '',
        '## 一、身份', '', '| 键 | 显示名 | 类型 | 所在 scope | 角色（域） |', '|---|---|---|---|---|',
    ]
    for key, identity in seed['identities'].items():
        roles = '；'.join(f"{', '.join(names)}（{seed['scopes'][identity['scope']]['domains'][domain]}）"
                          for domain, names in identity['roles'].items())
        lines.append(f"| `{key}` | {identity['display_name']} | {identity['type']} | {identity['scope']} | {roles} |")
    lines += ['', '## 二、播种步骤', '',
              '按顺序经 HTTP 的 prepare 与 commit 写入。建对象、修订、指派与门记播种时刻；外部事件与状态快照按下面写的真实时间。', '']
    for number, step in enumerate(seed['steps'], 1):
        head = f"{number}. {_step_title(step, seed, registry)}" + (f"（键 `{step['key']}`）" if step.get('key') else '')
        if step.get('key') in decoys:
            head += f"　**诱饵：{'、'.join(decoys[step['key']])}**"
        lines.append(head)
        payload = step.get('payload') or {}
        if step['do'] in spec.MAKES_OBJECT:
            object_type = spec.object_type(step)
        elif step['do'] == 'revise':
            object_type = spec.object_type(next(s for s in seed['steps'] if s.get('key') == step['target']))
        else:
            object_type = None
        if payload.get('blocks') and object_type:
            lines += _blocks(payload['blocks'], registry['objects'][object_type]['blocks'])
        fields = {k: v for k, v in payload.items() if k not in {'title', 'blocks'}}
        if step['do'] == 'revise' and 'title' in payload:
            fields['title'] = payload['title']
        if fields:
            lines.append('  - 字段：' + '，'.join(f'{k} = `{v}`' for k, v in fields.items()))
        if step['do'] == 'record':
            content = step['params']['content']
            lines.append(f"  - 主体：{', '.join(f'`{ref}`' for ref in step['params']['subject_refs'])}")
            lines += _blocks({'content': content}, [{'id': 'content', 'display_name': '内容'}])
        if step['do'] == 'relate':
            lines.append(f"  - 指向：{', '.join(f'`{ref}`' for ref in step['refs'])}")
        if step.get('note'):
            lines.append(f"  - 说明：{step['note']}")
    lines += ['', f"## 三、六问标准答案（从 `{answers['start']}` 出发）", '']
    for question in answers['questions']:
        lines += [f"### {question['id']}：{question['question']}", '', question['answer'], '', '应引用：']
        lines += [f'- {_describe(ref, seed, registry)}' for ref in question['expected']]
        lines.append('')
    lines += ['## 四、八类反例（出现即扣分）', '']
    for item in answers['counterexamples']:
        lines += [f"### {spec.COUNTEREXAMPLES[item['category']]}", '', item['rule'], '', '诱饵：']
        lines += [f'- {_describe(ref, seed, registry)}' for ref in item['decoys']]
        lines.append('')
    if answers.get('to_confirm'):
        lines += ['## 五、请你确认', '']
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
