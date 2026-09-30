"""tkos.world/0.2 独立验收的实验 E 场景（票 #67）：在同一个隔离库上新开一个随机 scope，经 HTTP 播种实验 E 的五个场景，
再从各场景起点做回放检查（只经读投影、取上下文与只读 SQL）。

放在全部场景之后跑：它不用前面场景的主干与身份，身份、控制面与业务记录都在自己的新 scope 里播。检查不证明矩阵的
格，照列在 CHECKS 里。播种与回放的实现在 experiments/world_v02（seed、replay），这里独立核对 scope 里的行数与
清单不含凭据。
"""
from __future__ import annotations

import json

from experiments.world_v02 import replay, seed, spec


def experiment_e(book, h, source, url):
    check = book.check
    scenarios = json.loads(seed.SCENARIOS.read_text())
    answers = json.loads(seed.GOLD.read_text())
    try:
        spec.validate(scenarios)
        spec.validate_gold(answers, scenarios)
        consistent = True
    except spec.SeedError:
        consistent = False
    check('experiment_e_the_scenarios_and_the_gold_answers_are_consistent', consistent)

    manifest, clients, scope = seed.seed(h, source, url, scenarios, answers)
    try:
        steps = spec.steps(scenarios)
        counts = h.sql(scope, """SELECT (SELECT count(*) FROM gov_objects WHERE scope_id=%s) AS objects,
                                        (SELECT count(*) FROM gov_world_events WHERE scope_id=%s) AS events,
                                        (SELECT count(*) FROM gov_world_events WHERE scope_id=%s
                                                AND contract_version='tkos.world/0.2') AS v02_events,
                                        (SELECT count(*) FROM gov_action_receipts WHERE scope_id=%s) AS receipts""",
                       (scope['scope_id'],) * 4)[0]
        check('experiment_e_the_five_scenarios_are_seeded_over_http_into_a_fresh_scope',
              counts['objects'] == len(manifest['objects']) == len(spec.final_versions(scenarios))
              and counts['events'] == counts['v02_events'] == counts['receipts'] == len(steps)
              and set(manifest['events']) == {step['key'] for _, step in steps
                                              if step.get('key') and step['do'] not in spec.MAKES_OBJECT})
        shown = json.dumps(manifest)
        check('experiment_e_the_manifest_carries_no_credentials',
              not any(actor['token'] in shown for actor in scope['actors'].values()))
        result = replay.check(answers, scenarios, manifest, clients[seed.READER], h, scope)
        book.metadata['experiment_e'] = {'scope_id': scope['scope_id'], 'summary': result['summary'],
                                         'context': {name: item['context'] for name, item in result['scenarios'].items()}}
        for name in spec.SCENARIOS:
            summary = result['summary'][name]
            check(f'experiment_e_{name}_recovers_the_six_questions_from_its_start', all(summary['questions'].values()))
            check(f'experiment_e_{name}_has_its_decoys_in_place', all(summary['decoys'].values()))
    finally:
        for client in clients.values():
            client.close()
