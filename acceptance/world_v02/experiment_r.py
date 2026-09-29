"""tkos.world/0.2 独立验收的四种取法对照（票 #68）：在同一个隔离库上新开一个随机 scope，经 HTTP 播种实验 E 的五个
场景，跑实验跑器的准备（全量文本、每问的固定路径返回与 RAG 结果），核对它交给模型的文本。不调模型。

放在对照实验 B 之后、最后跑：它不用前面场景的主干与身份，身份、控制面与业务记录都在自己的新 scope 里播（与实验 E 同一段
播种代码，另开一个 scope）。检查不证明矩阵的格，照列在 CHECKS 里。准备与核对的实现在 experiments/world_v02
（experiment.prepare、retrieval），这里另外独立核对：

- 全量文本覆盖整个 scope：读投影取到的对象与事件数等于只读 SQL 数出的 scope 里的对象与事件数；
- 每个分块只带一条自己的引用：重新经读投影取一遍、重新切分，每块的来源用 MCP 运行日志的口径（_content）判出的恰好是
  它自己的引用，且写在它的第一行；
- 取到的引用都在给模型的文本里：逐个读准备写下的上下文文件（全量一份，每问固定路径与 RAG 各一份），取到的每一项都
  以业务形式出现在文本里；
- 取到的引用都能经读投影读回：对象、块与组件按版本取对象找得到，事件在读投影取到的事件里；
- 同成本对照：每问 RAG 装入的字符数不超过固定路径这一问交给模型的字符数。
"""
from __future__ import annotations

import json

from experiments.world_v02 import experiment, retrieval, seed, spec
from tkos_world_mcp.server import _content
from tkos_world_mcp.tools_v02 import FACE


def experiment_r(book, h, source, url):
    check = book.check
    scenarios = json.loads(seed.SCENARIOS.read_text())
    answers = json.loads(seed.GOLD.read_text())
    manifest, clients, scope = seed.seed(h, source, url, scenarios, answers)
    try:
        check('experiment_r_the_five_scenarios_are_seeded_over_http_into_another_fresh_scope',
              set(manifest['objects']) == set(spec.final_versions(scenarios)))
        reader = clients[experiment.AGENT]
        out = h.output / 'experiment-r'
        prepared = experiment.prepare(reader, manifest, answers, out)
        counts = h.sql(scope, """SELECT (SELECT count(*) FROM gov_objects WHERE scope_id=%s) AS objects,
                                        (SELECT count(*) FROM gov_world_events WHERE scope_id=%s) AS events""",
                       (scope['scope_id'],) * 2)[0]
        corpus = retrieval.read_corpus(reader)
        check('experiment_r_the_full_text_holds_every_object_and_event_of_the_scope',
              len(corpus['objects']) == counts['objects']
              == prepared['corpus']['objects'] + prepared['corpus']['snapshots']
              and len(corpus['events']) == counts['events'] == prepared['corpus']['events'])

        def own(chunk):
            refs, events = _content(chunk.source, FACE)
            return refs | {f'event:{event}' for event in events} == {chunk.ref} and chunk.ref in chunk.text.splitlines()[0]
        found = retrieval.chunks(corpus)  # 一块的来源带出别的引用就抛错
        check('experiment_r_every_chunk_carries_exactly_its_own_reference',
              prepared['checks']['chunks_single_ref'] and all(own(chunk) for chunk in found)
              and len({chunk.ref for chunk in found}) == len(found)
              and sum(prepared['corpus']['chunks'].values()) == len(found))

        contexts = {path.name[:-len('.json')]: json.loads(path.read_text()) for path in (out / 'contexts').glob('*.json')}
        taken = {name: set(item['refs']) | {f'event:{event}' for event in item['event_ids']} for name, item in contexts.items()}
        check('experiment_r_every_taken_reference_is_written_in_the_text_handed_to_the_model',
              prepared['checks']['refs_in_text'] and len(contexts) == 1 + 2 * len(prepared['questions'])
              and all(taken[name] and all(ref in contexts[name]['text'] for ref in taken[name]) for name in contexts))
        again = experiment.verify(reader, corpus, contexts)
        check('experiment_r_every_taken_reference_reads_back_through_the_read_projection',
              prepared['checks']['refs_read_back'] and again['refs_read_back'] and prepared['verified'])
        check('experiment_r_each_rag_text_stays_within_the_characters_the_fixed_path_hands_to_the_model',
              all(contexts[f'{key}.rag']['chars'] <= contexts[f'{key}.rag']['max_chars'] == contexts[f'{key}.fixed']['chars']
                  for key in prepared['questions']))
        book.metadata['experiment_r'] = {
            'scope_id': scope['scope_id'], 'corpus': prepared['corpus'], 'full_chars': prepared['full']['chars'],
            'questions': {key: {group: {name: item['contexts'][group][name] for name in ('chars', 'recall')}
                                for group in ('full', 'fixed', 'rag')}
                          for key, item in prepared['questions'].items()}}
    finally:
        for client in clients.values():
            client.close()
