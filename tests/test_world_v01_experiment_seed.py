"""E&O 九月回放（票 #28）：播种文件与标准答案的自洽校验、占位引用解析，以及 DRI 批准——未批准或批准后内容
改过，实验跑器都拒绝使用；审阅稿与两份文件保持一致。"""
import json
from pathlib import Path
import shutil

import pytest

from experiments.world_v01 import gold, spec

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'experiments/world_v01'
OBJECTS = {'mission': {'object_id': '11111111-1111-4111-8111-111111111111', 'version': 3},
           'task': {'object_id': '22222222-2222-4222-8222-222222222222', 'version': 1}}
PRINCIPALS = {'owner': '33333333-3333-4333-8333-333333333333'}


def minimal_seed():
    return {
        'format': 'tkos-world-seed/0.1',
        'scopes': {'main': {'domains': {'company': '公司', 'eo': 'E&O'}}},
        'identities': {'ceo': {'scope': 'main', 'type': 'human', 'display_name': 'CEO',
                               'roles': {'company': ['CEO'], 'eo': ['CEO']}},
                       'owner': {'scope': 'main', 'type': 'human', 'display_name': 'Mission Owner',
                                 'roles': {'eo': ['OWNER']}}},
        'steps': [
            {'key': 'company', 'do': 'create', 'by': 'ceo', 'type': 'Company', 'domain': 'company',
             'payload': {'title': 'x'}},
            {'key': 'strategy', 'do': 'create', 'by': 'ceo', 'type': 'Strategy', 'domain': 'company',
             'payload': {'title': 'x', 'parent_ref': '@company'}},
            {'do': 'assign', 'by': 'ceo', 'target': 'strategy', 'to': 'owner'},
        ],
    }


# ------------------------------------------------------------------ placeholders
def test_placeholders_resolve_to_business_refs_and_principals():
    value = {'goal_ref': '@mission', 'blocks': {'plan': {'text': '见 @mission', 'refs': ['@mission#acceptance', '@task@1']}},
             'acceptor': '$owner', 'list': ['@mission@2#play']}
    assert spec.resolve(value, OBJECTS, PRINCIPALS) == {
        'goal_ref': '11111111-1111-4111-8111-111111111111@3',
        'blocks': {'plan': {'text': '见 @mission',
                            'refs': ['11111111-1111-4111-8111-111111111111@3#acceptance',
                                     '22222222-2222-4222-8222-222222222222@1']}},
        'acceptor': '33333333-3333-4333-8333-333333333333',
        'list': ['11111111-1111-4111-8111-111111111111@2#play']}


def test_a_placeholder_for_an_unknown_key_is_an_error():
    with pytest.raises(spec.SeedError):
        spec.resolve({'parent_ref': '@nowhere'}, OBJECTS, PRINCIPALS)
    with pytest.raises(spec.SeedError):
        spec.resolve({'acceptor': '$nobody'}, OBJECTS, PRINCIPALS)


def test_a_version_placeholder_newer_than_the_object_is_an_error():
    with pytest.raises(spec.SeedError):
        spec.resolve('@task@2', OBJECTS, PRINCIPALS)


# ------------------------------------------------------------------ seed consistency
def test_a_minimal_seed_is_consistent():
    spec.validate(minimal_seed())


@pytest.mark.parametrize('breaks', [
    lambda s: s['steps'].insert(0, s['steps'].pop(1)),                    # 引用了后面才建的对象
    lambda s: s['steps'][2].update(by='nobody'),                          # 执行者不存在
    lambda s: s['steps'][2].update(to='nobody'),                          # 被指派者不存在
    lambda s: s['steps'][1].update(key='company'),                        # 键重复
    lambda s: s['identities']['owner']['roles'].update(delivery=['IC']),  # 角色落在不存在的域
    lambda s: s['steps'][2].update(target='nowhere'),                     # 目标不存在
    lambda s: s['steps'][0].update(do='delete'),                          # 不认识的步骤
])
def test_an_inconsistent_seed_is_refused(breaks):
    seed = minimal_seed()
    breaks(seed)
    with pytest.raises(spec.SeedError):
        spec.validate(seed)


def test_the_committed_seed_and_gold_are_consistent():
    seed = json.loads((FOLDER / 'seed.json').read_text())
    answers = json.loads((FOLDER / 'gold.json').read_text())
    spec.validate(seed)
    spec.validate_gold(answers, seed)
    assert answers['approver'] == 'E&O DRI'


def test_gold_that_cites_a_key_the_seed_does_not_have_is_refused():
    seed = json.loads((FOLDER / 'seed.json').read_text())
    answers = json.loads((FOLDER / 'gold.json').read_text())
    answers['questions'][0]['expected'].append('@nowhere#definition')
    with pytest.raises(spec.SeedError):
        spec.validate_gold(answers, seed)
    answers = json.loads((FOLDER / 'gold.json').read_text())
    answers['counterexamples'][0]['decoys'] = ['event:nowhere']
    with pytest.raises(spec.SeedError):
        spec.validate_gold(answers, seed)


# ------------------------------------------------------------------ DRI approval
@pytest.fixture
def copies(tmp_path):
    for name in ('seed.json', 'gold.json'):
        shutil.copy(FOLDER / name, tmp_path / name)
    unsigned = json.loads((tmp_path / 'gold.json').read_text())
    unsigned['approval'] = {'approved_by': None, 'approved_at': None, 'content_sha256': None}
    (tmp_path / 'gold.json').write_text(json.dumps(unsigned, ensure_ascii=False))
    return tmp_path


def test_unapproved_gold_is_refused(copies):
    with pytest.raises(gold.NotApproved):
        gold.load_approved(copies / 'gold.json')


def test_approved_gold_is_loaded_until_either_file_changes(copies):
    gold.approve(copies / 'gold.json', 'E&O DRI')
    loaded = gold.load_approved(copies / 'gold.json')
    assert loaded['gold']['approval']['approved_by'] == 'E&O DRI' and loaded['seed']['steps']
    answers = json.loads((copies / 'gold.json').read_text())
    answers['questions'][0]['answer'] += '（改过）'
    (copies / 'gold.json').write_text(json.dumps(answers, ensure_ascii=False))
    with pytest.raises(gold.NotApproved):
        gold.load_approved(copies / 'gold.json')
    gold.approve(copies / 'gold.json', 'E&O DRI')
    seed = json.loads((copies / 'seed.json').read_text())
    seed['steps'][0]['payload']['title'] += '（改过）'
    (copies / 'seed.json').write_text(json.dumps(seed, ensure_ascii=False))
    with pytest.raises(gold.NotApproved):
        gold.load_approved(copies / 'gold.json')


def test_approved_gold_is_refused_for_a_world_seeded_from_other_content(copies):
    approval = gold.approve(copies / 'gold.json', 'E&O DRI')
    assert gold.load_approved(copies / 'gold.json', {'content_sha256': approval['content_sha256']})
    with pytest.raises(gold.NotApproved):
        gold.load_approved(copies / 'gold.json', {'content_sha256': '0' * 64})


def test_an_approval_is_signed_with_the_approver_role_not_a_personal_name(copies):
    for by in ('  ', 'Someone Personal'):
        with pytest.raises(ValueError):
            gold.approve(copies / 'gold.json', by)
    with pytest.raises(gold.NotApproved):
        gold.load_approved(copies / 'gold.json')


def test_a_decoy_cannot_also_be_an_expected_reference():
    seed = json.loads((FOLDER / 'seed.json').read_text())
    answers = json.loads((FOLDER / 'gold.json').read_text())
    answers['counterexamples'][0]['decoys'].append(answers['questions'][0]['expected'][0])
    with pytest.raises(spec.SeedError):
        spec.validate_gold(answers, seed)


def test_the_review_document_matches_the_seed_and_gold():
    assert (ROOT / 'docs/world-v01-eo-september-review.md').read_text() == gold.render(FOLDER / 'gold.json')
