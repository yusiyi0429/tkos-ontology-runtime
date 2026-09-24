"""静态截面实验的跑器（票 #29、#32 D）：同一组六问、同一模型、同一数据、同一口径，三组只差给模型的工具：

- B 组：经 tkos-world-mcp 调一次取上下文（每问一次，服务端固定路径）；
- A 组：Codex CLI 经 tkos-world-mcp 自行遍历，四个读工具都给（每问三次）；
- A0 组：纯模型遍历，同 A 但不给取上下文（每问三次）。

另经读投影量全量塞入的长度。指标由 metrics.py 从各次运行的 MCP 运行日志与结构化回答算，每个模型遍历组各自判定。

    python -m experiments.world_v01.experiment run --env-file P/env.json --seeded-private P2 --seeded-output O2 \\
        --private P3 --output O3 --model gpt-6-sol --effort medium [--groups b a a0]
    python -m experiments.world_v01.experiment summarize --seeded-output O2 --output O3   # 只重算指标

前提：标准答案已经 E&O DRI 批准，且就是这次播种用的内容（gold.load_approved 带播种清单核对）。各组都以 E&O Agent
的身份读，都只读：不给写工具。模型遍历每次运行都隔离：空目录、不读用户的 Codex 配置（也就不带用户的其他 MCP server）、
关掉 shell、记忆、联网搜索、插件、apps 与子代理，只开 tkos-world-mcp 里本组的读工具。事件流里出现别的工具就记为污染；
污染与失败的运行不计入指标，同一次重跑到有效为止（最多再试 RETRIES 次），每一次尝试都留在输出目录里。
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

import anyio
import jsonschema

from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import public_json

from . import gold, metrics

ROOT = Path(__file__).resolve().parents[2]
MCP = ROOT / '.venv/bin/tkos-world-mcp'
AGENT = 'eo_agent'
SERVER = 'tkos_world'
READS = ('world_get_object', 'world_get_context', 'world_get_events', 'world_get_state')
# 组 -> 给模型的工具，按跑的顺序：B 固定路径只调取上下文；A 模型遍历给四个读工具；A0 纯模型遍历不给取上下文
# （实验报告建议 6：A 几乎只用取上下文，纯遍历与八类诱饵都没有经受检验）。
GROUPS = {'b': ('world_get_context',), 'a': READS, 'a0': tuple(tool for tool in READS if tool != 'world_get_context')}
TOOL_NAMES = {'world_get_object': '取对象', 'world_get_context': '取上下文', 'world_get_events': '取事件',
              'world_get_state': '取状态'}
ATTEMPTS, RETRIES, TIMEOUT = 3, 3, 1200
# 关掉的 Codex 功能：执行命令，以及记忆、联网、插件、apps、浏览器、子代理这些业务世界以外的来源。代码模式留着：
# 这一版 Codex 的 MCP 工具经代码模式的 exec 调用，其宿主程序在 codex 真实路径的旁边，所以按解析后的路径调用。
DISABLED = ('shell_tool', 'unified_exec', 'memories', 'chronicle', 'apps', 'plugins', 'browser_use', 'computer_use',
            'image_generation', 'multi_agent', 'multi_agent_v2', 'goals', 'sleep_tool', 'view_image', 'skill_search',
            'tool_suggest')
# 事件流里允许出现的条目；其余（命令、改文件、联网搜索、别的 MCP 工具、子代理……）都是污染。
ALLOWED_ITEMS = {'agent_message', 'reasoning', 'error', 'todo_list'}
ANSWER = {
    'type': 'object', 'additionalProperties': False, 'required': ['claims'],
    'properties': {'claims': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['claim', 'kind', 'refs'],
        'properties': {'claim': {'type': 'string'}, 'kind': {'type': 'string', 'enum': ['fact', 'gap']},
                       'refs': {'type': 'array', 'items': {'type': 'string'}}}}}},
}
PROMPT = """你是 E&O 责任单元的执行 Agent。只用 tkos_world 的读工具（{tools}）了解业务世界，回答下面这个关于一条 Activity 的问题。

起点 Activity 的 object_id：{start}
问题：{question}

回答要求：
- 按给定的 JSON 结构回答，claims 是若干条断言；每条写 claim（一句中文）、kind 与 refs。
- kind 为 fact 表示陈述取到的内容；为 gap 表示说明某处为空或没有记录。
- 每条断言都要有 refs，写支撑它的引用，照工具返回里的原样：对象版本 `<object_id>@<版本>`，块 `<object_id>@<版本>#<块 id>`，事件 `event:<event_id>`；gap 断言引出显示为空的那个块或对象。
- 只写工具返回过的内容，不要猜测。"""


def select(names: tuple[str, ...] | list[str]) -> dict[str, tuple[str, ...]]:
    """要跑的组与各组给模型的工具，按 GROUPS 的顺序。"""
    return {name: tools for name, tools in GROUPS.items() if name in names}


def prompt(tools: tuple[str, ...], start: str, question: str) -> str:
    """提示词只列本组的读工具；A 组的与上一轮实验逐字相同。"""
    return PROMPT.format(tools='、'.join(TOOL_NAMES[tool] for tool in tools), start=start, question=question)


def codex_command(model: str, effort: str, workdir: Path, schema: Path, answer: Path, api_url: str, log_dir: Path,
                  prompt: str, tools: tuple[str, ...] = READS) -> list[str]:
    """一次隔离的 codex exec，只开本组的读工具：凭证只经环境变量 TKOS_WORLD_AGENT_TOKEN 透传给 MCP server，不上命令行。"""
    server = f'mcp_servers.{SERVER}'
    codex = str(Path(shutil.which('codex') or 'codex').resolve())  # 真实路径：代码模式的宿主程序在它旁边
    args = [codex, 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check', '--json',
            '-C', str(workdir), '-s', 'read-only', '-m', model, '-c', f'model_reasoning_effort={json.dumps(effort)}',
            '-c', 'web_search="disabled"', '--output-schema', str(schema), '-o', str(answer)]
    for feature in DISABLED:
        args += ['--disable', feature]
    env = f'{{TKOS_WORLD_API_URL = {json.dumps(api_url)}, TKOS_WORLD_MCP_LOG_DIR = {json.dumps(str(log_dir))}}}'
    return args + ['-c', f'{server}.command={json.dumps(str(MCP))}', '-c', f'{server}.env={env}',
                   '-c', f'{server}.env_vars=["TKOS_WORLD_AGENT_TOKEN"]',
                   '-c', f'{server}.enabled_tools={json.dumps(list(tools))}',
                   '-c', f'{server}.default_tools_approval_mode="approve"',  # 只读工具免批准，exec 下无人可批
                   '-c', f'{server}.tool_timeout_sec=150', prompt]


def contamination(events: list[dict], tools: tuple[str, ...] = READS) -> list[str]:
    """事件流里本组 tkos_world 读工具之外的动作。"""
    found = set()
    for event in events:
        item = event.get('item') or {}
        kind = item.get('type')
        if kind is None or kind in ALLOWED_ITEMS:
            continue
        if kind == 'mcp_tool_call' and item.get('server') == SERVER and item.get('tool') in tools:
            continue
        found.add(f"mcp_tool_call:{item.get('server')}/{item.get('tool')}" if kind == 'mcp_tool_call' else kind)
    return sorted(found)


def measure_world(client, manifest: dict, scope: str) -> dict:
    """全量塞入：scope 内每个对象、每条事件各塞一次，按取对象、取事件返回的紧凑 JSON 计字符数。取对象顺带返回的
    最新快照（state）不重复计入，快照本身也是对象。同时记下每个对象每个版本的空块，给「空块被当作有内容」用。"""
    full, empty, events = 0, set(), {}
    objects = [item for item in manifest['objects'].values() if item['scope'] == scope]
    for item in objects:
        path = f"/v1/world/objects/{item['object_id']}"
        view = {key: value for key, value in client.request('GET', path).json().items() if key != 'state'}
        full += _chars(view)
        for version in range(1, item['version'] + 1):
            empty |= {block['ref'] for block in client.request('GET', f'{path}?version={version}').json()['blocks']
                      if block['empty']}
        events.update((event['event_id'], event) for event in client.request('GET', f'{path}/events').json()['events'])
    full += sum(_chars(event) for event in events.values())
    return {'full_chars': full, 'objects': len(objects), 'events': len(events), 'empty_blocks': sorted(empty)}


def _chars(value: dict) -> int:
    """与 HTTP 面返回同样的紧凑 JSON 的字符数。"""
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')))


def _mcp_env(api_url: str, token: str, log_dir: Path) -> dict[str, str]:
    return {'PATH': os.environ.get('PATH', ''), 'TKOS_WORLD_API_URL': api_url, 'TKOS_WORLD_AGENT_TOKEN': token,
            'TKOS_WORLD_MCP_LOG_DIR': str(log_dir)}


def run_b(folder: Path, api_url: str, token: str, start: str, key: str, question: str) -> None:
    """B 组一问：经 tkos-world-mcp 调一次取上下文（预算与近期窗口用默认值）。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=str(MCP), args=[], env=_mcp_env(api_url, token, folder / 'mcp'))

    async def call():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool('world_get_context', {'object_id': start, 'question': question})

    result = anyio.run(call)
    public_json(folder / 'run.json', {'question': key, 'attempt': 1, 'status': 'failed' if result.is_error else 'ok'})


def run_a(folder: Path, model: str, effort: str, api_url: str, token: str, start: str, key: str, question: str,
          attempt: int, tools: tuple[str, ...] = READS) -> str:
    """模型遍历组（A 或 A0）一次：只开本组读工具的隔离 codex exec；返回 ok、contaminated 或 failed（没有一次工具调用
    到达 MCP server 也算失败）。"""
    folder.mkdir(parents=True)
    (folder / 'schema.json').write_text(json.dumps(ANSWER, ensure_ascii=False))
    began = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='world-exp-') as workdir:
        command = codex_command(model, effort, Path(workdir), folder / 'schema.json', folder / 'answer.json', api_url,
                                folder / 'mcp', prompt(tools, start, question), tools)
        try:
            done = subprocess.run(command, cwd=workdir, env={**os.environ, 'TKOS_WORLD_AGENT_TOKEN': token},
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT)
            exit_code, stdout, stderr = done.returncode, done.stdout, done.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code, stdout, stderr = None, _text(exc.stdout), _text(exc.stderr)
    (folder / 'codex.jsonl').write_text(stdout)
    (folder / 'codex.stderr').write_text(stderr)  # 凭证只在环境变量里，不会出现在这里
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith('{')]
    polluted = contamination(events, tools)
    try:
        jsonschema.validate(json.loads((folder / 'answer.json').read_text()), ANSWER)
        answered = True
    except (OSError, ValueError, jsonschema.ValidationError):
        answered = False
    reached = any((folder / 'mcp').glob('*.jsonl'))
    status = 'contaminated' if polluted else 'ok' if exit_code == 0 and answered and reached else 'failed'
    public_json(folder / 'run.json', {'question': key, 'attempt': attempt, 'status': status, 'exit_code': exit_code,
                                      'contamination': polluted, 'seconds': round(time.monotonic() - began, 1)})
    return status


def _text(output: bytes | str | None) -> str:
    return output.decode(errors='replace') if isinstance(output, bytes) else output or ''


def _source() -> dict:
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', 'status', '--porcelain', '--', 'src', 'experiments'], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    return {'commit': head, 'src_or_experiments_modified': bool(dirty)}


def summarize(seeded_output: Path, output: Path) -> dict:
    """按输出目录重算：跑过的每个模型遍历组（a/、a0/）各算一份，B 组作对照。"""
    manifest = json.loads((seeded_output / 'manifest.json').read_text())
    approved = gold.load_approved(gold.GOLD, manifest)
    world = json.loads((output / 'world.json').read_text())
    traversals = {name: metrics.load_runs(output / name) for name in GROUPS if name != 'b' and (output / name).is_dir()}
    result = metrics.summarize(metrics.resolve(approved['gold'], manifest), traversals, metrics.load_runs(output / 'b'),
                               world, attempts=ATTEMPTS)
    result['setup'] = json.loads((output / 'setup.json').read_text())
    public_json(output / 'summary.json', result)
    return result


def run(env_file: Path, seeded_private: Path, seeded_output: Path, private: Path, output: Path, model: str,
        effort: str, groups: tuple[str, ...] = tuple(GROUPS)) -> dict:
    if private.exists() or output.exists():
        raise ValueError('use fresh private and output paths')
    private, output = private.resolve(), output.resolve()  # codex 在临时空目录里运行，传给它的路径都要是绝对路径
    manifest = json.loads((seeded_output / 'manifest.json').read_text())
    answers = gold.load_approved(gold.GOLD, manifest)['gold']
    scope = manifest['objects'][answers['start']]['scope']
    actor = json.loads((seeded_private / 'scopes.json').read_text())[scope]['actors'][AGENT]
    start = manifest['objects'][answers['start']]['object_id']
    codex = subprocess.run(['codex', '--version'], capture_output=True, text=True).stdout.strip()
    chosen = select(groups)
    h = MethodHarness(env_file.resolve(), output.resolve(), private.resolve())
    try:
        _, url, _ = h.start_api(ROOT / 'src')
        client = h.clients(url, {'actors': {AGENT: actor}})[AGENT]
        public_json(output / 'setup.json', {
            'started_at': datetime.now(timezone.utc).isoformat(), 'model': model, 'effort': effort, 'codex': codex,
            'identity': AGENT, 'groups': {name: list(tools) for name, tools in chosen.items()},
            'disabled_features': list(DISABLED), 'attempts': ATTEMPTS, 'content_sha256': manifest['content_sha256'],
            'source': _source()})
        public_json(output / 'world.json', measure_world(client, manifest, scope))
        client.close()
        for group, tools in chosen.items():
            for item in answers['questions']:
                if group == 'b':
                    run_b(output / 'b' / item['id'], url, actor['token'], start, item['id'], item['question'])
                    continue
                for attempt in range(1, ATTEMPTS + 1):
                    for retry in range(RETRIES + 1):
                        name = f"{item['id']}-{attempt}" + (f'-retry{retry}' if retry else '')
                        if run_a(output / group / name, model, effort, url, actor['token'], start, item['id'],
                                 item['question'], attempt, tools) == 'ok':
                            break
    finally:
        h.close()
    return summarize(seeded_output, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['run', 'summarize'])
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--seeded-private', type=Path)
    parser.add_argument('--seeded-output', type=Path, required=True)
    parser.add_argument('--private', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model')
    parser.add_argument('--effort')
    parser.add_argument('--groups', nargs='+', choices=list(GROUPS), default=list(GROUPS),
                        help='要跑的组：b 固定路径、a 模型遍历（四个读工具）、a0 纯模型遍历（不给取上下文）；默认三组都跑')
    args = parser.parse_args()
    if args.command == 'summarize':
        result = summarize(args.seeded_output, args.output)
    else:
        if None in (args.env_file, args.seeded_private, args.private, args.model, args.effort):
            parser.error('run needs --env-file, --seeded-private, --private, --model and --effort')
        result = run(args.env_file, args.seeded_private, args.seeded_output, args.private, args.output, args.model,
                     args.effort, args.groups)
    shown = ('runs', 'short', 'recall', 'traceability', 'determinism', 'counterexamples', 'budget', 'verdict')
    print(json.dumps({name: {key: result[name][key] for key in shown} for name in GROUPS if name != 'b' and name in result}
                     | {'b': {key: result['b'][key] for key in ('runs', 'recall', 'decoys_taken')}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
