"""从通过的 world 验收报告生成公开摘要与冻结检查点。报告没通过、源码在运行中变过、矩阵不是冻结的那份，
都拒绝生成，什么也不写。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from acceptance.protocol_a1_independent.support import public_json

from .matrix import COVERAGE, MATRIX, NOT_APPLICABLE, REQUIRED_GATES, SOURCES, TITLES


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def summarize(report_path: Path, summary_path: Path, checkpoint_path: Path) -> dict:
    report = json.loads(report_path.read_text())
    total = sum(map(len, MATRIX.values()))
    assert report['world_api_accepted'] is True and not report.get('run_error')
    assert report['required_checks'] == MATRIX and report['checks_passed'] == total and report['passed'] == len(MATRIX)
    assert report['all_selected_scenarios_completed'] is True
    assert all(report['gates'][name]['passed'] is True for name in REQUIRED_GATES)
    assert report['source_commit'] and report['frozen_files']
    before = json.loads((report_path.parent / 'source-before.json').read_text())
    assert before == json.loads((report_path.parent / 'source-after.json').read_text())
    manifest_sha256 = _sha256(json.dumps(before, sort_keys=True, separators=(',', ':')).encode())
    summary = {
        'scope': 'tkos.world/0.1 APIs', 'sources': SOURCES, 'status': 'independently_accepted_locally',
        'world_api_accepted': True, 'source_commit': report['source_commit'], 'completed_at': report['updated_at'],
        'groups': len(MATRIX), 'passed_groups': report['passed'], 'mandatory_checks': total,
        'passed_checks': report['checks_passed'],
        'checks_per_group': {group: {'title': TITLES[group], 'checks': len(names)} for group, names in MATRIX.items()},
        'gates': {name: report['gates'][name]['passed'] for name in sorted(REQUIRED_GATES)},
        'scenarios': {name: row['status'] for name, row in report['scenarios'].items()},
        'action_coverage': {action: {kind: len(names) for kind, names in cells.items()}
                            for action, cells in COVERAGE.items()},
        'not_applicable': NOT_APPLICABLE,
        'source_files': len(before), 'source_manifest_sha256': manifest_sha256,
        'report_sha256': _sha256(report_path.read_bytes()),
        'excluded_capabilities': report['excluded_capabilities'], 'released': False, 'deployed': False,
    }
    rows = [f'| `{path}` | `{digest}` |' for path, digest in sorted(report['frozen_files'].items())]
    checkpoint = '\n'.join([
        '# World 0.1 freeze checkpoint', '',
        f"- 时间（UTC）: {report['updated_at']}",
        f"- 提交: `{report['source_commit']}`",
        f"- 验收：`world_api_accepted: true`，{len(MATRIX)} 组 {total} 项检查、{len(REQUIRED_GATES)} 个环境门槛全部通过；"
        f"摘要见 [{summary_path.name}]({os.path.relpath(summary_path, checkpoint_path.parent)})",
        f"- 源码清单 SHA256（src/ 下 {len(before)} 个文件）: `{manifest_sha256}`",
        f"- 文件清单：相对基线 {SOURCES['base_commit']} 改动过的 src/ 文件，加上 world 契约、登记、profile 与支持登记，"
        f"共 {len(rows)} 个", '',
        '| 文件 | SHA256 |', '|---|---|', *rows, '',
    ])
    public_json(summary_path, summary)
    checkpoint_path.write_text(checkpoint, encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    args = parser.parse_args()
    if not __debug__:
        raise SystemExit('the refusals are written as assertions; run without -O')
    result = summarize(args.report, args.summary, args.checkpoint)
    print(json.dumps({name: result[name] for name in ['world_api_accepted', 'source_commit', 'passed_checks']}))


if __name__ == '__main__':
    main()
