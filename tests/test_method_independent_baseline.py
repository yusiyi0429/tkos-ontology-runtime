"""Method 独立验收的环境保留基线：清单里的文件缺失时照样能取基线并比对。

DEFERRED 里有几项从未入库，干净检出上不存在；缺失记为 None，出现或消失都算变化。
不需要数据库或 Docker。
"""
from __future__ import annotations

from acceptance.method_independent import baseline


def _snapshot(root):
    return {"files": baseline.file_digests(root), "containers": []}


def test_missing_listed_files_do_not_break_the_baseline(tmp_path) -> None:
    present = baseline.DEFERRED[0]
    (tmp_path / present).parent.mkdir(parents=True)
    (tmp_path / present).write_text("unchanged\n")
    digests = baseline.file_digests(tmp_path)
    assert set(digests) == set(baseline.DEFERRED)
    assert digests[present] is not None
    assert all(digests[name] is None for name in baseline.DEFERRED[1:])
    assert baseline.compare(_snapshot(tmp_path), _snapshot(tmp_path))["files_unchanged"] is True


def test_a_listed_file_appearing_counts_as_a_change(tmp_path) -> None:
    before = _snapshot(tmp_path)
    target = tmp_path / baseline.DEFERRED[-1]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("new\n")
    assert baseline.compare(before, _snapshot(tmp_path))["files_unchanged"] is False


def test_the_real_checkout_captures_without_error() -> None:
    assert set(baseline.file_digests()) == set(baseline.DEFERRED)
