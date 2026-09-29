"""四种取法对照的跑器（票 #68，规格 #46 第十节，用户故事 81、82）。结构沿用 0.1 的 experiments/world_v01/experiment.py
（隔离、污染判定与重试照搬，0.1 的模块一行不改）。同一批问题——实验 E（#67）五个场景的六问——四组都作答，同一模型、
同一推理档、同一回答 Schema，每问三次；各组只差模型拿到上下文的方式：

- full（全量塞入）：scope 内每个对象的最新版加每条事件，经读投影取、按分块写进提示词（retrieval.full）；
- fixed（固定路径）：跑器调一次取上下文（不给预算，用服务端默认值），把返回写进提示词，同 0.1 的 F 组；
- traverse（模型遍历）：模型经 tkos-world-mcp（契约 0.2）的读工具自己走，不给取上下文，同 0.1 的 A0 组；
- rag（RAG）：按问题检索分块，写进提示词（retrieval.retrieve）。同成本对照：每一问装入的字符上限等于固定路径组这一问
  实际交给模型的字符数，也就是写进提示词的那段取上下文返回（MCP 交给调用方的 JSON）的字符数；两组按同一口径（交给
  模型的字符数）量成本。取上下文的默认预算（#70 定为 100000）大到几乎装下整个 scope，RAG 按它装就退化成全量塞入；
  按固定路径实际交给模型的字符装，比的是同样的成本下谁取得准。

全量、固定路径、RAG 三组不给任何工具。三组的上下文在准备阶段每问生成一次，三次作答用同一份，所以这三组每次运行取到的
集合相同。代码里不写死预算：固定路径的预算读自取上下文的返回（budget.max_chars），RAG 的上限是固定路径那段文本的长度。

    python -m experiments.world_v02.experiment prepare --env-file P/env.json --seeded-private P2 --seeded-output O2 \\
        --private P3 --output O3                       # 不调模型：生成三组的上下文并核对引用，批准前也能跑
    python -m experiments.world_v02.experiment run --env-file P/env.json --seeded-private P2 --seeded-output O2 \\
        --private P3 --output O3 --model M --effort E [--groups full fixed traverse rag] [--attempts N] [--rehearsal]
    python -m experiments.world_v02.experiment summarize --seeded-output O2 --output O3 [--b-observations F]

P2、O2 是 experiments.world_v02.seed 的 --private 与 --output（那次播种的凭据与 manifest.json）。

准备（prepare）：经 HTTP 读投影取 scope 的全部内容，切分并建 RAG 索引；每问经 HTTP 调一次取上下文（默认预算与近期
窗口），记下包、六问覆盖、预算与检索计划里被裁的项；生成全量文本、固定路径的返回与 RAG 结果（上限取固定路径这一问
交给模型的字符数）。然后核对：每个分块恰好带一条自己的引用；每组取到的每一项都以业务形式出现在交给模型的文本里；
取到的每个对象版本、块、组件与事件都能经读投影读回。
都通过 verified 才为真。另做读投影扫描，给触发检查用（triggers.scan）。

固定路径组交给模型的文本与 MCP 交给调用方的完全相同：``tkos_world_mcp.server._shown`` 取包 id、Markdown、六问覆盖与
按原因、按类计的裁剪条数，按 MCP 的写法序列化；取到的集合用 ``tkos_world_mcp.server._content`` 判，与 MCP 运行日志
一致。这是对 MCP 两个私有函数的依赖。成本按这段文本计，比包里 Markdown 的 used_chars 大一层 JSON 外壳（六问覆盖与
预算摘要，0.1 报告已指出）；RAG 的上限也按这段文本的长度定，两组同一口径。

跑（run）先经 ``gold.load_approved`` 取标准答案并核对播种，未经 E&O DRI 批准或批准后内容改过就拒跑。彩排（--rehearsal，
票 #74）改用 ``gold.load_for_rehearsal``：不要求批准，只核对播种用的正是当前内容；setup.json 记 rehearsal，summarize
按它选同一种取法并在 summary 里带上，报告开头写明结果不作数。--attempts 改每问的作答次数（默认 ATTEMPTS）。再在同一个输出
目录里做一次准备，核对不通过就停；然后按组跑模型。每次作答都隔离：空目录、不读用户的 Codex 配置，关掉 shell、记忆、
联网搜索、插件、apps 与子代理（DISABLED 同 0.1）；模型遍历组只开 tkos-world-mcp 的四个读工具，其余三组不配 MCP
server。事件流里出现别的工具就记为污染；污染与失败的运行不计入指标，同一次重跑到有效为止（最多再试 RETRIES 次），
每一次尝试都留在输出目录里。各组都以 E&O Agent 的身份读，都只读。

输出目录：setup.json、prepared.json、contexts/（全量文本、每问的固定路径返回与 RAG 结果）、<组>/<场景>.<问>-<次>[-retryN]/
（run.json、answer.json、codex.jsonl、codex.stderr，全量、固定路径、RAG 另有 context.json，模型遍历有 mcp/ 运行日志）、
summary.json。都是本地产物，不入库。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import jsonschema

from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import public_json
from experiments.world_v01.experiment import ATTEMPTS, DISABLED, RETRIES, TIMEOUT, contamination
from tkos_world_mcp.server import _content, _shown
from tkos_world_mcp.tools_v02 import FACE

from . import counterexamples, gold, metrics, retrieval, spec, triggers

ROOT = Path(__file__).resolve().parents[2]
MCP = ROOT / '.venv/bin/tkos-world-mcp'
CONTRACT = 'tkos.world/0.2'
AGENT = 'eo_agent'
SERVER = 'tkos_world'
# 0.2 Agent 面的读工具里除取上下文以外的四个（同 0.1 的 A0：纯模型遍历）。
READS = ('world_get_object', 'world_get_events', 'world_get_state', 'world_list_objects')
GROUPS = {'full': (), 'fixed': (), 'traverse': READS, 'rag': ()}
TOOL_NAMES = {'world_get_object': '取对象', 'world_get_events': '取事件', 'world_get_state': '取状态',
              'world_list_objects': '列对象'}
ANSWER = {
    'type': 'object', 'additionalProperties': False, 'required': ['claims'],
    'properties': {'claims': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['claim', 'kind', 'refs'],
        'properties': {'claim': {'type': 'string'}, 'kind': {'type': 'string', 'enum': list(counterexamples.KINDS)},
                       'refs': {'type': 'array', 'items': {'type': 'string'}}}}}},
}
_OPENING = {
    'traverse': '只用 tkos_world 的读工具（{tools}）了解业务世界，回答下面这个关于一个业务对象的问题。',
    'full': '下面是这个业务世界（scope）里每个对象的最新版与每条事件，经读投影取出、按分块整理，每段开头带它的引用。'
            '只根据它回答下面这个关于一个业务对象的问题。',
    'fixed': '下面是经 tkos_world 取上下文（服务端按固定路径从起点向上取）得到的返回，原样附上。'
             '只根据它回答下面这个关于一个业务对象的问题。',
    'rag': '下面是按问题从这个业务世界（scope）里检索到的片段，按相关度排列，每段开头带它的引用。'
           '只根据它回答下面这个关于一个业务对象的问题。',
}
_LABELS = {'full': '业务世界的全部内容', 'fixed': '取上下文的返回', 'rag': '检索到的片段'}
PROMPT = """你是 E&O 责任单元的执行 Agent。{opening}

起点对象的 object_id：{start}
问题：{question}
{context}
回答要求：
- 按给定的 JSON 结构回答，claims 是若干条断言；每条写 claim（一句中文）、kind 与 refs。
- kind 为 fact 表示陈述{where}里的内容；为 gap 表示说明某处为空或没有记录；为 conflict 表示指出冲突，refs 同时引冲突的两处（上层的约束与本层的计划）。
- 每条断言都要有 refs，写支撑它的引用，照{where}里的原样：对象版本 `<object_id>@<版本>`，块 `<object_id>@<版本>#<块 id>`，组件 `<object_id>@<版本>#<块 id>/<组件 id>`，事件 `event:<event_id>`；gap 断言引出显示为空的那个块或对象。
- 只写{where}里有的内容，不要猜测。"""


def select(names) -> dict[str, tuple[str, ...]]:
    """要跑的组与各组给模型的工具，按 GROUPS 的顺序。"""
    return {name: tools for name, tools in GROUPS.items() if name in names}


def prompt(group: str, start: str, question: str, context: str | None = None) -> str:
    """四组的提示词只差开头一句与上下文；回答要求逐条相同，只把「工具返回」换成「上面的内容」。"""
    tools = GROUPS[group]
    if tools:
        return PROMPT.format(opening=_OPENING[group].format(tools='、'.join(TOOL_NAMES[tool] for tool in tools)),
                             start=start, question=question, context='', where='工具返回')
    return PROMPT.format(opening=_OPENING[group], start=start, question=question, where='上面的内容',
                         context=f'\n{_LABELS[group]}：\n{context}\n')


def codex_command(model: str, effort: str, workdir: Path, schema: Path, answer: Path, api_url: str, log_dir: Path,
                  text: str, tools: tuple[str, ...] = ()) -> list[str]:
    """一次隔离的 codex exec（同 0.1），只开本组的读工具，没有工具就不配 MCP server。MCP server 选契约 0.2
    （TKOS_WORLD_CONTRACT_VERSION），凭证只经环境变量 TKOS_WORLD_AGENT_TOKEN 透传，不上命令行。"""
    server = f'mcp_servers.{SERVER}'
    codex = str(Path(shutil.which('codex') or 'codex').resolve())  # 真实路径：代码模式的宿主程序在它旁边
    args = [codex, 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check', '--json',
            '-C', str(workdir), '-s', 'read-only', '-m', model, '-c', f'model_reasoning_effort={json.dumps(effort)}',
            '-c', 'web_search="disabled"', '--output-schema', str(schema), '-o', str(answer)]
    for feature in DISABLED:
        args += ['--disable', feature]
    if not tools:
        return args + [text]
    env = (f'{{TKOS_WORLD_API_URL = {json.dumps(api_url)}, TKOS_WORLD_CONTRACT_VERSION = {json.dumps(CONTRACT)}, '
           f'TKOS_WORLD_MCP_LOG_DIR = {json.dumps(str(log_dir))}}}')
    return args + ['-c', f'{server}.command={json.dumps(str(MCP))}', '-c', f'{server}.env={env}',
                   '-c', f'{server}.env_vars=["TKOS_WORLD_AGENT_TOKEN"]',
                   '-c', f'{server}.enabled_tools={json.dumps(list(tools))}',
                   '-c', f'{server}.default_tools_approval_mode="approve"',  # 只读工具免批准，exec 下无人可批
                   '-c', f'{server}.tool_timeout_sec=150', text]


# ------------------------------------------------------------------ prepare
def fixed_context(body: dict) -> dict:
    """取上下文的 HTTP 返回 -> 固定路径组交给模型的文本（与 MCP 交给调用方的相同）、取到的集合与检索计划的摘要。"""
    text = json.dumps(_shown('world_get_context', body), ensure_ascii=False, separators=(',', ':'))
    refs, events = _content(body['context_pack'], FACE)
    budget = body['budget']
    return {'text': text, 'chars': len(text), 'refs': sorted(refs), 'event_ids': sorted(events),
            'context_pack_id': body['context_pack_id'], 'used_chars': budget['used_chars'],
            'max_chars': budget['max_chars'], 'over_budget': budget['over_budget'],
            'coverage': {name: item['answered'] for name, item in body['coverage'].items()},
            'trimmed': body['plan']['trimmed']}


def _taken(context: dict) -> set[str]:
    return set(context['refs']) | {f'event:{event}' for event in context['event_ids']}


def _described(ref: str, labels: dict[str, str], types: dict[str, str]) -> dict:
    parsed = counterexamples.parse(ref) or {}
    return {'ref': ref, 'label': labels.get(ref, ref), 'object_type': types.get(parsed.get('object_id'))}


def _trimmed(entry: dict, labels: dict[str, str], types: dict[str, str]) -> dict:
    """检索计划里裁掉的一项，写上它是什么（对象类型、标题与块名）。"""
    kind, _, rest = entry['key'].partition(':')
    ref = entry['key'] if kind == 'event' else rest
    if kind == 'relations':
        return {**entry, 'label': f"上溯第 {entry['level']} 层的跨链关系", 'object_type': None}
    return {**entry, **{name: value for name, value in _described(ref, labels, types).items() if name != 'ref'}}


def verify(client, corpus: dict, contexts: dict[str, dict]) -> dict:
    """核对每组交给模型的文本：取到的每一项都以业务形式出现在文本里，并且能经读投影读回（对象、块与组件按版本取对象，
    事件在读投影取到的事件里）。contexts 是 名字 -> 上下文。"""
    events = {event['event_id'] for event in corpus['events']}
    views: dict[tuple[str, int], dict] = {}
    not_in_text, not_read_back = {}, set()
    for name, context in contexts.items():
        absent = sorted(ref for ref in _taken(context) if ref not in context['text'])
        if absent:
            not_in_text[name] = absent
        for ref in _taken(context):
            parsed = counterexamples.parse(ref)
            if parsed is None:
                not_read_back.add(ref)
            elif 'event_id' in parsed:
                if parsed['event_id'] not in events:
                    not_read_back.add(ref)
            else:
                where = (parsed['object_id'], parsed['version'])
                if where not in views:
                    views[where] = client.json('GET', f'/v1/world/objects/{where[0]}?version={where[1]}',
                                               expected={200, 404})
                view = views[where]
                body = view.get('business', view)
                block = next((item for item in body.get('blocks', []) if item['id'] == parsed['block']), None)
                if 'object_id' not in view or body['version'] != parsed['version'] \
                        or (parsed['block'] and block is None) \
                        or (parsed['component'] and not any(item['id'] == parsed['component']
                                                            for item in block['components'])):
                    not_read_back.add(ref)
    return {'refs_in_text': not not_in_text, 'not_in_text': not_in_text,
            'refs_read_back': not not_read_back, 'not_read_back': sorted(not_read_back),
            'object_versions_read': len(views)}


def prepare(client, manifest: dict, answers: dict, output: Path) -> dict:
    """生成全量、固定路径、RAG 三组每问的上下文（写进 output/contexts/），核对引用，做读投影扫描。RAG 每问的装入上限
    是固定路径这一问交给模型的字符数（同成本对照）。"""
    corpus = retrieval.read_corpus(client)
    found = retrieval.chunks(corpus)  # 每个分块恰好带一条自己的引用，不是就报错
    labels = {chunk.ref: chunk.label for chunk in found}
    types = {view['business']['object_id'] if 'business' in view else view['object_id']:
             view['business']['object_type'] if 'business' in view else 'StateSnapshot' for view in corpus['objects']}
    titles = {view['business']['object_id']: view['business']['title'] for view in corpus['objects'] if 'business' in view}
    index = retrieval.Index(found)
    full = retrieval.full(found)
    public_json(output / 'contexts/full.json', full)
    contexts, questions = {'full': full}, {}
    for scenario, gold_item in metrics.resolve(answers, manifest).items():
        start = counterexamples.parse(gold_item['start'])['object_id']
        asked = {item['id']: item['question'] for item in
                 next(item for item in answers['scenario_answers'] if item['id'] == scenario)['questions']}
        for name, expected in gold_item['questions'].items():
            key = metrics.key(scenario, name)
            fixed = fixed_context(client.json('POST', f'/v1/world/objects/{start}/context', {'question': asked[name]}))
            made = {'fixed': fixed, 'rag': retrieval.retrieve(index, titles[start], asked[name], fixed['chars'])}
            for group, context in made.items():
                public_json(output / f'contexts/{key}.{group}.json', context)
                contexts[f'{key}.{group}'] = context
            made['full'] = full
            summary = {}
            for group, context in made.items():
                got = _taken(context)
                summary[group] = {'chars': context['chars'], 'recall': [sum(ref in got for ref in expected), len(expected)],
                                  'missing': [ref for ref in expected if ref not in got]}
            fixed, rag = made['fixed'], made['rag']
            summary['fixed'].update(used_chars=fixed['used_chars'], max_chars=fixed['max_chars'],
                                    over_budget=fixed['over_budget'], coverage=fixed['coverage'],
                                    trimmed=[_trimmed(entry, labels, types) for entry in fixed['trimmed']])
            summary['rag'].update(max_chars=rag['max_chars'], kept=len(rag['kept']), skipped=len(rag['skipped']))
            questions[key] = {'scenario': scenario, 'question': name, 'text': asked[name], 'start': gold_item['start'],
                              'expected': [_described(ref, labels, types) for ref in expected], 'contexts': summary}
    checks = verify(client, corpus, contexts)
    counted: dict[str, int] = {}
    for chunk in found:
        counted[chunk.kind] = counted.get(chunk.kind, 0) + 1
    return {'format': 'tkos-world-02-retrieval-prepared/0.1', 'content_sha256': manifest['content_sha256'],
            'corpus': {'objects': sum('business' in view for view in corpus['objects']),
                       'snapshots': sum('business' not in view for view in corpus['objects']),
                       'events': len(corpus['events']), 'chunks': counted},
            'full': {'chars': full['chars']}, 'questions': questions, 'scan': triggers.scan(corpus),
            'checks': {'chunks_single_ref': True, **checks},
            'verified': checks['refs_in_text'] and checks['refs_read_back']}


def _seeded(seeded_private: Path, seeded_output: Path) -> tuple[dict, dict]:
    """那次播种的 manifest 与 E&O Agent 的凭据（只从私有文件读）。"""
    manifest = json.loads((seeded_output / 'manifest.json').read_text())
    scope = json.loads((seeded_private / f"scope-{manifest['scope']['scope_id'][:8]}.json").read_text())
    return manifest, scope['actors'][AGENT]


def prepare_command(env_file: Path, seeded_private: Path, seeded_output: Path, private: Path, output: Path,
                    gold_path: Path = gold.GOLD) -> dict:
    """不调模型的准备：批准前也能跑（只校验场景文件与标准答案自洽，并核对播种用的就是这两份文件）。"""
    if private.exists() or output.exists():
        raise ValueError('use fresh private and output paths')
    answers = json.loads(gold_path.read_text())
    scenarios = json.loads((gold_path.parent / answers['scenarios']).read_text())
    spec.validate(scenarios)
    spec.validate_gold(answers, scenarios)
    manifest, actor = _seeded(seeded_private, seeded_output)
    if manifest['content_sha256'] != gold.content_sha256(answers, scenarios):
        raise ValueError('this world was seeded from other scenarios or gold answers')
    h = MethodHarness(env_file.resolve(), output.resolve(), private.resolve())
    try:
        _, url, _ = h.start_api(ROOT / 'src')
        client = h.clients(url, {'actors': {AGENT: actor}})[AGENT]
        try:
            prepared = prepare(client, manifest, answers, output.resolve())
        finally:
            client.close()
    finally:
        h.close()
    public_json(output / 'prepared.json', prepared)
    return prepared


# ------------------------------------------------------------------ run
def run_model(folder: Path, model: str, effort: str, api_url: str, token: str, start: str, scenario: str,
              question: dict, attempt: int, group: str, context: dict | None = None) -> str:
    """一次作答：只开本组读工具的隔离 codex exec；没有工具的组把准备好的上下文写进提示词，并把它取到的集合与字符数
    记进 context.json。返回 ok、contaminated 或 failed（模型遍历组没有一次工具调用到达 MCP server 也算失败）。"""
    tools = GROUPS[group]
    folder.mkdir(parents=True)
    (folder / 'schema.json').write_text(json.dumps(ANSWER, ensure_ascii=False))
    if context is not None:
        public_json(folder / 'context.json', {key: context[key] for key in ('refs', 'event_ids', 'chars')})
    text = prompt(group, start, question['question'], context and context['text'])
    environment = {key: value for key, value in os.environ.items() if key != 'TKOS_WORLD_AGENT_TOKEN'}
    if tools:
        environment['TKOS_WORLD_AGENT_TOKEN'] = token
    began = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='world-exp-') as workdir:
        command = codex_command(model, effort, Path(workdir), folder / 'schema.json', folder / 'answer.json', api_url,
                                folder / 'mcp', text, tools)
        try:
            done = subprocess.run(command, cwd=workdir, env=environment, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=TIMEOUT)
            exit_code, stdout, stderr = done.returncode, done.stdout, done.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code, stdout, stderr = None, _decoded(exc.stdout), _decoded(exc.stderr)
    (folder / 'codex.jsonl').write_text(stdout)
    (folder / 'codex.stderr').write_text(stderr)  # 凭证只在环境变量里，不会出现在这里
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith('{')]
    polluted = contamination(events, tools)
    try:
        jsonschema.validate(json.loads((folder / 'answer.json').read_text()), ANSWER)
        answered = True
    except (OSError, ValueError, jsonschema.ValidationError):
        answered = False
    reached = not tools or any((folder / 'mcp').glob('*.jsonl'))
    status = 'contaminated' if polluted else 'ok' if exit_code == 0 and answered and reached else 'failed'
    public_json(folder / 'run.json', {'group': group, 'scenario': scenario, 'question': question['id'], 'attempt': attempt,
                                      'status': status, 'exit_code': exit_code, 'contamination': polluted,
                                      'seconds': round(time.monotonic() - began, 1)})
    return status


def _decoded(output: bytes | str | None) -> str:
    return output.decode(errors='replace') if isinstance(output, bytes) else output or ''


def _source() -> dict:
    """跑在哪个提交上；src/ 与 experiments/ 下有未提交改动时逐个列出路径（同 0.1）。"""
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', 'status', '--porcelain', '--', 'src', 'experiments'], cwd=ROOT,
                           capture_output=True, text=True).stdout.splitlines()
    paths = sorted(line[3:] for line in dirty if line.strip())
    return {'commit': head, 'src_or_experiments_modified': bool(paths), 'modified_paths': paths}


def run(env_file: Path, seeded_private: Path, seeded_output: Path, private: Path, output: Path, model: str,
        effort: str, groups=tuple(GROUPS), gold_path: Path = gold.GOLD, attempts: int = ATTEMPTS,
        rehearsal: bool = False) -> dict:
    if private.exists() or output.exists():
        raise ValueError('use fresh private and output paths')
    if attempts < 1:
        raise ValueError('attempts must be at least 1')
    private, output = private.resolve(), output.resolve()  # codex 在临时空目录里运行，传给它的路径都要是绝对路径
    manifest, actor = _seeded(seeded_private, seeded_output)
    answers = _gold(rehearsal)(gold_path, manifest)['gold']  # 正式：未批准、批准后改过或播种用的不是批准的内容，都拒跑
    codex = subprocess.run(['codex', '--version'], capture_output=True, text=True).stdout.strip()
    chosen = select(groups)
    h = MethodHarness(env_file.resolve(), output, private)
    try:
        _, url, _ = h.start_api(ROOT / 'src')
        client = h.clients(url, {'actors': {AGENT: actor}})[AGENT]
        public_json(output / 'setup.json', {
            'started_at': datetime.now(timezone.utc).isoformat(), 'model': model, 'effort': effort, 'codex': codex,
            'identity': AGENT, 'contract_version': CONTRACT, 'groups': {name: list(tools) for name, tools in chosen.items()},
            'disabled_features': list(DISABLED), 'attempts': attempts, 'rehearsal': rehearsal,
            'content_sha256': manifest['content_sha256'], 'source': _source()})
        try:
            prepared = prepare(client, manifest, answers, output)
        finally:
            client.close()
        public_json(output / 'prepared.json', prepared)
        if not prepared['verified']:
            raise RuntimeError('the prepared contexts failed their reference checks; see prepared.json')
        full = json.loads((output / 'contexts/full.json').read_text())
        for group in chosen:
            for scenario in answers['scenario_answers']:
                start = counterexamples.parse(counterexamples.resolve(scenario, manifest)['start'])['object_id']
                for item in scenario['questions']:
                    key = metrics.key(scenario['id'], item['id'])
                    context = (None if group == 'traverse' else full if group == 'full'
                               else json.loads((output / f'contexts/{key}.{group}.json').read_text()))
                    for attempt in range(1, attempts + 1):
                        for retry in range(RETRIES + 1):
                            name = f'{key}-{attempt}' + (f'-retry{retry}' if retry else '')
                            if run_model(output / group / name, model, effort, url, actor['token'], start,
                                         scenario['id'], item, attempt, group, context) == 'ok':
                                break
    finally:
        h.close()
    return summarize(seeded_output, output, gold_path)


def _gold(rehearsal: bool):
    return gold.load_for_rehearsal if rehearsal else gold.load_approved


def summarize(seeded_output: Path, output: Path, gold_path: Path = gold.GOLD, observations: Path | None = None) -> dict:
    """按输出目录重算：跑过的每一组各算一份，加上准备结果与四个触发检查。observations 是 #66 的观测结论
    （b_observe 的输出），经 triggers.b_conclusions 摘成 summary 的 b，报告的对照实验 B 一节与第 1 条检查都读它。
    正式还是彩排、每问几次，都按这次运行的 setup.json；没有 setup.json（只做了准备）按正式处理。"""
    manifest = json.loads((seeded_output / 'manifest.json').read_text())
    setup = output / 'setup.json'
    setup = json.loads(setup.read_text()) if setup.exists() else None
    rehearsal = bool(setup and setup.get('rehearsal'))
    answers = _gold(rehearsal)(gold_path, manifest)['gold']
    prepared = json.loads((output / 'prepared.json').read_text())
    if prepared['content_sha256'] != manifest['content_sha256']:
        raise ValueError('the prepared contexts come from another seeding')
    runs = {name: metrics.load_runs(output / name) for name in metrics.GROUPS}
    result = metrics.summarize(metrics.resolve(answers, manifest), runs, setup['attempts'] if setup else ATTEMPTS,
                               prepared)
    result['setup'] = setup
    result['rehearsal'] = rehearsal
    result['b'] = triggers.b_conclusions(json.loads(observations.read_text()) if observations else None)
    result['triggers'] = triggers.evaluate(result)
    result['thresholds'] = triggers.THRESHOLDS
    public_json(output / 'summary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['prepare', 'run', 'summarize'])
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--seeded-private', type=Path)
    parser.add_argument('--seeded-output', type=Path, required=True)
    parser.add_argument('--private', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model')
    parser.add_argument('--effort')
    parser.add_argument('--groups', nargs='+', choices=list(GROUPS), default=list(GROUPS),
                        help='要跑的组：full 全量塞入、fixed 固定路径、traverse 模型遍历、rag；默认都跑')
    parser.add_argument('--b-observations', type=Path, help='#66 对照实验 B 的观测结论（summarize 用）')
    parser.add_argument('--attempts', type=int, default=ATTEMPTS, help=f'run：每问作答几次，默认 {ATTEMPTS}')
    parser.add_argument('--rehearsal', action='store_true',
                        help='run：彩排，标准答案不要求批准，结果不作数（summarize 按 setup.json 自动识别）')
    args = parser.parse_args()
    if args.command == 'summarize':
        result = summarize(args.seeded_output, args.output, observations=args.b_observations)
    else:
        if None in (args.env_file, args.seeded_private, args.private):
            parser.error(f'{args.command} needs --env-file, --seeded-private and --private')
        if args.command == 'prepare':
            prepared = prepare_command(args.env_file, args.seeded_private, args.seeded_output, args.private, args.output)
            print(json.dumps({'verified': prepared['verified'], 'corpus': prepared['corpus'],
                              'full_chars': prepared['full']['chars'],
                              'checks': {key: prepared['checks'][key] for key in
                                         ('chunks_single_ref', 'refs_in_text', 'refs_read_back')}}, ensure_ascii=False))
            if not prepared['verified']:
                raise SystemExit(2)
            return
        if None in (args.model, args.effort):
            parser.error('run needs --model and --effort')
        result = run(args.env_file, args.seeded_private, args.seeded_output, args.private, args.output, args.model,
                     args.effort, args.groups, attempts=args.attempts, rehearsal=args.rehearsal)
    shown = ('runs', 'short', 'recall', 'traceability', 'determinism', 'counterexamples', 'chars', 'verdict')
    print(json.dumps({name: {key: result['groups'][name][key] for key in shown} for name in GROUPS}
                     | {'triggers': {item['id']: item['status'] for item in result['triggers']}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
