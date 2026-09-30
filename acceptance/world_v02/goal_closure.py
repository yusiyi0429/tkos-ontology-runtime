"""tkos.world/0.2 独立验收的长期目标与周期目标收口（票 #60，契约第 6、7、10.2、10.3、11、14、15.1 节）。

放在 Issue 场景之后、撤销 CEO 指派之前跑，对象都新建，不动前面场景的主干。进入本场景时 scope 里还没有任何已确认的公司
复盘（复盘确认本票才接入），主干的单元长期目标已由 gates 场景确认。再确认、复盘确认、取消与终止、形成锚定逐条经
prepare 与 commit 驱动；拒绝在两个入口上核对库快照不变；事件、回执与修订行用只读 SQL 独立核对。复盘确认的目标是
快照：快照不修订、对象行不前进，目标的期望版本取列对象头里的 object_version（本场景给每条快照一个独有的周期，按
类型与周期列出它）。

#69 另补两处：一轮重走的候选可以改指 review_ref（补 45），首期没带 review_ref 的周期目标由此开轮并写回；一轮进行中
周期目标被复盘确认关闭或被取消，这一轮作废，撤回那条事件则连同候选恢复（补 47）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from acceptance.method_independent.fixture import uid

REVIEW = 'world_confirm_review'


def utc(moment):
    moment = moment if isinstance(moment, datetime) else datetime.fromisoformat(str(moment).replace('Z', '+00:00'))
    text = moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    return text + (f'.{moment.microsecond:06d}' if moment.microsecond else '') + 'Z'


def goal_closure(book, h, f, flow, trunk, event_kinds):
    check = book.check
    made = trunk['made']
    scope = f['scope_id']
    actor_id = {name: actor['principal_id'] for name, actor in f['actors'].items()}
    unit, company, company_goal = made['ResponsibilityUnit'], made['Company'], made['LongTermGoal']
    # 快照的时点从十分钟前起按秒排开，带非零的微秒，不会与前面场景写过的快照撞上同一时点。
    base = datetime.now(timezone.utc).replace(microsecond=250001) - timedelta(minutes=10)
    periods = iter(f'{year}-{month:02d}' for year in (2034, 2035) for month in range(1, 13))

    def at(seconds):
        return utc(base + timedelta(seconds=seconds))

    def listed(**query):
        return flow.clients['outsider'].json('GET', '/v1/world/objects?' + urlencode(query))['items']

    def view(oid):
        return flow.read('outsider', oid)

    def life(oid):
        return view(oid)['records']['lifecycle']

    def ref_of(oid):
        return f"{oid}@{view(oid)['business']['version']}"

    def receipt(actor, kind, oid, params=None, target=None):
        return flow.commit(actor, flow.prepare(actor, flow.targeted(kind, oid, params or {}, target)))

    def act(actor, kind, oid, params=None, target=None):
        return receipt(actor, kind, oid, params, target)['result']

    def deny(actor, kind, oid, codes, params=None, says=None, target=None):
        flow.deny(actor, flow.targeted(kind, oid, params or {}, target), codes=codes, says=says)

    def deny_revise(actor, oid, patch, codes, says=None):
        flow.deny(actor, flow.targeted('world_revise_object', oid, {'payload': patch}), codes=codes, says=says)

    def event(event_id):
        """事件行（只读 SQL），事件 id 按文本给，便于与 HTTP 读回的对照。"""
        row = flow.rows('SELECT e.*, r.action_type FROM gov_world_events e JOIN gov_action_receipts r '
                        'ON r.scope_id=e.scope_id AND r.receipt_id=e.action_id WHERE e.scope_id=%s AND e.event_id=%s',
                        (scope, event_id))[0]
        return {**row, 'event_id': str(row['event_id'])}

    def kinds_of(receipt_id):
        return [row['kind'] for row in flow.rows('SELECT kind FROM gov_world_events WHERE scope_id=%s AND action_id=%s',
                                                 (scope, receipt_id))]

    def revisions(oid):
        return flow.rows('SELECT count(*) AS n FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s',
                         (scope, oid))[0]['n']

    def issue_events():
        return flow.rows("SELECT count(*) AS n FROM gov_world_events WHERE scope_id=%s AND kind LIKE 'issue.%%'",
                         (scope,))[0]['n']

    def subjects(row):
        return [str(item['object_id']) for item in row['subject_refs']]

    def logic_patch(cid, text):
        """候选只改目标定义里的一条实现逻辑（#80：实现逻辑从单独的块变成目标定义块里的组件）。"""
        return {'target': {'components': [{'id': cid, 'type': 'realization_logic', 'text': text}]}}

    def logic(read):
        """目标定义块里实现逻辑组件的文字。"""
        target = {block['id']: block for block in read['business']['blocks']}['target']
        return [item['text'] for item in target['components'] if item['type'] == 'realization_logic']

    def ltg(title):
        return flow.create('a', 'LongTermGoal', 'a', {'title': title, 'scope': 'unit', 'horizon': '2029',
                                                     'parent_ref': unit['ref'], 'goal_ref': company_goal['ref']})['result']

    def period_goal(title, period, goal, review=None):
        payload = {'title': title, 'period': period, 'goal_ref': goal['ref']}
        if review is not None:
            payload['review_ref'] = review['ref']
        return flow.create('a', 'PeriodGoal', 'a', payload)['result']

    source = flow.record('ceo', {'category': 'meeting', 'subject_refs': [company['ref']], 'occurred_at': at(0),
                                 'content': {'text': '月度经营复盘会'}})['result']

    def snapshot(actor, oid, payload_type, seconds, blocks):
        """写一条快照，返回回执结果另加复盘确认用的目标（期望版本取列对象头，快照按独有的周期列出）。"""
        period = next(periods)
        written = flow.refresh(actor, {'title': f'{payload_type} {period}', 'subject_ref': ref_of(oid),
                                       'as_of': at(seconds), 'period': period, 'payload_type': payload_type,
                                       'source_event_refs': [f"event:{source['event_id']}"], 'blocks': blocks})['result']
        header, = listed(type='StateSnapshot', period=period)
        assert header['object_id'] == written['object_id'] and header['revision_id'] == written['revision_id']
        written['target'] = {'object_id': written['object_id'], 'revision_id': written['revision_id'],
                             'expected_version': header['object_version']}
        return written

    def review_act(actor, snap, params=None):
        return receipt(actor, REVIEW, snap['object_id'], params, snap['target'])

    def deny_review(actor, snap, codes, params=None, says=None):
        deny(actor, REVIEW, snap['object_id'], codes, params, says, snap['target'])

    def confirmed_review(oid):
        return view(oid)['records']['confirmed_review']

    def snapshot_read(oid):
        return {key: value for key, value in view(oid).items() if key != 'protocol'}

    # ================================================================ 形成锚定：第一个周期
    goal = ltg('战场 A 三年目标（收口）')
    gid = goal['object_id']
    first = period_goal('11 月目标（收口）', '2031-11', goal)
    fid = first['object_id']
    deny('a', 'world_commit_period_goal', fid, {'INVALID_STATE'}, says='formation_anchors')
    check('formation_a_commitment_on_a_draft_long_term_goal_is_refused')

    issues = issue_events()
    confirmed = receipt('ceo', 'world_confirm_long_term_goal', gid,
                        {'outcome': 'accepted', 'returns_to': 'strategy', 'content': {'text': 'B 战场投入需要战略处理'}})
    row = event(confirmed['result']['event_id'])
    check('a_long_term_goal_confirmation_records_returns_to_strategy_in_its_detail_and_nothing_else',
          life(gid) == {'status': 'confirmed', 'display_name': '已确认', 'event_id': confirmed['result']['event_id']}
          and row['kind'] == 'confirm' and row['outcome'] == 'accepted' and row['detail'] == {'returns_to': 'strategy'}
          and kinds_of(confirmed['receipt_id']) == ['confirm'] and confirmed['result']['version'] == goal['version']
          and issue_events() == issues)
    deny('ceo', 'world_confirm_long_term_goal', gid, {'INVALID_REQUEST'},
         {'outcome': 'withdrawn', 'supersedes_event_id': confirmed['result']['event_id'], 'returns_to': 'strategy'})
    deny('ceo', 'world_reconfirm_long_term_goal', gid, {'INVALID_REQUEST'}, {'returns_to': 'm1a'})
    check('returns_to_is_only_strategy_and_a_withdrawal_carries_none')

    act('a', 'world_commit_period_goal', fid)
    formed = act('ceo', 'world_confirm_period_goal', fid, {'outcome': 'accepted'})
    relations = {item['field']: item for item in view(fid)['business']['relations']}
    check('formation_the_first_period_forms_without_a_review_while_the_scope_has_no_confirmed_company_review',
          life(fid) == {'status': 'confirmed', 'display_name': '已确认', 'event_id': formed['event_id']}
          and relations['review_ref'] == {'field': 'review_ref', 'relation': 'based_on_review', 'value': None})

    # ================================================================ 再确认：不出修订、不带候选、只多一条事件
    before, revs, events_before = view(gid)['business'], revisions(gid), len(flow.events('outsider', gid)['events'])
    body = flow.prepare('ceo', flow.targeted('world_reconfirm_long_term_goal', gid,
                                             {'returns_to': 'strategy', 'content': {'text': '保持：三年目标仍然有效'}}))
    kept = flow.commit('ceo', body)
    row, after = event(kept['result']['event_id']), view(gid)
    check('reconfirming_a_long_term_goal_keeps_it_and_writes_one_gate_event_without_a_revision',
          after['business']['version'] == before['version'] and after['business']['revision_id'] == before['revision_id']
          and revisions(gid) == revs and len(flow.events('outsider', gid)['events']) == events_before + 1
          and after['records']['lifecycle'] == life(gid) == {'status': 'confirmed', 'display_name': '已确认',
                                                              'event_id': confirmed['result']['event_id']}
          and after['business']['formal'] == before['formal']
          and row['kind'] == 'reconfirm' and event_kinds['reconfirm']['class'] == 'gate' and row['outcome'] is None
          and row['detail'] == {'returns_to': 'strategy'} and row['content']['text'] == '保持：三年目标仍然有效'
          and str(row['principal_id']) == actor_id['ceo'] and row['action_type'] == 'world_reconfirm_long_term_goal'
          and str(row['subject_refs'][0]['revision_id']) == before['revision_id']
          and kinds_of(kept['receipt_id']) == ['reconfirm'] and kept['result']['version'] == before['version'])
    replay = flow.commit('ceo', body)
    check('replaying_a_reconfirmation_returns_the_original_receipt',
          replay['receipt_id'] == kept['receipt_id'] and kinds_of(kept['receipt_id']) == ['reconfirm'])

    before, revs = view(fid)['business'], revisions(fid)
    kept = act('ceo', 'world_reconfirm_period_goal', fid)
    row = event(kept['event_id'])
    check('reconfirming_a_period_goal_keeps_it_and_writes_one_gate_event_without_a_revision',
          view(fid)['business']['version'] == before['version'] and revisions(fid) == revs
          and life(fid) == {'status': 'confirmed', 'display_name': '已确认', 'event_id': formed['event_id']}
          and row['kind'] == 'reconfirm' and row['detail'] is None and row['outcome'] is None
          and str(row['subject_refs'][0]['revision_id']) == before['revision_id'])

    deny('a', 'world_reconfirm_period_goal', fid, {'FORBIDDEN'})
    deny('agent_ceo_a', 'world_reconfirm_period_goal', fid, {'FORBIDDEN'}, says='recorded by a person')
    check('only_the_ceo_in_person_reconfirms')
    deny('ceo', 'world_reconfirm_period_goal', fid, {'INVALID_REQUEST'}, {'payload': {'title': '再确认改标题'}})
    deny('ceo', 'world_reconfirm_long_term_goal', gid, {'INVALID_REQUEST'},
         {'outcome': 'withdrawn', 'supersedes_event_id': kept['event_id']})
    deny('ceo', 'world_reconfirm_period_goal', fid, {'INVALID_REQUEST'}, {'returns_to': 'strategy'})
    check('a_reconfirmation_carries_no_candidate_is_not_withdrawn_and_returns_to_is_only_on_a_long_term_goal')

    # ================================================================ 公司复盘：只赋效力
    # #79 起公司复盘多了整体经营状态、关键风险与问题（都可缺省）；复盘的 id 与形成锚定的守卫不变。
    blocks = {'overall_state': {'text': '整体经营达成九成'}, 'results': {'text': '收入达成九成'},
              'gaps': {'text': 'B 战场落后'}}
    review_old = snapshot('ceo', company['object_id'], 'company_review', 10, blocks)
    review1 = snapshot('ceo', company['object_id'], 'company_review', 20, blocks)
    review2 = snapshot('ceo', company['object_id'], 'company_review', 30, blocks)

    second = period_goal('12 月目标（收口）', '2031-12', goal, review1)
    sid = second['object_id']
    relations = {item['field']: item for item in view(sid)['business']['relations']}
    stored = flow.rows('SELECT payload FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s',
                       (scope, second['revision_id']))[0]['payload']['review_ref']
    check('a_period_goal_names_the_company_review_it_is_based_on_when_created',
          relations['review_ref']['relation'] == 'based_on_review'
          and relations['review_ref']['value']['ref'] == review1['ref']
          and stored == {'object_id': review1['object_id'], 'object_version': 1, 'revision_id': review1['revision_id'],
                         'block': None, 'component': None})
    goal_state = snapshot('a', fid, 'goal_state', 5, {'progress': {'text': '签了 2 家'}})
    for review_ref in (goal_state['ref'], company['ref'], review1['ref'] + '#results'):
        flow.deny_create('a', 'PeriodGoal', 'a', {'title': 'P', 'period': '2032-01', 'goal_ref': goal['ref'],
                                                 'review_ref': review_ref}, codes={'INVALID_REQUEST'})
    check('review_ref_points_to_a_company_review_snapshot_itself')
    deny('a', 'world_commit_period_goal', sid, {'INVALID_STATE'}, says='formation_anchors')
    check('formation_a_review_ref_to_an_unconfirmed_company_review_is_refused')

    snapshot_before = snapshot_read(review1['object_id'])
    done = review_act('ceo', review1, {'content': {'text': '九月复盘确认'}})
    row = event(done['result']['event_id'])
    company_view = view(company['object_id'])
    check('confirming_a_company_review_is_one_gate_event_pinning_the_snapshot_and_its_company',
          row['kind'] == 'review.confirmed' and event_kinds['review.confirmed']['class'] == 'gate'
          and row['outcome'] is None and row['action_type'] == REVIEW and str(row['principal_id']) == actor_id['ceo']
          and subjects(row) == [review1['object_id'], company['object_id']]
          and str(row['subject_refs'][0]['revision_id']) == review1['revision_id']
          and str(row['subject_refs'][1]['revision_id']) == company_view['business']['revision_id']
          and row['content']['text'] == '九月复盘确认' and kinds_of(done['receipt_id']) == ['review.confirmed']
          and done['result']['object_id'] == review1['object_id']
          and [item['object_id'] for item in done['result']['subject_refs']] == [company['object_id']])
    header, = listed(type='StateSnapshot', period=snapshot_before['period'])
    check('a_confirmed_company_review_only_gives_effect_changes_no_lifecycle_and_leaves_the_snapshot_as_it_was',
          done['object_versions'] == [] and company_view['records']['lifecycle'] is None
          and header['object_version'] == review1['target']['expected_version']
          and snapshot_read(review1['object_id']) == snapshot_before and snapshot_before['unconfirmed'] is True
          and life(fid)['event_id'] == formed['event_id'] and life(gid)['event_id'] == confirmed['result']['event_id'])
    check('records_give_the_confirmed_review_with_the_confirmation_event_and_the_snapshot',
          company_view['records']['confirmed_review'] == {
              'event_id': row['event_id'], 'ref': f"event:{row['event_id']}", 'confirmed_at': utc(row['recorded_at']),
              'principal': {'principal_id': actor_id['ceo'], 'principal_type': 'human',
                            'display_name': company_view['records']['confirmed_review']['principal']['display_name']},
              'on_behalf_of': None, 'snapshot': snapshot_before})
    review_blocks = {block['id']: block for block in snapshot_before['blocks']}
    check('a_confirmed_company_review_reads_back_its_new_blocks_and_gives_the_standard_sentence_for_those_left_out',
          list(review_blocks) == ['overall_state', 'results', 'gaps', 'causes', 'key_changes', 'implications',
                                  'key_risks', 'issues', 'materials']
          and review_blocks['overall_state']['text'] == '整体经营达成九成'
          and review_blocks['overall_state']['display_name'] == '整体经营状态'
          and review_blocks['gaps']['display_name'] == '关键结果差距'
          and (review_blocks['key_risks']['empty'], review_blocks['key_risks']['text']) == (True, '关键风险：暂无')
          and (review_blocks['issues']['empty'], review_blocks['issues']['text']) == (True, '问题：暂无'))

    deny_review('ceo', review1, {'INVALID_STATE'}, says='already confirmed')
    deny_review('ceo', review1, {'INVALID_STATE'}, {'outcome': 'withdrawn', 'supersedes_event_id': row['event_id']},
                says='not withdrawn')
    check('a_company_review_is_confirmed_once_and_its_confirmation_is_not_withdrawn')
    deny_review('a', review2, {'FORBIDDEN'})
    deny_review('agent_company', review2, {'FORBIDDEN'})
    check('only_the_ceo_confirms_a_company_review')
    deny('ceo', REVIEW, fid, {'ACTION_NOT_SUPPORTED_FOR_PROTOCOL'})
    goal_snapshot = snapshot('ceo', gid, 'goal_state', 6, {'progress': {'text': '按计划'}})
    unit_snapshot = snapshot('a', unit['object_id'], 'unit_state', 7, {'progress': {'text': '按计划'}})
    for other in (goal_snapshot, unit_snapshot):
        deny_review('ceo', other, {'INVALID_STATE'}, says='Only a company review')
    check('only_a_company_review_or_a_snapshot_of_a_period_goal_is_confirmed_as_a_review')

    act('ceo', REVIEW, review_old['object_id'], target=review_old['target'])
    check('with_several_confirmed_company_reviews_the_latest_as_of_wins_not_the_latest_confirmation',
          confirmed_review(company['object_id'])['snapshot']['object_id'] == review1['object_id'])

    act('a', 'world_commit_period_goal', sid)
    accepted = act('ceo', 'world_confirm_period_goal', sid, {'outcome': 'accepted'})
    check('formation_a_review_ref_to_a_confirmed_company_review_anchors_the_period_goal',
          life(sid) == {'status': 'confirmed', 'display_name': '已确认', 'event_id': accepted['event_id']})
    # 一轮重走的候选可以改指 review_ref（#69，补 45），形成锚定按候选判：改指还没确认的复盘被守卫拒。
    deny('a', 'world_commit_period_goal', sid, {'INVALID_STATE'},
         {'payload': {'review_ref': review2['ref'], 'blocks': logic_patch('gc-rl-s', '改')}},
         says='formation_anchors')
    check('a_re_run_candidate_re_pointing_review_ref_to_an_unconfirmed_review_is_refused_by_the_formation_guard')

    # 首期没带 review_ref 的周期目标（第一个周期形成的 fid）：scope 有了已确认的公司复盘之后，候选不给 review_ref 时
    # 守卫不成立；候选改指已确认的复盘则开轮，CEO 确认接受时写回，生命周期与推出它的事件不变。
    realization = logic_patch('gc-rl-f', '第二个月加一场客户复盘会')
    deny('a', 'world_commit_period_goal', fid, {'INVALID_STATE'},
         {'payload': {'blocks': realization}}, says='formation_anchors')
    check('a_first_period_goal_without_review_ref_opens_no_round_once_the_scope_has_a_confirmed_review_unless_it_names_one')
    before_view = view(fid)
    before = before_view['business']
    opened = act('a', 'world_commit_period_goal', fid,
                 {'payload': {'review_ref': review1['ref'], 'blocks': realization}})
    during = view(fid)
    rewritten = act('ceo', 'world_confirm_period_goal', fid, {'outcome': 'accepted'})
    after = view(fid)
    relations = {item['field']: item for item in after['business']['relations']}
    kept = {'status': 'confirmed', 'display_name': '已确认', 'event_id': formed['event_id']}
    check('a_first_period_goal_re_runs_with_a_candidate_re_pointing_review_ref_to_a_confirmed_review_and_it_is_written_back',
          during['records']['lifecycle'] == kept == after['records']['lifecycle']
          and during['business']['round']['opened_by_event_id'] == opened['event_id']
          and during['business']['round']['candidate']['review_ref'] == review1['ref']
          and during['business']['version'] == before['version'] and after['business']['round'] is None
          and relations['review_ref']['value']['ref'] == review1['ref']
          and logic(after) == ['第二个月加一场客户复盘会'] and logic(before_view) == []
          and rewritten['version'] == before['version'] + 1
          and after['business']['formal'] == {'lifecycle_status': 'confirmed',
                                              'effective_revision_id': rewritten['revision_id']})

    # 草稿期改指：scope 有了已确认的公司复盘后，不带 review_ref 的周期目标承诺被拒；改指未确认的仍被拒，改指已确认的放行。
    third = period_goal('1 月目标（收口）', '2032-01', goal)
    tid = third['object_id']
    deny('a', 'world_commit_period_goal', tid, {'INVALID_STATE'}, says='formation_anchors')
    check('formation_once_the_scope_has_a_confirmed_company_review_a_goal_without_review_ref_is_refused')
    flow.revise('a', tid, {'review_ref': review2['ref']})
    deny('a', 'world_commit_period_goal', tid, {'INVALID_STATE'}, says='formation_anchors')
    act('ceo', REVIEW, review2['object_id'], target=review2['target'])
    check('the_latest_as_of_confirmed_company_review_is_the_scopes_confirmed_review',
          confirmed_review(company['object_id'])['snapshot']['object_id'] == review2['object_id'])
    flow.revise('a', tid, {'review_ref': review_old['ref']})
    repointed = flow.revise('a', tid, {'review_ref': review2['ref']})['result']
    committed = act('a', 'world_commit_period_goal', tid)
    relations = {item['field']: item for item in view(tid)['business']['relations']}
    check('review_ref_is_re_pointed_while_the_goal_is_a_draft_and_the_confirmed_one_anchors_it',
          relations['review_ref']['value']['ref'] == review2['ref'] and repointed['version'] == third['version'] + 3
          and life(tid) == {'status': 'committed', 'display_name': '已承诺', 'event_id': committed['event_id']})
    deny_revise('a', tid, {'review_ref': review1['ref']}, {'INVALID_STATE'}, 'while it is a draft')
    check('review_ref_is_not_re_pointed_once_the_goal_is_committed')
    third_snapshot = snapshot('a', tid, 'goal_state', 8, {'progress': {'text': '刚承诺'}})
    deny_review('ceo', third_snapshot, {'INVALID_STATE'})
    check('a_period_goal_is_closed_by_review_only_once_it_is_confirmed')

    # ================================================================ 复盘确认关闭周期目标
    closing1 = snapshot('a', fid, 'goal_state', 40, {'progress': {'text': '期末：签了 3 家'}})
    closing2 = snapshot('a', fid, 'goal_state', 50, {'progress': {'text': '期末（修订）：签了 3 家'}})
    deny_review('agent_ceo_a', closing2, {'FORBIDDEN'}, says='recorded by a person')
    deny_review('a', closing2, {'FORBIDDEN'})
    check('only_the_ceo_in_person_confirms_a_period_goal_review')
    before = view(fid)['business']
    closed = review_act('ceo', closing2)
    row = event(closed['result']['event_id'])
    check('confirming_a_snapshot_of_a_period_goal_closes_it',
          life(fid) == {'status': 'closed', 'display_name': '已关闭', 'event_id': row['event_id']}
          and subjects(row) == [closing2['object_id'], fid]
          and str(row['subject_refs'][1]['revision_id']) == before['revision_id']
          and view(fid)['business']['version'] == before['version']
          and closed['object_versions'] == [{'object_id': fid, 'object_version': before['object_version'] + 1}]
          and confirmed_review(fid)['snapshot']['object_id'] == closing2['object_id']
          and confirmed_review(fid)['event_id'] == row['event_id'])
    check('the_review_confirmation_is_among_the_events_of_the_period_goal',
          any(item['event_id'] == row['event_id'] and item['action'] == REVIEW
              for item in flow.events('outsider', fid)['events']))

    deny_review('ceo', closing1, {'INVALID_STATE'})
    deny_review('ceo', closing1, {'INVALID_STATE'}, {'outcome': 'withdrawn', 'supersedes_event_id': row['event_id']},
                says='this snapshot')
    deny_review('a', closing2, {'FORBIDDEN'}, {'outcome': 'withdrawn', 'supersedes_event_id': row['event_id']})
    check('a_closed_goal_takes_no_other_review_and_only_the_ceo_withdraws_the_one_on_the_same_snapshot')
    withdrawn = act('ceo', REVIEW, closing2['object_id'],
                    {'outcome': 'withdrawn', 'supersedes_event_id': row['event_id']}, closing2['target'])
    read = {item['event_id']: item for item in flow.events('outsider', fid)['events']}
    check('withdrawing_a_period_goal_review_returns_the_goal_to_confirmed_and_the_review_is_no_longer_in_force',
          life(fid) == {'status': 'confirmed', 'display_name': '已确认', 'event_id': withdrawn['event_id']}
          and confirmed_review(fid) is None and read[row['event_id']]['withdrawn_by'] == [withdrawn['event_id']]
          and event(withdrawn['event_id'])['outcome'] == 'withdrawn')
    closed = act('ceo', REVIEW, closing1['object_id'], target=closing1['target'])
    check('the_goal_is_closed_again_by_the_other_snapshot_which_is_now_its_confirmed_review',
          life(fid)['event_id'] == closed['event_id']
          and confirmed_review(fid)['snapshot']['object_id'] == closing1['object_id'])
    for kind, params in (('world_reconfirm_period_goal', {}), ('world_cancel', {}), ('world_commit_period_goal', {}),
                         ('world_confirm_period_goal', {'outcome': 'accepted'})):
        deny('ceo', kind, fid, {'INVALID_STATE'}, params)
    deny_revise('a', fid, {'external_refs': [{'system': 'tianshu-60', 'id': 'closed'}]}, {'INVALID_STATE'}, 'not revised')
    deny_revise('a', fid, {'title': '关闭后改标题'}, {'INVALID_STATE'}, 'not revised')
    check('a_closed_period_goal_is_not_reconfirmed_cancelled_committed_confirmed_or_revised')

    # ================================================================ 取消（周期目标）与终止（长期目标）
    fourth = period_goal('2 月目标（收口）', '2032-02', goal, review2)
    for pg, state in ((fourth, 'draft'), (third, 'committed'), (second, 'confirmed')):
        oid = pg['object_id']
        assert life(oid)['status'] == state
        deny('a', 'world_cancel', oid, {'FORBIDDEN'}, says='parent')
        before = view(oid)['business']
        cancelled = act('ceo', 'world_cancel', oid, {'content': {'text': '不再需要'}})
        row = event(cancelled['event_id'])
        check(f'a_period_goal_is_cancelled_by_the_ceo_from_{state}',
              life(oid) == {'status': 'cancelled', 'display_name': '已取消', 'event_id': cancelled['event_id']}
              and row['kind'] == 'cancel' and event_kinds['cancel']['class'] == 'lifecycle'
              and view(oid)['business']['version'] == before['version'] and row['content']['text'] == '不再需要')
    deny('agent_a', 'world_cancel', sid, {'FORBIDDEN'}, says='Agent face')
    deny('ceo', 'world_commit_period_goal', fourth['object_id'], {'INVALID_STATE'})
    deny('a', 'world_commit_period_goal', fourth['object_id'], {'INVALID_STATE'})
    deny('ceo', 'world_confirm_period_goal', third['object_id'], {'INVALID_STATE'}, {'outcome': 'accepted'})
    deny('ceo', 'world_reconfirm_period_goal', sid, {'INVALID_STATE'})
    deny('ceo', 'world_cancel', sid, {'INVALID_STATE'})
    deny_revise('a', fourth['object_id'], {'title': '取消后改标题'}, {'INVALID_STATE'}, 'not revised')
    check('a_cancelled_period_goal_is_not_committed_confirmed_reconfirmed_cancelled_or_revised_again')
    cancel_event = life(sid)['event_id']
    back = act('ceo', 'world_cancel', sid, {'outcome': 'withdrawn', 'supersedes_event_id': cancel_event})
    check('withdrawing_the_cancellation_returns_the_goal_to_where_it_was',
          life(sid) == {'status': 'confirmed', 'display_name': '已确认', 'event_id': back['event_id']})

    ended_draft = ltg('战场 A 旧目标（收口，草稿终止）')
    act('ceo', 'world_cancel', ended_draft['object_id'])
    check('a_draft_long_term_goal_is_terminated_by_the_ceo',
          life(ended_draft['object_id'])['status'] == 'terminated'
          and life(ended_draft['object_id'])['display_name'] == '已终止')
    ended = ltg('战场 A 过期目标（收口）')
    eid = ended['object_id']
    act('ceo', 'world_confirm_long_term_goal', eid, {'outcome': 'accepted'})
    hanging = period_goal('3 月目标（收口）', '2032-03', ended, review2)
    pending = period_goal('4 月目标（收口）', '2032-04', ended, review2)
    act('a', 'world_commit_period_goal', hanging['object_id'])
    deny('a', 'world_cancel', eid, {'FORBIDDEN'}, says='self')
    deny('agent_a', 'world_cancel', eid, {'FORBIDDEN'})
    terminated = act('ceo', 'world_cancel', eid, {'content': {'text': '不再有效'}})
    check('a_confirmed_long_term_goal_is_terminated_by_the_ceo_with_a_cancel_event',
          life(eid) == {'status': 'terminated', 'display_name': '已终止', 'event_id': terminated['event_id']}
          and event(terminated['event_id'])['kind'] == 'cancel' and view(eid)['business']['formal']['lifecycle_status']
          == 'confirmed')
    deny('ceo', 'world_reconfirm_long_term_goal', eid, {'INVALID_STATE'})
    deny('ceo', 'world_confirm_long_term_goal', eid, {'INVALID_STATE'}, {'outcome': 'accepted', 'payload': {'horizon': '2030'}})
    deny('ceo', 'world_cancel', eid, {'INVALID_STATE'})
    deny_revise('ceo', eid, {'external_refs': [{'system': 'tianshu-60', 'id': 'terminated'}]}, {'INVALID_STATE'},
                'not revised')
    check('a_terminated_long_term_goal_is_not_reconfirmed_confirmed_cancelled_or_revised')
    deny('a', 'world_commit_period_goal', pending['object_id'], {'INVALID_STATE'}, says='formation_anchors')
    deny('ceo', 'world_confirm_period_goal', hanging['object_id'], {'INVALID_STATE'}, {'outcome': 'accepted'},
         says='formation_anchors')
    returned = act('ceo', 'world_confirm_period_goal', hanging['object_id'], {'outcome': 'returned'})
    check('a_period_goal_hanging_on_a_terminated_long_term_goal_is_not_committed_or_confirmed_and_a_return_is_not_guarded',
          life(hanging['object_id']) == {'status': 'draft', 'display_name': '草稿', 'event_id': returned['event_id']})

    # ================================================================ 代记（门族与生命周期族）
    grant = flow.act('ceo', 'world_grant_delegation', {
        'delegate_principal_id': actor_id['tianshu'], 'families': ['gate', 'lifecycle'],
        'domain_ids': [f['domains']['company'], f['domains']['a']],
        'valid_until': utc(datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=1))})['result']
    grants = {str(item['event_id']) for item in flow.rows(
        """SELECT event_id FROM gov_world_events WHERE scope_id=%s AND kind='delegation.granted'
             AND principal_id=%s AND detail->>'delegate_principal_id'=%s""", (scope, actor_id['ceo'], actor_id['tianshu']))}

    def on_behalf(kind, oid, params=None, target=None):
        sent = {'principal_id': actor_id['ceo'], 'external_record_id': f'tianshu:{kind}:{uid()[:8]}',
                'external_confirmed_at': utc(datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=5))}
        result = act('tianshu', kind, oid, {**(params or {}), 'on_behalf_of': sent}, target)
        row = event(result['event_id'])
        written = (str(row['principal_id']) == actor_id['tianshu'] and str(row['on_behalf_of']) == actor_id['ceo']
                   and row['external_record_id'] == sent['external_record_id']
                   and utc(row['external_confirmed_at']) == sent['external_confirmed_at']
                   and result['on_behalf_of']['principal_id'] == actor_id['ceo']
                   and result['on_behalf_of']['delegation_event_id'] in grants and grant['event_id'] in grants)
        return result, row, written

    result, row, written = on_behalf('world_reconfirm_long_term_goal', gid, {'returns_to': 'strategy'})
    check('the_long_term_goal_reconfirmation_is_recorded_on_behalf_of_the_ceo',
          written and row['kind'] == 'reconfirm' and row['detail'] == {'returns_to': 'strategy'}
          and life(gid)['event_id'] == confirmed['result']['event_id'])
    result, row, written = on_behalf('world_reconfirm_period_goal', sid)
    check('the_period_goal_reconfirmation_is_recorded_on_behalf_of_the_ceo',
          written and row['kind'] == 'reconfirm' and life(sid)['event_id'] == back['event_id'])
    review3 = snapshot('ceo', company['object_id'], 'company_review', 60, blocks)
    result, row, written = on_behalf(REVIEW, review3['object_id'], target=review3['target'])
    cr = confirmed_review(company['object_id'])
    check('the_company_review_confirmation_is_recorded_on_behalf_of_the_ceo',
          written and row['kind'] == 'review.confirmed' and cr['event_id'] == row['event_id']
          and cr['principal']['principal_id'] == actor_id['tianshu']
          and cr['on_behalf_of']['principal_id'] == actor_id['ceo'])
    closing = snapshot('a', sid, 'goal_state', 70, {'progress': {'text': '期末'}})
    result, row, written = on_behalf(REVIEW, closing['object_id'], target=closing['target'])
    check('the_period_goal_review_confirmation_is_recorded_on_behalf_of_the_ceo_and_closes_it',
          written and life(sid) == {'status': 'closed', 'display_name': '已关闭', 'event_id': row['event_id']})
    fifth = period_goal('5 月目标（收口）', '2032-05', goal, review3)
    retired = ltg('战场 A 备选目标（收口）')
    result, row, written = on_behalf('world_cancel', fifth['object_id'])
    result2, row2, written2 = on_behalf('world_cancel', retired['object_id'])
    check('cancelling_a_period_goal_and_terminating_a_long_term_goal_are_recorded_on_behalf_of_the_ceo',
          written and written2 and life(fifth['object_id'])['status'] == 'cancelled'
          and life(retired['object_id'])['status'] == 'terminated' and row['kind'] == row2['kind'] == 'cancel')
    deny('tianshu', 'world_reconfirm_long_term_goal', gid, {'FORBIDDEN'}, {'on_behalf_of': {
        'principal_id': actor_id['a'], 'external_record_id': 'tianshu:no-delegation',
        'external_confirmed_at': utc(datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=5))}},
        says='No delegation in force')
    check('recording_on_behalf_of_someone_without_a_delegation_is_refused')

    # 复盘确认的重放返回原回执、只有一条事件。
    review4 = snapshot('ceo', company['object_id'], 'company_review', 80, blocks)
    body = flow.prepare('ceo', flow.targeted(REVIEW, review4['object_id'], {}, review4['target']))
    first_commit = flow.commit('ceo', body)
    check('replaying_a_review_confirmation_returns_the_original_receipt',
          flow.commit('ceo', body)['receipt_id'] == first_commit['receipt_id']
          and kinds_of(first_commit['receipt_id']) == ['review.confirmed'])

    # ================================================================ 一轮进行中关闭或取消（#69，补 47）
    # 进入已关闭、已取消时进行中的一轮作废，同退回：候选不写回、正式内容不变；撤回那条事件，这一轮连同候选原样回来
    # （同撤回一条退回），CEO 确认接受照常写回；再取消又作废。
    def formed_goal(title, period):
        oid = period_goal(title, period, goal, review1)['object_id']
        act('a', 'world_commit_period_goal', oid)
        act('ceo', 'world_confirm_period_goal', oid, {'outcome': 'accepted'})
        return oid

    def voided(oid, formed_view, stage):
        after = view(oid)
        return (after['records']['lifecycle'] == stage and after['business']['round'] is None
                and after['business']['version'] == formed_view['business']['version']
                and after['business']['formal'] == formed_view['business']['formal']
                and logic(after) == logic(formed_view) == [])

    def restored(oid, opened, candidate, stage):
        now = view(oid)
        return (now['records']['lifecycle'] == stage and now['business']['round']['opened_by_event_id'] == opened
                and now['business']['round']['candidate'] == candidate)

    closing_goal = formed_goal('6 月目标（一轮中复盘关闭）', '2032-06')
    formed_view = view(closing_goal)
    candidate = {'blocks': logic_patch('gc-rl-6', '候选：月中加一场复盘会')}
    opened = act('a', 'world_commit_period_goal', closing_goal, {'payload': candidate})
    end = snapshot('a', closing_goal, 'goal_state', 90, {'progress': {'text': '期末：达成'}})
    closed = review_act('ceo', end)['result']
    deny('ceo', 'world_confirm_period_goal', closing_goal, {'INVALID_STATE'}, {'outcome': 'accepted'})
    check('period_goal_a_round_open_when_it_is_closed_by_review_is_void_and_nothing_is_written_back',
          voided(closing_goal, formed_view, {'status': 'closed', 'display_name': '已关闭', 'event_id': closed['event_id']}))
    back = act('ceo', REVIEW, end['object_id'], {'outcome': 'withdrawn', 'supersedes_event_id': closed['event_id']},
               end['target'])
    was_restored = restored(closing_goal, opened['event_id'], candidate,
                            {'status': 'confirmed', 'display_name': '已确认', 'event_id': back['event_id']})
    written = act('ceo', 'world_confirm_period_goal', closing_goal, {'outcome': 'accepted'})
    check('period_goal_withdrawing_the_closing_review_restores_the_round_which_is_then_written_back',
          was_restored and view(closing_goal)['business']['round'] is None
          and logic(view(closing_goal)) == ['候选：月中加一场复盘会']
          and written['version'] == formed_view['business']['version'] + 1
          and life(closing_goal) == {'status': 'confirmed', 'display_name': '已确认', 'event_id': back['event_id']})

    dropped_goal = formed_goal('7 月目标（一轮中取消）', '2032-07')
    formed_view = view(dropped_goal)
    candidate = {'blocks': logic_patch('gc-rl-7', '候选：换一个渠道')}
    opened = act('a', 'world_commit_period_goal', dropped_goal, {'payload': candidate})
    cancelled = act('ceo', 'world_cancel', dropped_goal)
    check('period_goal_a_round_open_when_it_is_cancelled_is_void_and_nothing_is_written_back',
          voided(dropped_goal, formed_view,
                 {'status': 'cancelled', 'display_name': '已取消', 'event_id': cancelled['event_id']}))
    back = act('ceo', 'world_cancel', dropped_goal, {'outcome': 'withdrawn', 'supersedes_event_id': cancelled['event_id']})
    was_restored = restored(dropped_goal, opened['event_id'], candidate,
                            {'status': 'confirmed', 'display_name': '已确认', 'event_id': back['event_id']})
    again = act('ceo', 'world_cancel', dropped_goal)
    deny('ceo', 'world_confirm_period_goal', dropped_goal, {'INVALID_STATE'}, {'outcome': 'accepted'})
    check('period_goal_withdrawing_the_cancellation_restores_the_round_and_cancelling_again_voids_it',
          was_restored and voided(dropped_goal, formed_view,
                                  {'status': 'cancelled', 'display_name': '已取消', 'event_id': again['event_id']}))
