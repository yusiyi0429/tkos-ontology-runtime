"""静态截面实验跑器（票 #29）的隔离：Codex 事件流里 tkos_world 四个读工具之外的动作都算污染（干净的一份录自
真实运行、去掉了内容，污染的一份按同样格式构造）；每次 codex exec 都不读用户配置、关掉 shell 与记忆等来源、
只开四个读工具，凭证不上命令行。不接真模型。"""
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
