"""tkos.world/0.2 独立验收的 Strategy 门：指定本轮、Agreement、确认生效与再确认（票 #59，契约第 9、10.1、11、12、14 节，
决 13）。

放在列对象之后、撤销 CEO 指派之前跑，用主干上的 Strategy：它此前一直是草稿，责任单元 a 的架构引用钉在它的版本 1，
其下挂着前面各场景建的长期目标、周期目标、Mission、Task 与 Activity。先在草稿里走一轮到已生效，再在已生效时带候选
开轮、写回新修订，核对下游对象仍钉旧版本、生命周期与修订都没动；然后走结论为不改的一轮（再确认结束）、被退回的一轮，
最后由天枢代一位被指定的人记 Agreement。被指定的人是单元 a、b、c 的 DRI，他们在 Strategy 所在的公司域不持任何角色，
Agreement 照样能记（决 13）；公司域里持 IC 的人策略给了角色，没被指定照样不能记。拒绝都在 prepare 与 commit 两个入口
核对库快照不变。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from urllib.parse import urlencode

from acceptance.method_independent.fixture import uid
from .fixture import REGISTRY

V02 = 'tkos.world/0.2'
EVENT_KINDS = {item['kind']: item for item in json.loads(REGISTRY.read_text())['event_kinds']}


def utc_now(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime('%Y-%m-%dT%H:%M:%SZ')


def strategy_gates(book, h, f, flow, trunk):
    check = book.check
    made = trunk['made']
    scope = f['scope_id']
    actor_id = {name: actor['principal_id'] for name, actor in f['actors'].items()}
    a, b, c = actor_id['a'], actor_id['b'], actor_id['c']
    sid = made['Strategy']['object_id']

    def act(actor, kind, oid, params=None):
        return flow.commit(actor, flow.prepare(actor, flow.targeted(kind, oid, params or {})))['result']

    def deny(actor, kind, oid, codes, params=None, says=None):
        flow.deny(actor, flow.targeted(kind, oid, params or {}), codes=codes, says=says)

    def designate(*people, payload=None, actor='ceo'):
        params = {'principal_ids': [actor_id[p] for p in people], **({'payload': payload} if payload else {})}
        return act(actor, 'world_assign_strategy_round', sid, params)

    def deny_designate(codes, principal_ids, actor='ceo', says=None, **params):
        deny(actor, 'world_assign_strategy_round', sid, codes, {'principal_ids': principal_ids, **params}, says=says)

    def agree(actor, **params):
        return act(actor, 'world_agree_strategy', sid, params)

    def view():
        return flow.read('outsider', sid)

    def life():
        return view()['records']['lifecycle']

    def current_round():
        return view()['business']['round']

    def event(event_id):
        return flow.rows('SELECT e.*, r.action_type FROM gov_world_events e JOIN gov_action_receipts r '
                         'ON r.scope_id=e.scope_id AND r.receipt_id=e.action_id WHERE e.scope_id=%s AND e.event_id=%s',
                         (scope, event_id))[0]

    def events_of(receipt_id):
        return flow.rows('SELECT event_id FROM gov_world_events WHERE scope_id=%s AND action_id=%s', (scope, receipt_id))

    def agreed_to(result):
        """一条 Agreement 的 detail 里所同意的内容：本轮、修订、候选、记下时是否补齐。"""
        return event(result['event_id'])['detail']

    def used_assignment(person):
        """判权只要求在 scope 内有生效指派：回执记下这个人当前按 id 排在最前的那条指派。"""
        return [min(str(row['assignment_id']) for row in flow.rows(
            'SELECT assignment_id FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s AND active',
            (scope, person)))]

    def withdraw(actor, original):
        return agree(actor, outcome='withdrawn', supersedes_event_id=original['event_id'])

    def block_text(read, block):
        return next(item for item in read['business']['blocks'] if item['id'] == block)['text']

    # ================================================================ 草稿：没有一轮时
    start = view()
    base = {key: start['business'][key] for key in ('version', 'revision_id')}
    check('strategy_the_trunk_strategy_is_a_draft_without_formal_content_or_a_round',
          start['records']['lifecycle']['status'] == 'draft'
          and start['business']['formal'] == {'lifecycle_status': 'draft', 'effective_revision_id': None}
          and start['business']['round'] is None)
    deny('a', 'world_agree_strategy', sid, {'INVALID_STATE'}, says='round_')
    deny('ceo', 'world_confirm_strategy', sid, {'INVALID_STATE'}, {'outcome': 'accepted'})
    deny('ceo', 'world_reconfirm_strategy', sid, {'INVALID_STATE'})
    check('strategy_without_a_round_nothing_is_agreed_confirmed_or_reconfirmed')

    # 指定本轮：至少一人、各不相同、scope 内有效的人；只由 Strategy 的责任人（CEO，人）记；草稿里不带候选。
    deny_designate({'INVALID_REQUEST'}, [])
    deny_designate({'INVALID_REQUEST'}, [a, a])
    deny_designate({'INVALID_REQUEST'}, [a, f['bystander_principal_id']], says='active person')  # 没有任何指派
    deny_designate({'INVALID_REQUEST'}, [actor_id['agent_a']], says='active person')              # Agent 不是人
    deny_designate({'INVALID_REQUEST'}, [actor_id['foreign_ceo']], says='active person')          # 另一 scope
    deny_designate({'INVALID_REQUEST'}, [uid()], says='active person')
    check('strategy_a_round_designates_at_least_one_distinct_active_person_of_the_scope')
    deny_designate({'FORBIDDEN'}, [a], actor='unrelated', says='recorded by self')  # 公司域的 IC：策略放行，不是责任人
    deny_designate({'FORBIDDEN'}, [a], actor='a')                                  # 公司域没有角色
    deny_designate({'FORBIDDEN'}, [a], actor='agent_company', says='by a person')  # 公司域持 AGENT 的 Agent
    check('strategy_only_the_strategys_ceo_in_person_designates_a_round')
    deny_designate({'INVALID_STATE'}, [a], payload={'blocks': {'strategy_core': {'text': '草稿里的候选'}}}, says='candidate')
    check('strategy_a_draft_round_carries_no_candidate')

    # ================================================================ 草稿：指定两人、Agreement、补齐
    r1 = designate('a', 'b')
    row = event(r1['event_id'])
    read = view()
    check('strategy_designating_records_one_assign_event_with_the_designated_people_and_no_new_revision',
          row['kind'] == 'assign' and EVENT_KINDS['assign']['class'] == 'record'
          and row['action_type'] == 'world_assign_strategy_round' and row['contract_version'] == V02
          and row['detail'] == {'principal_ids': [a, b]} and str(row['principal_id']) == actor_id['ceo']
          and row['outcome'] is None and row['subject_refs'][0]['revision_id'] == base['revision_id']
          and r1['version'] == base['version'] and read['business']['revision_id'] == base['revision_id']
          and len(events_of(flow.receipts[-1]['receipt_id'])) == 1)
    check('strategy_a_draft_round_keeps_the_stage_and_is_read_back_with_the_designated_people',
          read['records']['lifecycle'] == start['records']['lifecycle']
          and read['business']['round'] == {
              'opened_by_event_id': r1['event_id'], 'stage': 'draft', 'display_name': '草稿',
              'candidate_event_id': None, 'candidate': None, 'designated': [a, b], 'agreements': [],
              'pending': [a, b]})

    deny('unrelated', 'world_agree_strategy', sid, {'FORBIDDEN'}, says='recorded by designated')
    deny('c', 'world_agree_strategy', sid, {'FORBIDDEN'}, says='recorded by designated')
    deny('ceo', 'world_agree_strategy', sid, {'FORBIDDEN'}, says='recorded by designated')
    deny('agent', 'world_agree_strategy', sid, {'FORBIDDEN'}, says='by a person')  # 单元 a 持 DRI 角色的 Agent
    check('strategy_only_a_person_designated_for_the_round_records_an_agreement_whatever_roles_the_policy_lists')

    # 单元 a 的 DRI 在公司域没有任何角色：Agreement 不按策略的角色表判（决 13），同键重放也照样成立。
    body = flow.prepare('a', flow.targeted('world_agree_strategy', sid,
                                           {'content': {'text': '同意：两个战场的取舍成立。'}}))
    receipt = flow.commit('a', body)
    first = receipt['result']
    replay = flow.commit('a', deepcopy(body))
    row = event(first['event_id'])
    in_company = flow.rows('SELECT count(*) AS n FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s '
                           'AND domain_id=%s', (scope, a, f['domains']['company']))[0]['n']
    check('strategy_a_designated_unit_dri_without_a_role_in_the_company_domain_records_an_agreement',
          in_company == 0 and row['kind'] == 'agreement' and EVENT_KINDS['agreement']['class'] == 'gate'
          and row['action_type'] == 'world_agree_strategy' and str(row['principal_id']) == a
          and row['on_behalf_of'] is None and row['outcome'] is None and row['content']['text'] == '同意：两个战场的取舍成立。'
          and row['subject_refs'][0]['revision_id'] == base['revision_id']
          and row['detail'] == {'round_event_id': r1['event_id'], 'object_version': base['version'],
                                'revision_id': base['revision_id'], 'candidate_event_id': None, 'round_complete': False}
          and first['required_assignment_ids'] == used_assignment(a)
          and life() == start['records']['lifecycle'])
    check('strategy_replaying_an_agreement_returns_the_original_receipt_and_writes_nothing_more',
          replay['receipt_id'] == receipt['receipt_id'] and len(events_of(receipt['receipt_id'])) == 1)
    deny('a', 'world_agree_strategy', sid, {'INVALID_STATE'}, says='already agreed')
    check('strategy_the_same_person_agrees_to_the_same_content_once')

    # 补齐之前草稿又被修订：钉住旧修订的 Agreement 不计。
    revised = flow.revise('ceo', sid, {'blocks': {'strategy_core': {'text': '聚焦企业经营系统，先做两个战场。'}}})['result']
    second = agree('b')
    read = view()
    check('strategy_an_agreement_on_an_earlier_revision_does_not_count_once_the_draft_is_revised',
          read['records']['lifecycle'] == start['records']['lifecycle']
          and agreed_to(second) == {'round_event_id': r1['event_id'], 'object_version': revised['version'],
                                    'revision_id': revised['revision_id'], 'candidate_event_id': None,
                                    'round_complete': False}
          and [item['event_id'] for item in read['business']['round']['agreements']] == [first['event_id'],
                                                                                          second['event_id']]
          and read['business']['round']['pending'] == [a])
    third = agree('a')
    check('strategy_the_agreement_completing_the_round_on_the_latest_revision_reaches_agreed',
          life() == {'status': 'agreed', 'display_name': '已达成判断', 'event_id': third['event_id']}
          and agreed_to(third)['round_complete'] is True and agreed_to(third)['revision_id'] == revised['revision_id']
          and view()['business']['formal'] == {'lifecycle_status': 'draft', 'effective_revision_id': None}
          and current_round()['stage'] == 'agreed' and current_round()['pending'] == [])

    # 已达成判断：不能再指定、不能直接改正式内容、不能再记 Agreement，也不能再确认。
    deny_designate({'INVALID_STATE'}, [a, b])
    deny('ceo', 'world_revise_object', sid, {'INVALID_STATE'}, {'payload': {'title': '直接改'}})
    deny('b', 'world_agree_strategy', sid, {'INVALID_STATE'})
    deny('ceo', 'world_reconfirm_strategy', sid, {'INVALID_STATE'})
    check('strategy_once_agreed_there_is_no_new_designation_formal_revision_further_agreement_or_reconfirmation')

    # 撤回：只撤推出当前状态的那条，由记它的人本人；撤回后这一条不再算，本人可以再记。
    deny('b', 'world_agree_strategy', sid, {'INVALID_STATE'},
         {'outcome': 'withdrawn', 'supersedes_event_id': second['event_id']})
    deny('b', 'world_agree_strategy', sid, {'FORBIDDEN'},
         {'outcome': 'withdrawn', 'supersedes_event_id': third['event_id']})
    withdrawn = withdraw('a', third)
    row = event(withdrawn['event_id'])
    check('strategy_the_agreement_that_completed_the_round_is_withdrawn_by_its_recorder_back_to_draft',
          life() == {'status': 'draft', 'display_name': '草稿', 'event_id': withdrawn['event_id']}
          and row['outcome'] == 'withdrawn' and str(row['supersedes_event_id']) == third['event_id']
          and row['detail'] is None and current_round()['pending'] == [a]
          and event(third['event_id'])['kind'] == 'agreement')
    again = agree('a')
    check('strategy_after_withdrawing_the_person_agrees_again_and_the_round_is_agreed',
          life() == {'status': 'agreed', 'display_name': '已达成判断', 'event_id': again['event_id']})

    # CEO 退回：本轮作废，要再指定才能再记 Agreement。
    deny('unrelated', 'world_confirm_strategy', sid, {'FORBIDDEN'}, {'outcome': 'returned'})
    deny('a', 'world_confirm_strategy', sid, {'FORBIDDEN'}, {'outcome': 'returned'})
    returned = act('ceo', 'world_confirm_strategy', sid, {'outcome': 'returned', 'content': {'text': '假设还不成立'}})
    check('strategy_returning_the_agreed_draft_ends_the_round',
          life() == {'status': 'draft', 'display_name': '草稿', 'event_id': returned['event_id']}
          and event(returned['event_id'])['outcome'] == 'returned' and current_round() is None)
    deny('a', 'world_agree_strategy', sid, {'INVALID_STATE'})
    check('strategy_after_a_return_an_agreement_needs_a_new_designation')

    # 重新指定即开新一轮，此前的 Agreement 作废。
    r2 = designate('a', 'b')
    agree('a')
    r3 = designate('a', 'c')
    deny('b', 'world_agree_strategy', sid, {'FORBIDDEN'}, says='recorded by designated')
    by_c = agree('c')
    check('strategy_designating_again_opens_a_new_round_and_the_earlier_agreements_are_void',
          r3['event_id'] != r2['event_id'] and life()['status'] == 'draft'
          and agreed_to(by_c)['round_event_id'] == r3['event_id'] and agreed_to(by_c)['round_complete'] is False
          and current_round()['designated'] == [a, c] and current_round()['pending'] == [a]
          and [item['event_id'] for item in current_round()['agreements']] == [by_c['event_id']])
    agree('a')
    agreed = view()
    confirmed = act('ceo', 'world_confirm_strategy', sid, {'outcome': 'accepted'})
    read = view()
    check('strategy_the_ceo_confirms_the_agreed_draft_and_the_strategy_becomes_effective_without_a_new_revision',
          agreed['records']['lifecycle']['status'] == 'agreed'
          and read['records']['lifecycle'] == {'status': 'effective', 'display_name': '已生效',
                                               'event_id': confirmed['event_id']}
          and read['business']['formal'] == {'lifecycle_status': 'confirmed',
                                             'effective_revision_id': agreed['business']['revision_id']}
          and confirmed['version'] == agreed['business']['version'] == read['business']['version']
          and event(confirmed['event_id'])['kind'] == 'confirm' and read['business']['round'] is None)
    formal_confirmation = confirmed

    # ================================================================ 已生效：修订规则与再确认
    deny('ceo', 'world_revise_object', sid, {'INVALID_STATE'}, {'payload': {'blocks': {'business_logic': {'text': '直接改'}}}},
         says='re-run of the gate')
    refs = flow.revise('ceo', sid, {'external_refs': [{'system': 'tianshu-59', 'id': 'strategy-2027'}]})['result']
    read = view()
    check('strategy_effective_formal_content_changes_only_through_a_round_and_external_refs_are_revised_directly',
          read['business']['version'] == refs['version'] == confirmed['version'] + 1
          and read['business']['formal']['effective_revision_id'] == refs['revision_id']
          and read['records']['lifecycle']['event_id'] == confirmed['event_id'])
    deny('ceo', 'world_confirm_strategy', sid, {'INVALID_STATE'}, {'outcome': 'accepted'}, says='No re-run')
    deny('a', 'world_agree_strategy', sid, {'INVALID_STATE'})
    check('strategy_effective_without_a_round_nothing_is_confirmed_or_agreed')
    kept = act('ceo', 'world_reconfirm_strategy', sid, {'content': {'text': '本季度判断：继续有效。'}})
    row = event(kept['event_id'])
    after = view()
    check('strategy_reconfirming_records_one_gate_event_without_a_new_revision_and_keeps_the_state',
          row['kind'] == 'reconfirm' and EVENT_KINDS['reconfirm']['class'] == 'gate' and row['outcome'] is None
          and row['action_type'] == 'world_reconfirm_strategy' and row['content']['text'] == '本季度判断：继续有效。'
          and row['subject_refs'][0]['revision_id'] == refs['revision_id']
          and (kept['version'], after['business']['revision_id']) == (refs['version'], refs['revision_id'])
          and after['records']['lifecycle'] == read['records']['lifecycle']
          and after['business']['formal'] == read['business']['formal'])
    deny('ceo', 'world_reconfirm_strategy', sid, {'INVALID_REQUEST'},
         {'outcome': 'withdrawn', 'supersedes_event_id': kept['event_id']})
    deny('a', 'world_reconfirm_strategy', sid, {'FORBIDDEN'})
    check('strategy_a_reconfirmation_is_recorded_by_the_ceo_and_is_not_withdrawn')

    # ================================================================ 已生效：带候选开轮、写回、下游不动
    deny_designate({'INVALID_REQUEST'}, [a], payload={'external_refs': []}, says='only formal')
    check('strategy_a_rounds_candidate_carries_only_formal_content')
    first_candidate = {'blocks': {'strategy_core': {'text': '候选一：只做一个战场。'}}}
    r4 = designate('a', 'b', payload=first_candidate)
    read = view()
    check('strategy_designating_with_a_candidate_opens_a_round_on_the_effective_strategy_and_keeps_its_state',
          read['records']['lifecycle'] == after['records']['lifecycle']
          and read['business']['version'] == refs['version']
          and event(r4['event_id'])['detail'] == {'principal_ids': [a, b], 'candidate': first_candidate}
          and read['business']['round'] == {
              'opened_by_event_id': r4['event_id'], 'stage': 'draft', 'display_name': '草稿',
              'candidate_event_id': r4['event_id'], 'candidate': first_candidate, 'designated': [a, b],
              'agreements': [], 'pending': [a, b]})
    on_first = agree('a')
    check('strategy_an_effective_rounds_agreement_pins_the_round_and_its_candidate',
          agreed_to(on_first) == {'round_event_id': r4['event_id'], 'object_version': refs['version'],
                                  'revision_id': refs['revision_id'], 'candidate_event_id': r4['event_id'],
                                  'round_complete': False})
    candidate = {'title': '2027 战略', 'blocks': {
        'strategy_core': {'text': '聚焦企业经营系统，三个战场。'},
        'responsibility_structure': {'components': [{'id': 'unit-c', 'type': 'unit_entry', 'text': '战场 C'}]}}}
    r5 = designate('a', 'b', payload=candidate)
    check('strategy_designating_again_before_the_round_is_agreed_opens_a_new_round_with_its_own_candidate',
          current_round()['opened_by_event_id'] == r5['event_id'] and current_round()['agreements'] == []
          and current_round()['candidate'] == candidate)
    agree('a')
    deny('a', 'world_agree_strategy', sid, {'INVALID_STATE'}, says='already agreed')
    agree('b')
    check('strategy_an_effective_round_is_agreed_in_its_own_stage_while_the_strategy_stays_effective',
          life() == after['records']['lifecycle'] and current_round()['stage'] == 'agreed'
          and current_round()['pending'] == [])
    deny_designate({'INVALID_STATE'}, [a], payload=first_candidate)
    check('strategy_an_agreed_round_is_not_designated_again')

    def headers():
        """scope 里全部对象的对象头（列对象按页取完），按 id。"""
        found, cursor = {}, None
        while True:
            query = {'limit': 100, **({'cursor': cursor} if cursor else {})}
            page = flow.clients['outsider'].json('GET', '/v1/world/objects?' + urlencode(query))
            found.update({item['object_id']: item for item in page['items']})
            cursor = page['next_cursor']
            if cursor is None:
                return found

    def event_count():
        return flow.rows('SELECT count(*) AS n FROM gov_world_events WHERE scope_id=%s', (scope,))[0]['n']

    unit = made['ResponsibilityUnit']['object_id']
    before_headers, before_events = headers(), event_count()
    architecture = flow.read('outsider', unit)['business']['relations'][0]['value']
    written = act('ceo', 'world_confirm_strategy', sid, {'outcome': 'accepted'})
    read = view()
    structure = next(item for item in read['business']['blocks'] if item['id'] == 'responsibility_structure')
    ledger = {item['id']: item for item in read['business']['component_ledger']}
    check('strategy_confirming_the_agreed_round_writes_the_candidate_back_as_a_new_effective_revision',
          written['version'] == refs['version'] + 1 == read['business']['version']
          and read['business']['title'] == '2027 战略' and block_text(read, 'strategy_core') == '聚焦企业经营系统，三个战场。'
          and [item['id'] for item in structure['components']][-1] == 'unit-c'
          and ledger['unit-c']['added_in_version'] == written['version']
          and read['business']['attributes']['external_refs'] == [
              {'system': 'tianshu-59', 'id': 'strategy-2027', 'url': None}]
          and read['business']['formal'] == {'lifecycle_status': 'confirmed',
                                             'effective_revision_id': written['revision_id']}
          and read['records']['lifecycle'] == after['records']['lifecycle'] and read['business']['round'] is None
          and event(written['event_id'])['subject_refs'][0]['revision_id'] == written['revision_id'])
    after_headers = headers()
    unit_view = flow.read('outsider', unit)
    check('strategy_a_new_version_takes_effect_without_reopening_downstream_which_stays_pinned_to_the_old_version',
          event_count() == before_events + 1
          and unit_view['business']['relations'][0]['value'] == architecture
          and (architecture['object_version'], architecture['revision_id']) == (1, made['Strategy']['revision_id'])
          and set(after_headers) == set(before_headers) and len(before_headers) > 20
          and all(after_headers[oid] == header for oid, header in before_headers.items() if oid != sid)
          and after_headers[sid]['version'] == written['version'])
    deny('ceo', 'world_confirm_strategy', sid, {'INVALID_STATE'},
         {'outcome': 'withdrawn', 'supersedes_event_id': formal_confirmation['event_id']}, says='rewritten')
    check('strategy_once_a_round_wrote_back_the_confirmation_that_made_it_effective_cannot_be_withdrawn')

    # ================================================================ 已生效：结论为不改、被退回的一轮
    r6 = designate('a')
    agree('a')
    deny('ceo', 'world_confirm_strategy', sid, {'INVALID_STATE'}, {'outcome': 'accepted'}, says='reconfirmation')
    ended = act('ceo', 'world_reconfirm_strategy', sid)
    read = view()
    check('strategy_a_round_concluding_no_change_ends_with_a_reconfirmation_and_no_new_revision',
          r6['version'] == ended['version'] == written['version'] == read['business']['version']
          and read['business']['round'] is None and read['records']['lifecycle'] == after['records']['lifecycle']
          and read['business']['formal']['effective_revision_id'] == written['revision_id'])
    designate('a', payload={'blocks': {'business_logic': {'text': '先 A 后 B。'}}})
    agree('a')
    voided = act('ceo', 'world_confirm_strategy', sid, {'outcome': 'returned'})
    read = view()
    check('strategy_returning_an_effective_round_voids_it_and_writes_nothing_back',
          voided['version'] == written['version'] == read['business']['version']
          and block_text(read, 'business_logic') == block_text(flow.read('outsider', sid, version=written['version']), 'business_logic')
          and read['business']['round'] is None and read['records']['lifecycle'] == after['records']['lifecycle'])

    # ================================================================ 代记一条 Agreement（契约第 14 节）
    def grant(actor, domain):
        return flow.act(actor, 'world_grant_delegation', {
            'delegate_principal_id': actor_id['tianshu'], 'families': ['gate'], 'domain_ids': [f['domains'][domain]],
            'valid_until': utc_now(days=1)})['result']

    records = iter(range(1, 100))

    def behalf(person):
        return {'principal_id': actor_id[person], 'external_record_id': f'tianshu:agree:{next(records)}',
                'external_confirmed_at': utc_now(minutes=-5)}

    r8 = designate('a', 'b')
    grant('b', 'b')  # 只覆盖单元 b，不覆盖 Strategy 所在的公司域
    deny('tianshu', 'world_agree_strategy', sid, {'FORBIDDEN'}, {'on_behalf_of': behalf('b')}, says='No delegation')
    grant('c', 'company')  # 覆盖公司域，但 c 不在本轮
    deny('tianshu', 'world_agree_strategy', sid, {'FORBIDDEN'}, {'on_behalf_of': behalf('c')},
         says='recorded by designated')
    deny('tianshu', 'world_agree_strategy', sid, {'FORBIDDEN'}, says='by a person')
    check('strategy_an_agreement_on_behalf_needs_a_delegation_covering_the_strategys_domain_and_a_designated_person')
    delegation = grant('b', 'company')
    sent = behalf('b')
    body = flow.prepare('tianshu', flow.targeted('world_agree_strategy', sid, {'on_behalf_of': sent}))
    receipt = flow.commit('tianshu', body)
    result, row = receipt['result'], event(receipt['result']['event_id'])
    check('strategy_the_service_principal_records_an_agreement_on_behalf_of_a_designated_person',
          str(row['principal_id']) == actor_id['tianshu'] and str(row['on_behalf_of']) == b
          and row['external_record_id'] == sent['external_record_id'] and row['kind'] == 'agreement'
          and row['detail']['round_event_id'] == r8['event_id'] and row['detail']['round_complete'] is False
          and result['required_assignment_ids'] == used_assignment(b)
          and result['on_behalf_of']['principal_id'] == b
          and result['on_behalf_of']['delegation_event_id'] == delegation['event_id']
          and current_round()['pending'] == [a]
          and [item['principal_id'] for item in current_round()['agreements']] == [b])
    replay = flow.commit('tianshu', deepcopy(body))
    check('strategy_replaying_an_agreement_recorded_on_behalf_returns_the_original_receipt',
          replay['receipt_id'] == receipt['receipt_id'] and len(events_of(receipt['receipt_id'])) == 1)
    deny('b', 'world_agree_strategy', sid, {'INVALID_STATE'}, says='already agreed')
    check('strategy_the_person_recorded_on_behalf_has_agreed_and_does_not_agree_again_in_person')
    agree('a')
    act('ceo', 'world_reconfirm_strategy', sid)
    check('strategy_the_round_agreed_partly_on_behalf_ends_with_a_reconfirmation',
          current_round() is None and view()['business']['version'] == written['version'])
