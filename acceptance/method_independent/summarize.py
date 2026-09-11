"""Publish a compact, evidence-derived summary only after complete acceptance."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from acceptance.protocol_a1_independent.support import public_json
from .matrix import MATRIX, REQUIRED_GATES, SOURCES


def summarize(report_path: Path, output: Path):
    report = json.loads(report_path.read_text())
    assert report['method_api_accepted'] is True
    assert report['passed'] == len(MATRIX) and report['checks_passed'] == sum(map(len, MATRIX.values()))
    assert report['all_selected_scenarios_completed'] is True
    assert all(report['gates'][name]['passed'] is True for name in REQUIRED_GATES)
    assert report['required_checks'] == MATRIX and not report.get('run_error')
    transcript = report_path.parent / 'http-transcript.jsonl'
    rows = [json.loads(line) for line in transcript.read_text().splitlines()]
    manifest = json.loads((report_path.parent / 'source-before.json').read_text())
    assert manifest == json.loads((report_path.parent / 'source-after.json').read_text())
    result = {
        'scope': 'tkos.method/0.1 APIs', 'sources': SOURCES,
        'status': 'independently_accepted_locally', 'method_api_accepted': True,
        'completed_at': report['updated_at'], 'mandatory_cases': len(MATRIX),
        'passed_cases': report['passed'], 'mandatory_checks': sum(map(len, MATRIX.values())),
        'passed_checks': report['checks_passed'],
        'gates': {name: report['gates'][name]['passed'] for name in sorted(REQUIRED_GATES)},
        'scenarios': {name: row['status'] for name, row in report['scenarios'].items()},
        'http_requests': len(rows), 'expected_4xx_responses': sum(400 <= row['status'] < 500 for row in rows),
        'injected_500_responses': sum(row['status'] == 500 for row in rows),
        'source_files': len(manifest), 'source_manifest_sha256': hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'report_sha256': hashlib.sha256(report_path.read_bytes()).hexdigest(),
        'http_transcript_sha256': hashlib.sha256(transcript.read_bytes()).hexdigest(),
        'excluded_capabilities': report['excluded_capabilities'],
        'released': False, 'deployed': False,
    }
    public_json(output, result)
    public_json(output.with_name(output.stem + '-source-sha256.json'), manifest)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.report, args.output)
    print(json.dumps({name: result[name] for name in ['method_api_accepted', 'passed_cases', 'passed_checks', 'http_requests']}))


if __name__ == '__main__':
    main()
