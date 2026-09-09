"""generate.py 入口保护测试：拒绝发生在 seed 与进程启动之前（无数据库）。

远程/错误目标配置经 monkeypatch 注入时必须被 validate_environment 拒绝；
create_fixture 不得执行，Harness 不得构造，运行目录不得创建。
有效本机配置下的真实运行由 Codex 验证。
"""
from __future__ import annotations

import pytest

from acceptance.runtime import infra
from acceptance.runtime import seed as seed_module
from acceptance.workbench import generate

LOCAL_DSN = "postgresql://synthetic:synthetic@127.0.0.1:54350/tkos_runtime_acceptance"
LOCAL_ENV = {
    "DATABASE_URL": LOCAL_DSN,
    "APP_DATABASE_URL": LOCAL_DSN,
    "MIGRATION_DATABASE_URL": LOCAL_DSN,
    "TKOS_OBJECT_STORE_ENDPOINT": "http://127.0.0.1:54351",
}


@pytest.fixture
def guards(monkeypatch):
    calls = {"seed": 0, "harness": 0}

    def no_seed(*args, **kwargs):
        calls["seed"] += 1
        raise AssertionError("create_fixture must not run when entry guards reject")

    class NoHarness:
        def __init__(self, *args, **kwargs):
            calls["harness"] += 1
            raise AssertionError("Harness must not start when entry guards reject")

    monkeypatch.setattr(seed_module, "create_fixture", no_seed)
    monkeypatch.setattr(generate, "Harness", NoHarness)
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)
    return calls


def run_main(monkeypatch, run_id):
    monkeypatch.setattr("sys.argv", ["generate.py", "--run-id", run_id])
    with pytest.raises(SystemExit) as caught:
        generate.main()
    assert caught.value.code == 2  # argparse parser.error


@pytest.mark.parametrize("env", [
    {**LOCAL_ENV, "MIGRATION_DATABASE_URL": LOCAL_DSN.replace("127.0.0.1", "203.0.113.10")},
    {**LOCAL_ENV, "DATABASE_URL": LOCAL_DSN.replace("/tkos_runtime_acceptance", "/synthetic_other")},
    {**LOCAL_ENV, "TKOS_OBJECT_STORE_ENDPOINT": "http://203.0.113.10:54351"},
    {key: value for key, value in LOCAL_ENV.items() if key != "APP_DATABASE_URL"},
])
def test_non_local_targets_rejected_before_any_seeding(guards, monkeypatch, env):
    monkeypatch.setattr(infra, "load_environment", lambda: env)
    run_main(monkeypatch, "workbench-guard-remote")
    assert guards == {"seed": 0, "harness": 0}
    output = generate.ROOT / "artifacts" / "runtime-acceptance" / "workbench-guard-remote"
    assert not output.exists()


def test_existing_run_directory_rejected_before_any_seeding(guards, monkeypatch, tmp_path):
    monkeypatch.setattr(infra, "load_environment", lambda: dict(LOCAL_ENV))
    existing = generate.ROOT / "artifacts" / "runtime-acceptance" / "workbench-guard-clash"
    existing.mkdir(parents=True)
    try:
        run_main(monkeypatch, "workbench-guard-clash")
        assert guards == {"seed": 0, "harness": 0}
    finally:
        existing.rmdir()


def test_unsafe_run_id_rejected_before_loading_environment(guards, monkeypatch):
    def no_load():
        raise AssertionError("environment must not be consulted for an unsafe run-id")

    monkeypatch.setattr(infra, "load_environment", no_load)
    run_main(monkeypatch, "../outside")
    assert guards == {"seed": 0, "harness": 0}
