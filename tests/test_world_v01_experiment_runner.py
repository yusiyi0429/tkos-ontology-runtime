"""静态截面实验跑器（票 #29）的隔离：Codex 事件流里本组读工具之外的动作都算污染（干净的一份录自
真实运行、去掉了内容，污染的一份按同样格式构造）；每次 codex exec 都不读用户配置、关掉 shell 与记忆等来源、
只开本组的读工具，凭证不上命令行。四组（#32 D）：B 固定路径取上下文、不作答；F 固定上下文作答（模型只看 B 那样
取到的上下文，不给工具）；A 模型遍历给四个读工具；A0 纯模型遍历不给取上下文。三个作答组同一模型、同一数据、
同一口径，只差模型拿到上下文的方式。不接真模型。"""
import json
from pathlib import Path

from experiments.world_v01 import experiment

FIXTURE = Path(__file__).resolve().parent / 'fixtures/world_v01_experiment/codex'


def events(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURE / name).read_text().splitlines()]


def test_a_run_that_only_read_through_tkos_world_is_clean():
    assert experiment.contamination(events('clean.jsonl')) == []


def test_commands_other_mcp_servers_writes_and_web_search_are_contamination():
    assert experiment.contamination(events('polluted.jsonl')) == [
        'command_execution', 'mcp_tool_call:pencil/open_document', 'mcp_tool_call:tkos_world/world_revise_object', 'web_search']


def test_each_codex_run_is_isolated_and_carries_no_credential(tmp_path):
    command = experiment.codex_command('gpt-6-sol', 'medium', tmp_path, tmp_path / 's.json', tmp_path / 'a.json',
                                       'http://127.0.0.1:8010', tmp_path / 'mcp', '问题')
    joined = ' '.join(command)
    assert command[1] == 'exec' and '--ignore-user-config' in command and '--ephemeral' in command
    assert ['-s', 'read-only'] == command[command.index('-s'):command.index('-s') + 2]
    disabled = {command[i + 1] for i, arg in enumerate(command) if arg == '--disable'}
    assert {'shell_tool', 'unified_exec', 'memories', 'apps', 'plugins', 'multi_agent'} <= disabled
    assert 'web_search="disabled"' in command
    assert ('mcp_servers.tkos_world.enabled_tools=["world_get_object", "world_get_context", "world_get_events", '
            '"world_get_state"]') in command
    assert 'mcp_servers.tkos_world.env_vars=["TKOS_WORLD_AGENT_TOKEN"]' in command and 'TOKEN=' not in joined
    assert command[-1] == '问题'


def test_the_groups_differ_only_in_how_the_model_gets_context_and_run_in_a_fixed_order():
    assert experiment.GROUPS == {
        'b': ('world_get_context',),
        'f': (),
        'a': ('world_get_object', 'world_get_context', 'world_get_events', 'world_get_state'),
        'a0': ('world_get_object', 'world_get_events', 'world_get_state')}
    assert list(experiment.select(['a0', 'b'])) == ['b', 'a0'] and experiment.select(['a0'])['a0'] == experiment.GROUPS['a0']
    assert list(experiment.select(list(experiment.GROUPS))) == ['b', 'f', 'a', 'a0']


def test_a_fixed_context_run_offers_no_tools_and_answers_only_from_the_context_in_its_prompt(tmp_path):
    command = experiment.codex_command('gpt-6-sol', 'medium', tmp_path, tmp_path / 's.json', tmp_path / 'a.json',
                                       'http://127.0.0.1:8010', tmp_path / 'mcp', '问题', experiment.GROUPS['f'])
    assert not any(arg.startswith('mcp_servers.') for arg in command) and command[-1] == '问题'
    context = '{"context_pack_id": "p", "markdown": "# 上下文"}'
    text = experiment.prompt_fixed('x', '为什么？', context)
    assert context in text and '只根据它回答' in text and '不要猜测' in text
    # 回答要求与 A 组逐条相同，只把「工具返回」换成「上面的返回」。
    a_rules = experiment.prompt(experiment.GROUPS['a'], 'x', '为什么？').split('回答要求：')[1]
    assert text.split('回答要求：')[1] == a_rules.replace('工具返回', '上面的返回')


def test_any_tkos_world_call_in_a_fixed_context_run_is_contamination():
    found = experiment.contamination(events('clean.jsonl'), experiment.GROUPS['f'])
    assert found and all(item.startswith('mcp_tool_call:tkos_world/') for item in found)


def test_a_pure_traversal_run_offers_three_read_tools_and_its_prompt_does_not_mention_get_context(tmp_path):
    tools = experiment.GROUPS['a0']
    command = experiment.codex_command('gpt-6-sol', 'medium', tmp_path, tmp_path / 's.json', tmp_path / 'a.json',
                                       'http://127.0.0.1:8010', tmp_path / 'mcp', '问题', tools)
    assert 'mcp_servers.tkos_world.enabled_tools=["world_get_object", "world_get_events", "world_get_state"]' in command
    assert '取对象、取事件、取状态' in experiment.prompt(tools, 'x', '为什么？') and '取上下文' not in experiment.prompt(tools, 'x', '为什么？')
    # A 组的提示词与上一轮实验逐字相同。
    assert '（取对象、取上下文、取事件、取状态）' in experiment.prompt(experiment.GROUPS['a'], 'x', '为什么？')


def test_a_get_context_call_in_a_pure_traversal_run_is_contamination():
    assert experiment.contamination(events('clean.jsonl'), experiment.GROUPS['a0']) == [
        'mcp_tool_call:tkos_world/world_get_context']
    assert experiment.contamination(events('clean.jsonl'), experiment.GROUPS['a']) == []


def test_the_review_material_takes_the_valid_run_of_the_first_attempt(tmp_path):
    """人工核验材料每问每组取一份回答：第 1 次尝试有效就取它，否则取它有效的那次重试，不拿第 2、3 次顶替。"""
    from experiments.world_v01 import review

    for name, status in (('why-1', 'contaminated'), ('why-1-retry1', 'failed'), ('why-1-retry2', 'ok'),
                         ('why-2', 'ok'), ('who-1', 'ok'), ('now-1', 'failed'), ('now-2', 'ok')):
        (tmp_path / name).mkdir()
        (tmp_path / name / 'run.json').write_text(json.dumps({'status': status}))
    assert review.first_valid_run(tmp_path, 'why').name == 'why-1-retry2'
    assert review.first_valid_run(tmp_path, 'who').name == 'who-1'
    assert review.first_valid_run(tmp_path, 'now') is None
