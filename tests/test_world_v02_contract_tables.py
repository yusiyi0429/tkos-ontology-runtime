"""tkos.world/0.2 契约里由登记生成的表：与登记逐字一致，改登记不重生成即失败。

生成脚本 scripts/render_world_v02_tables.py 以登记 JSON 为唯一来源；这里对真契约与真登记跑一遍，
再各改一处，确认漂移被发现、重生成能恢复。
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_world_v02_tables.py"
CONTRACT = ROOT / "docs" / "contracts" / "tkos-world-0.2.md"
REGISTRY = ROOT / "docs" / "contracts" / "world-registry-0.2.json"

spec = importlib.util.spec_from_file_location("render_world_v02_tables", SCRIPT)
tables = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(tables)


@pytest.fixture
def registry():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


@pytest.fixture
def contract():
    return CONTRACT.read_text(encoding="utf-8")


def test_the_contract_tables_match_the_registry(registry, contract):
    assert tables.sync(contract, registry) == contract


def test_every_generated_table_is_in_the_contract(registry, contract):
    rendered = tables.render(registry)
    assert len(rendered) == 13
    for name, text in rendered.items():
        assert contract.count(text) == 1, name


def test_a_known_row_is_rendered_from_the_registry(registry):
    rows = tables.render(registry)["lifecycle-Mission"].splitlines()
    assert "| 退回 `world_reject` | 已交付 | 调整中 | RU DRI |  |" in rows
    assert ("| 取消 `world_cancel` | 草稿、已承诺、已成立、进行中、已交付、调整中 | 已取消 | RU DRI |  |") in rows


def test_changing_the_registry_without_regenerating_is_caught(registry, contract):
    changed = copy.deepcopy(registry)
    state = next(item for item in changed["lifecycles"]["Task"]["states"] if item["id"] == "adjusting")
    state["display_name"] = "返工中"
    synced = tables.sync(contract, changed)
    assert synced != contract
    assert "返工中" in synced
    assert tables.sync(synced, changed) == synced


def test_a_hand_edit_to_a_generated_table_is_caught_and_regeneration_restores_it(registry, contract):
    edited = contract.replace("| 退回 `world_reject` | 已交付 | 调整中 | Mission DRI |  |",
                              "| 退回 `world_reject` | 已交付 | 调整中 | Task DRI |  |", 1)
    assert edited != contract
    assert tables.sync(edited, registry) == contract


def test_a_new_recorder_without_a_label_fails_loudly(registry):
    changed = copy.deepcopy(registry)
    for item in changed["lifecycles"]["Task"]["transitions"]:
        if item["action"] == "world_start":
            item["by"] = "self_or_agent"
    with pytest.raises(KeyError):
        tables.render(changed)


def test_the_check_command_passes_on_the_committed_contract():
    result = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_check_command_fails_on_drift(tmp_path, registry, contract):
    changed = copy.deepcopy(registry)
    changed["event_kinds"][0]["display_name"] = "新建对象"
    registry_file, contract_file = tmp_path / "registry.json", tmp_path / "contract.md"
    registry_file.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    contract_file.write_text(contract, encoding="utf-8")
    command = [sys.executable, str(SCRIPT), "--registry", str(registry_file), "--contract", str(contract_file)]
    assert subprocess.run([*command, "--check"], capture_output=True).returncode == 1
    assert subprocess.run(command, capture_output=True).returncode == 0
    assert subprocess.run([*command, "--check"], capture_output=True).returncode == 0
    assert contract_file.read_text(encoding="utf-8") != contract
