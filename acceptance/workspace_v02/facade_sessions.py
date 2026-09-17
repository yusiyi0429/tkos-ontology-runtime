"""Real-HTTP acceptance for the local session facade over workspace/0.2.

Covers human source/context prepare -> commit -> get -> list -> retry, the
top-level ``context_id`` shape of Context saves, and journal privacy after the
current source grant is revoked. Reads only; no production module is changed
and every record is created through the facade itself.

Failures are collected (not fail-fast) so independent checks still run and the
private ``facade-findings.md`` report contains the complete picture for pi#1.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

import httpx

PREFIX = '/dashboard/api/v1'


def uid():
    return str(uuid.uuid4())


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Facade:
    def __init__(self, url: str, login: dict):
        self.url = url
        self.client = httpx.Client(base_url=url, trust_env=False, timeout=30,
                                   headers={'Origin': url})
        response = self.client.post(PREFIX + '/session', json=login)
        response.raise_for_status()
        body = response.json()
        self.identity = body['identity']
        self.client.headers['X-CSRF-Token'] = body['csrf']

    def close(self):
        self.client.close()

    def call(self, path, body=None, status=200):
        response = None
        for attempt in range(2):
            try:
                if body is None:
                    response = self.client.get(PREFIX + path)
                else:
                    response = self.client.post(PREFIX + path, json=body)
                break
            except httpx.TransportError:
                # A previous 500 may close the keep-alive socket; resend once.
                if attempt == 1:
                    raise AssertionError(f'{path} -> transport error') from None
        if response.status_code != status:
            raise AssertionError(f'{path} -> {response.status_code}')
        return response.json()

    def prepare(self, body):
        return self.call('/commands/prepare', body)

    def commit(self, command_id, retry=False):
        return self.call('/commands/' + command_id + ('/retry' if retry else '/commit'), {})

    def get(self, command_id, status=200):
        return self.call('/commands/' + command_id, status=status)

    def probe(self, path):
        """Read-only status + body; never falls back to core /v1 and never raises on 4xx."""
        try:
            response = self.client.get(PREFIX + path)
        except httpx.TransportError:
            return None, None
        try:
            body = response.json()
        except ValueError:
            body = None
        return response.status_code, body

    def listing(self):
        return self.call('/commands')


def run(url: str, private: Path):
    fixture = json.loads((private / 'identities.json').read_text())
    owner = Facade(url, json.loads((private / 'dri-a-login.json').read_text()))
    grantee = Facade(url, json.loads((private / 'dri-b-login.json').read_text()))
    non_member = Facade(url, json.loads((private / 'ceo-login.json').read_text()))
    checks, failures, missing_reads = [], [], []

    def check(name, condition=True):
        if condition:
            checks.append(name)
            print('PASS ' + name, flush=True)
        else:
            failures.append(name)
            print('FAIL ' + name, flush=True)

    def action_result(payload):
        """Read a core result whether the journal view wraps the receipt or not."""
        receipt = payload.get('receipt')
        if isinstance(receipt, dict) and isinstance(receipt.get('receipt'), dict):
            receipt = receipt['receipt']
        return (receipt or {}).get('result') or {}

    def action_receipt(payload):
        receipt = payload.get('receipt')
        if isinstance(receipt, dict) and isinstance(receipt.get('receipt'), dict):
            return receipt['receipt']
        return receipt or {}

    def optional(call):
        try:
            return call()
        except (AssertionError, httpx.HTTPError):
            return None

    canary = 'FACADE-CANARY-' + uuid.uuid4().hex
    scene_id = uid()
    a_principal = fixture['actors']['a']['principal_id']
    b_principal = fixture['actors']['b']['principal_id']
    when = now_iso()
    context_id = None
    try:
        core = httpx.Client(base_url=url, trust_env=False, timeout=30,
                            headers={'Authorization': 'Bearer ' + fixture['actors']['b']['token']})
        check('facade_session_identity_matches_fixture',
              grantee.identity['principal_id'] == b_principal
              and grantee.identity['principal_type'] == 'human')

        # --------------------------------------------- scene + source events
        create_body = {'contract_version': 'tkos.workspace/0.2', 'scene_id': scene_id,
                       'expected_version': 0, 'idempotency_key': uid(),
                       'event': {'kind': 'scene_create', 'scene_type': 'selected_conversation',
                                 'external_id': 'facade-' + uuid.uuid4().hex,
                                 'title': 'Facade source session', 'owner_principal_id': a_principal,
                                 'participant_principal_ids': [b_principal], 'agent_bindings': []}}
        command = owner.prepare(create_body)
        created = owner.commit(command['command_id'])
        check('facade_scene_prepare_commit', created['status'] == 'committed'
              and action_result(created).get('kind') == 'scene_create'
              and action_result(created).get('formal_effect') == 'none')
        try:
            repeated_create = owner.prepare(deepcopy(create_body))['command_id']
        except AssertionError:
            repeated_create = None
        check('facade_prepare_idempotent_same_command',
              repeated_create == command['command_id'])
        # A double-click on an uncommitted create stays one visible command.
        pending_scene = uid()
        pending_body = {'contract_version': 'tkos.workspace/0.2', 'scene_id': pending_scene,
                        'expected_version': 0, 'idempotency_key': uid(),
                        'event': {'kind': 'scene_create', 'scene_type': 'meeting',
                                  'external_id': 'facade-pending-' + uuid.uuid4().hex,
                                  'title': 'Facade pending create', 'owner_principal_id': a_principal,
                                  'participant_principal_ids': [], 'agent_bindings': []}}
        pending_command = owner.prepare(pending_body)
        pending_again = owner.prepare(deepcopy(pending_body))
        pending_get = optional(lambda: owner.get(pending_command['command_id']))
        pending_list = optional(lambda: owner.listing())
        check('facade_uncommitted_create_single_visible_command',
              pending_again['command_id'] == pending_command['command_id']
              and pending_get is not None
              and pending_get.get('status') == 'prepared'
              and (pending_get.get('envelope') or {}).get('event', {}).get('kind') == 'scene_create')
        check('facade_uncommitted_create_listed_before_commit',
              pending_list is not None
              and any(item['command_id'] == pending_command['command_id']
                      and item.get('status') == 'prepared'
                      for item in pending_list.get('items', [])))
        pending_commit = owner.commit(pending_command['command_id'])
        check('facade_uncommitted_create_commits_once',
              pending_commit['status'] == 'committed'
              and action_result(pending_commit).get('kind') == 'scene_create')
        version = action_result(created)['version']

        add_body = {'contract_version': 'tkos.workspace/0.2', 'scene_id': scene_id,
                    'expected_version': version, 'idempotency_key': uid(),
                    'event': {'kind': 'source_add', 'system': 'local-fixture',
                              'external_id': 'facade-source-' + uuid.uuid4().hex,
                              'title': 'Facade synthetic source', 'media_type': 'text/plain',
                              'acquired_at': when, 'sensitivity': 'private'}}
        added = owner.commit(owner.prepare(add_body)['command_id'])
        check('facade_source_add_commit', added['status'] == 'committed'
              and action_result(added).get('source_id'))
        source_id = action_result(added)['source_id']
        version = action_result(added)['version']

        artifact = ('FACADE-SYNTHETIC-ARTIFACT ' + canary).encode()
        version_body = {'contract_version': 'tkos.workspace/0.2', 'scene_id': scene_id,
                        'expected_version': version, 'idempotency_key': uid(),
                        'event': {'kind': 'source_version', 'source_id': source_id,
                                  'fingerprint': hashlib.sha256(artifact).hexdigest(),
                                  'media_type': 'text/plain', 'acquired_at': when,
                                  'segments': [{'speaker': 'A', 'text': canary + ' facade segment',
                                                'occurred_at': when}]}}
        version_command = owner.prepare(version_body)
        appended = owner.commit(version_command['command_id'])
        check('facade_source_version_commit', appended['status'] == 'committed')
        version_event = action_result(appended)['event_id']
        version_hash = action_result(appended)['payload_hash']
        version = action_result(appended)['version']

        share_body = {'contract_version': 'tkos.workspace/0.2', 'scene_id': scene_id,
                      'expected_version': version, 'idempotency_key': uid(),
                      'event': {'kind': 'source_share', 'source_id': source_id,
                                'version_event_id': version_event, 'payload_hash': version_hash,
                                'share_to_principal_id': b_principal}}
        shared = owner.commit(owner.prepare(share_body)['command_id'])
        check('facade_source_share_exact_grant', shared['status'] == 'committed')
        share_event = action_result(shared)['event_id']
        version = action_result(shared)['version']

        # ------------------------------------------- context save/get/list/retry
        context_body = {'contract_version': 'tkos.workspace/0.2', 'scene_id': scene_id,
                        'idempotency_key': uid(), 'purpose': 'facade purpose ' + canary,
                        'items': [{'source_id': source_id, 'version_event_id': version_event,
                                   'payload_hash': version_hash}]}
        context_command = grantee.prepare(context_body)
        context_commit = grantee.commit(context_command['command_id'])
        check('facade_context_commit_top_level_context_id',
              context_commit['status'] == 'committed'
              and context_commit['receipt'].get('context_id')
              and 'result' not in context_commit['receipt']
              and context_commit['receipt']['complete'] is True)
        context_id = context_commit['receipt'].get('context_id')
        context_retry = grantee.commit(context_command['command_id'], retry=True)
        check('facade_context_retry_same_snapshot',
              context_retry['receipt'].get('context_id') == context_id)
        context_get = optional(lambda: grantee.get(context_command['command_id']))
        check('facade_context_get_reauthorized_top_level_context_id',
              context_get is not None
              and context_get['receipt'].get('context_id') == context_id
              and 'result' not in context_get['receipt']
              and context_get['receipt']['complete'] is True)

        # ------------------- frontend read URLs: session facade, not core /v1
        anonymous = httpx.Client(base_url=url, trust_env=False, timeout=10)
        try:
            anonymous_status = anonymous.get(PREFIX + '/governance/tasks').status_code
        finally:
            anonymous.close()
        check('facade_reads_require_session', anonymous_status == 401)

        def required_read(name, path, shape, actor):
            status, body = actor.probe(path)
            unregistered = (status == 404 and isinstance(body, dict)
                            and body.get('detail') == 'Not Found')
            if unregistered:
                missing_reads.append(path)
            check(name, not unregistered and status == 200
                  and (shape is None or (isinstance(body, dict) and shape(body))))
            return status, body

        required_read('facade_read_method_tasks', '/governance/method-tasks',
                      lambda body: isinstance(body.get('items'), list), owner)
        status, body = required_read('facade_read_sources_list', '/governance/sources',
                                     lambda body: isinstance(body.get('items'), list), owner)
        check('facade_sources_list_member_scope',
              status == 200 and isinstance(body, dict)
              and any(item.get('scene_id') == scene_id for item in body.get('items', [])))
        required_read('facade_read_source_detail', '/governance/sources/' + scene_id,
                      lambda data: data.get('scene_id') == scene_id, owner)
        facade_context_status, facade_context_body = grantee.probe(
            '/governance/sources/contexts/' + context_id)
        unregistered_context = (facade_context_status == 404 and isinstance(facade_context_body, dict)
                                and facade_context_body.get('detail') == 'Not Found')
        if unregistered_context:
            missing_reads.append('/governance/sources/contexts/{context_id}')
        check('facade_read_source_context',
              not unregistered_context and facade_context_status == 200
              and isinstance(facade_context_body, dict)
              and facade_context_body.get('context_id') == context_id
              and facade_context_body.get('complete') is True
              and canary in json.dumps(facade_context_body))
        actions_status, actions_body = owner.probe('/governance/objects/' + scene_id + '/actions')
        unregistered_actions = (actions_status == 404 and isinstance(actions_body, dict)
                                and actions_body.get('detail') == 'Not Found')
        if unregistered_actions:
            missing_reads.append('/governance/objects/{object_id}/actions')
        check('facade_read_object_actions',
              not unregistered_actions and isinstance(actions_body, dict)
              and (actions_status == 200
                   or actions_body.get('error', {}).get('code') in {'NOT_FOUND', 'FORBIDDEN'}))
        non_member_status, non_member_body = non_member.probe('/governance/sources')
        check('facade_sources_list_non_member_scope',
              non_member_status == 200 and isinstance(non_member_body, dict)
              and all(item.get('scene_id') != scene_id
                      for item in non_member_body.get('items', [])))
        denied_status, denied_body = non_member.probe('/governance/sources/' + scene_id)
        check('facade_source_detail_non_member_denied',
              denied_status == 404 and isinstance(denied_body, dict)
              and denied_body.get('error', {}).get('code') == 'NOT_FOUND')

        # ------------------------------------------------ cited draft + journal
        draft_body = {'contract_version': 'tkos.workspace/0.2', 'scene_id': scene_id,
                      'expected_version': version, 'idempotency_key': uid(),
                      'event': {'kind': 'followup_draft', 'title': 'Facade draft',
                                'items': [{'item_kind': 'suggestion', 'text': 'Draft ' + canary,
                                           'citations': [{'source_id': source_id,
                                                          'version_event_id': version_event,
                                                          'payload_hash': version_hash,
                                                          'segment_index': 0,
                                                          'quote': canary + ' facade segment'}]}]}}
        draft_command = grantee.prepare(draft_body)
        draft_commit = grantee.commit(draft_command['command_id'])
        check('facade_draft_prepare_commit', draft_commit['status'] == 'committed')
        draft_retry = grantee.commit(draft_command['command_id'], retry=True)
        check('facade_draft_retry_same_receipt',
              action_receipt(draft_retry).get('receipt_id') == action_receipt(draft_commit).get('receipt_id'))
        draft_get = grantee.get(draft_command['command_id'])
        check('facade_journal_draft_visible_before_revoke',
              draft_get.get('envelope') is not None and canary in json.dumps(draft_get))
        # The only other derived free text a grantee can author is a decision
        # note, which may quote the source just as a draft body can.
        note_canary = 'NOTE-CANARY-' + uuid.uuid4().hex
        decision_body = {'contract_version': 'tkos.workspace/0.2', 'scene_id': scene_id,
                         'expected_version': action_result(draft_commit)['version'],
                         'idempotency_key': uid(),
                         'event': {'kind': 'draft_decision',
                                   'draft_event_id': action_result(draft_commit)['event_id'],
                                   'item_index': 0, 'decision': 'noted',
                                   'note': 'Decision note ' + note_canary + ' ' + canary}}
        decision_command = grantee.prepare(decision_body)
        decision_commit = grantee.commit(decision_command['command_id'])
        check('facade_draft_decision_commit', decision_commit['status'] == 'committed')
        decision_get = grantee.get(decision_command['command_id'])
        check('facade_draft_decision_note_visible_before_revoke',
              decision_get.get('envelope') is not None and note_canary in json.dumps(decision_get))
        version = action_result(decision_commit)['version']

        grantee_listing = optional(lambda: grantee.listing())
        owner_listing = optional(lambda: owner.listing())
        check('facade_list_contains_own_commands',
              grantee_listing is not None and owner_listing is not None
              and {item['command_id'] for item in grantee_listing['items']}
              >= {context_command['command_id'], draft_command['command_id']}
              and {item['command_id'] for item in owner_listing['items']} >= {command['command_id']})
        cross = owner.get(draft_command['command_id'], status=404)
        check('facade_private_journal_hides_cross_identity',
              cross.get('error', {}).get('code') == 'NOT_FOUND')

        # ------------------------------- member display + controlled fixture metadata
        scene = core.get('/v1/workspace-sources/' + scene_id).json()
        members = scene['scene']['members']
        check('member_display_projection_verified',
              members['authority'] == 'display_only_not_authorization'
              and members['owner']['display_name'] and members['owner']['current'] is True
              and all(item['display_name'] and item['current'] is True
                      for item in members['participants']))
        fixture_source = Path(__file__).resolve().parents[2] / 'acceptance/workspace_v02/run.py'
        fixture_text = fixture_source.read_text()
        check('fixture_model_metadata_controlled',
              'controlled-fixture' in fixture_text and '"deepseek"' not in fixture_text)

        # ------------------------------------- current source revoke -> journal privacy
        # "current source revoke" here intentionally uses the source grant; a
        # withdrawn version is the second path checked below.
        unshare_body = {'contract_version': 'tkos.workspace/0.2', 'scene_id': scene_id,
                        'expected_version': version, 'idempotency_key': uid(),
                        'event': {'kind': 'source_unshare', 'share_event_id': share_event,
                                  'reason': 'Facade acceptance revokes the current grant'}}
        unshared = owner.commit(owner.prepare(unshare_body)['command_id'])
        check('facade_unshare_current_source', unshared['status'] == 'committed')

        revoked_draft = grantee.get(draft_command['command_id'])
        check('facade_journal_draft_withheld_after_revoke',
              revoked_draft.get('envelope') is None
              and revoked_draft.get('payload_withheld') == 'source_derived_payload_withheld'
              and canary not in json.dumps(revoked_draft))
        def no_private_note(payload, marker):
            envelope = payload.get('envelope') or {}
            event = envelope.get('event') or {}
            return marker not in json.dumps(payload) and (
                payload.get('envelope') is None or event.get('note') in (None, ''))

        revoked_decision = optional(lambda: grantee.get(decision_command['command_id']))
        check('facade_journal_draft_decision_note_withheld_after_revoke',
              revoked_decision is not None
              and revoked_decision.get('command_id') == decision_command['command_id']
              and no_private_note(revoked_decision, note_canary))
        revoked_listing = optional(lambda: grantee.listing())
        check('facade_journal_listing_withholds_decision_note_after_revoke',
              revoked_listing is not None
              and any(item['command_id'] == decision_command['command_id']
                      for item in revoked_listing.get('items', []))
              and note_canary not in json.dumps(revoked_listing))
        revoked_context = optional(lambda: grantee.get(context_command['command_id']))
        check('facade_journal_context_withheld_after_revoke',
              revoked_context is not None
              and revoked_context.get('envelope') is None
              and revoked_context.get('payload_withheld') == 'source_derived_payload_withheld'
              and revoked_context['receipt'].get('context_id') == context_id
              and revoked_context['receipt'].get('purpose') is None
              and revoked_context['receipt'].get('complete') is False
              and canary not in json.dumps(revoked_context))

        version = action_result(unshared)['version']
        withdraw_body = {'contract_version': 'tkos.workspace/0.2', 'scene_id': scene_id,
                         'expected_version': version, 'idempotency_key': uid(),
                         'event': {'kind': 'source_withdraw', 'source_id': source_id,
                                   'version_event_id': version_event,
                                   'reason': 'Facade acceptance retires the exact version'}}
        withdrawn = owner.commit(owner.prepare(withdraw_body)['command_id'])
        check('facade_withdraw_current_source_version', withdrawn['status'] == 'committed')
        after_withdraw = optional(lambda: grantee.get(context_command['command_id']))
        check('facade_journal_withheld_after_withdraw',
              after_withdraw is not None
              and after_withdraw.get('envelope') is None
              and canary not in json.dumps(after_withdraw))
        facade_after = grantee.probe('/governance/sources/contexts/' + context_id)
        check('facade_source_context_recheck_after_revoke',
              facade_after[0] == 200 and isinstance(facade_after[1], dict)
              and facade_after[1].get('context_id') == context_id
              and facade_after[1].get('complete') is False
              and facade_after[1].get('purpose') is None
              and canary not in json.dumps(facade_after[1]))
        after_withdraw_decision = optional(lambda: grantee.get(decision_command['command_id']))
        check('facade_journal_decision_note_withheld_after_withdraw',
              after_withdraw_decision is not None
              and after_withdraw_decision.get('command_id') == decision_command['command_id']
              and no_private_note(after_withdraw_decision, note_canary))

        # GET redaction is not enough: the same private envelope came back from
        # the commit/retry tail before pi#1's fix.  Exercise both paths after
        # every revocation for every kind carrying derived private text.
        def no_segments(payload):
            envelope = payload.get('envelope') or {}
            event = envelope.get('event') or {}
            return payload.get('envelope') is None or event.get('segments') in (None, [])

        def no_purpose(payload):
            envelope = payload.get('envelope') or {}
            return payload.get('envelope') is None or envelope.get('purpose') in (None, '')

        def marker_free(payload, marker):
            return marker not in json.dumps(payload)

        revoked_commands = [
            ('facade_revoked_source_segments_commit_retry', owner, version_command, canary, no_segments),
            ('facade_revoked_context_purpose_commit_retry', grantee, context_command, canary, no_purpose),
            ('facade_revoked_draft_body_commit_retry', grantee, draft_command, canary, None),
            ('facade_revoked_decision_note_commit_retry', grantee, decision_command, note_canary,
             lambda payload: no_private_note(payload, note_canary)),
        ]
        for name, actor, command, marker, shape in revoked_commands:
            commit_response = optional(lambda a=actor, c=command: a.commit(c['command_id']))
            retry_response = optional(lambda a=actor, c=command: a.commit(c['command_id'], retry=True))
            check(name,
                  commit_response is not None and retry_response is not None
                  and marker_free(commit_response, marker) and marker_free(retry_response, marker)
                  and (shape is None or (shape(commit_response) and shape(retry_response))))
            get_response = optional(lambda a=actor, c=command: a.get(c['command_id']))
            check(name.replace('_commit_retry', '_get_view'),
                  get_response is not None and marker_free(get_response, marker)
                  and (shape is None or shape(get_response)))
        return checks, failures, missing_reads
    finally:
        owner.close()
        grantee.close()
        non_member.close()
        if 'core' in locals():
            core.close()


def write_findings(private: Path, url: str, checks: list[str], failures: list[str],
                   missing_reads: list[str]):
    lines = [
        '# Session facade source/context acceptance findings (pi#2 → pi#1)',
        '',
        f'- URL origin: `{url}`',
        f'- Verdict: **{"passed" if not failures else "failed"}**; passed {len(checks)}, failed {len(failures)}.',
        '- Scope: real HTTP `/dashboard/api/v1` session facade over `tkos.workspace/0.2` source and'
        ' Context actions only; no pi#1 application module modified (a B2-owned module was fixed: see below).',
        '- Records: scene/source/share/context/draft created via facade prepare+commit; assertions only.',
        '',
        '## Verified',
        '',
    ]
    lines += [f'- {name}' for name in checks] or ['- (none)']
    if missing_reads:
        lines += ['', '## Missing session-facade reads (frontend URLs)', '']
        lines += [f'- `{path}` → 404 with FastAPI `detail=Not Found` (route not registered).' for path in missing_reads]
        lines += [
            '',
            'Suggested thin wrappers in pi#1 `app/governance.py` (same session identity, no core URL'
            ' substitution from the browser): `current(request)` then `reads.read(token, ...)`;'
            'Cache-Control no-store:',
            '',
            '- `/dashboard/api/v1/governance/method-tasks` → `reads.read(token, reads.method_tasks, after, limit)`',
            '- `/dashboard/api/v1/governance/sources` → `reads.read(token, reads.source_scenes, scene_type, after, limit)`',
            '- `/dashboard/api/v1/governance/sources/{scene_id}` → `reads.read(token, reads.source_scene, str(scene_id))`',
            '- `/dashboard/api/v1/governance/sources/contexts/{context_id}` → `reads.read(token, reads.source_context, str(context_id))`',
            '- `/dashboard/api/v1/governance/objects/{object_id}/actions` → `reads.read(token, reads.actions, str(object_id))`',
        ]
    if failures:
        lines += ['', '## Failures (pi#1 to confirm/fix)', '']
        lines += [f'- {name}' for name in failures]
        if any('decision' in name for name in failures):
            lines += [
                '',
                '### Derived free text beyond draft/context (pi#1 to extend withholding)',
                '',
                '- `governance_commands.get()` withholds the original envelope only for `followup_draft` and'
                ' `context_v02`. A committed `draft_decision` carries a human `note` that may quote the same'
                ' private source; after the current grant is revoked the envelope (and the `GET /commands`'
                ' list) must not restore that note either.',
                '- Suggested minimal fix: treat every scene_v02 event with derived free text the same way'
                ' (at minimum `draft_decision`: resolve the referenced draft citations and set'
                ' `source_derived_payload_withheld` / drop `note` when any cited version is unreadable).',
            ]
        if any('prepare_idempotent' in name for name in failures):
            lines += [
                '',
                '### Committed scene_create re-prepare returns 409',
                '',
                '- After a `scene_create` has been committed, repeating `prepare` with the identical body must'
                ' return the same `command_id` (idempotent double-click). The new uncommitted-create branch in'
                ' `governance_commands.get()` also runs for committed records: `v02.validate_create(..., [], event)`'
                ' re-checks the external id against the persisted scene and returns `VERSION_CONFLICT` (409).',
                '- Suggested fix: run the uncommitted-create validation only when the stored record is not yet'
                ' committed, or skip the persisted external-id duplicate check for the existing scene.',
            ]
        if any('commit_retry' in name for name in failures):
            lines += [
                '',
                '### Commit/retry still return the private envelope after revoke (pi#1)',
                '',
                '- `governance_commands.commit()` ends with `return data` for both `/commit` and `/retry`,'
                ' i.e. the private on-disk envelope. `GET /commands/{id}` is redacted under current'
                ' authorization, but the commit/retry responses still carry the original `segments`,'
                ' Context `purpose`, draft body/citations and `draft_decision.note`.',
                '- Required: the browser-visible commit/retry response must be the same current-authorization'
                ' read view as `GET /commands/{id}` (or return a receipt/envelope redacted by the same'
                ' withholder logic), never the raw private envelope. Retry must not become a recovery path'
                ' for revoked private text.',
                '- Reproduced after revoke for: `source_version` segments (source withdraw), Context purpose'
                ' (source unshare), draft body, and `draft_decision.note`.',
            ]
        if any('context_get_reauthorized' in name or 'journal_context_withheld' in name for name in failures):
            lines += [
                '',
                '### Context journal GET',
                '',
                '- ``POST /commands/{id}/commit`` for a `context_v02` body returns the Context view at the'
                ' **top level** (`context_id`/`complete`/`items`), which is the agreed shape.',
                '- ``GET /commands/{id}`` / ``GET /commands`` must re-read the snapshot under current'
                ' authorization and must not assume a receipt envelope (`receipt_id`).',
            ]
    lines += [
        '',
        '## Contract notes',
        '',
        '- Context save commits return the context view itself: top-level `context_id`/`complete`/`items`,'
        ' never `receipt.result`.',
        '- After the current exact-version grant is revoked (unshare) or the version is withdrawn, the local'
        ' journal keeps only safe metadata: the envelope is withheld or its derived free-text fields'
        ' (`note`, purpose, quote-bearing bodies) are emptied; old quotes/items/purpose are not recoverable.',
        '- Journal access is per identity: another human gets 404 for a command they did not prepare.',
        '- Facade 0.2 allowlist excludes Agent-only events (`agent_run`); Agent-created Context/run stay on the'
        ' runtime contract, not the human session facade (by design).',
        '- Member display projection: bounded to visible scene members, display-only, explicit current/status.',
        '- Fixture metadata: synthetic runs carry `controlled-fixture` provider/name/version, no real-model'
        ' provenance and no deepseek/flash/max values.',
        '- B2-side observation (fixed by pi#2 in the B2-owned `workspace_v02_service.py`, no pi#1 app file):'
        ' `authorize_receipt` assumed `payload.source_id` for every source kind, so facade journal reads of a'
        ' `source_add` receipt (identity = its event) or `source_unshare` receipt (resolve via share event)'
        ' raised `KeyError`. Fixed with a unit test; core B2 acceptance re-run separately.',
        '',
    ]
    path = private / 'facade-findings.md'
    path.write_text('\n'.join(lines))
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--private', type=Path, required=True)
    args = parser.parse_args()
    args.private = args.private.resolve()
    checks, failures, missing_reads, error = [], [], [], None
    try:
        checks, failures, missing_reads = run(args.url, args.private)
    except Exception as exc:  # noqa: BLE001 - report type only, never the body
        error = type(exc).__name__
        write_findings(args.private, args.url, checks, failures + [f'run_error:{error}'], missing_reads)
        raise
    write_findings(args.private, args.url, checks, failures, missing_reads)
    if failures:
        print('facade acceptance found ' + str(len(failures)) + ' failure(s); see private facade-findings.md',
              flush=True)
        raise SystemExit(1)
    print('facade source/context acceptance passed: ' + str(len(checks)) + ' checks', flush=True)


if __name__ == '__main__':
    main()
