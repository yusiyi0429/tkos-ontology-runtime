"""P0A §5.1B/§5.2：Working Memory actor-in-scope 负向用例与事务零部分写入。

覆盖：
- create_chain_with_first_signal / create_signal / form_issue / create_judgment /
  create_agreement / create_mission / create_close / append_version 的跨 scope actor 拒绝，
  跨 scope 维度同时覆盖（异 tenant/同 org）与（同 tenant/异 org），actor kind 覆盖
  human 与 agent_service；
- 零部分写入在同一可提交事务内核对（不用会自动回滚的 nested savepoint 掩盖部分写入）；
- 外部关联对象（signal/issue/judgment/agreement）跨 scope 的 scoped lookup 负向；
- added_by 同 scope；确认路径仍只允许 human。
"""
from __future__ import annotations

import hashlib
import uuid

import psycopg
import pytest

from memory_service import working
from tests.conftest import Scope, connect
from tests.fixtures_p0a import GraphRig, RecordingConn

_SIGNAL_CONTENT = {"title": "越权信号", "description": "不应落库"}


@pytest.fixture
def rigs():
    scope_a = Scope()
    rig_a = GraphRig(scope_a)
    try:
        yield rig_a
    finally:
        # wm 表经 created_by FK 引用 users：必须先清 wm（Scope.cleanup），再清图与用户。
        scope_a.cleanup()
        rig_a.cleanup()


def _foreign(rig_a: GraphRig, mode: str) -> tuple[str, str]:
    return (
        rig_a.foreign_tenant_scope()
        if mode == "tenant"
        else rig_a.foreign_organization_scope()
    )


def _chain_counts(conn, chain_id: str) -> tuple[int, int, int, int]:
    """wm_objects / wm_object_versions / wm_agreement_parties / wm_issue_signals 增量快照。"""
    objects = conn.execute(
        "SELECT count(*) FROM wm_objects WHERE chain_id=%s", (chain_id,)
    ).fetchone()[0]
    versions = conn.execute(
        """SELECT count(*) FROM wm_object_versions v
             JOIN wm_objects o ON o.object_id = v.object_id WHERE o.chain_id=%s""",
        (chain_id,),
    ).fetchone()[0]
    parties = conn.execute(
        """SELECT count(*) FROM wm_agreement_parties p
             JOIN wm_object_versions v ON v.record_id = p.agreement_record_id
             JOIN wm_objects o ON o.object_id = v.object_id WHERE o.chain_id=%s""",
        (chain_id,),
    ).fetchone()[0]
    links = conn.execute(
        "SELECT count(*) FROM wm_issue_signals WHERE chain_id=%s", (chain_id,)
    ).fetchone()[0]
    return objects, versions, parties, links


def _outsider_writes(conn, outsider: str) -> int:
    """越权 actor 名下不得有任何 WM 写入残留。"""
    objects = conn.execute(
        "SELECT count(*) FROM wm_objects WHERE created_by=%s", (outsider,)
    ).fetchone()[0]
    versions = conn.execute(
        "SELECT count(*) FROM wm_object_versions WHERE created_by=%s", (outsider,)
    ).fetchone()[0]
    chains = conn.execute(
        "SELECT count(*) FROM wm_issue_chains WHERE created_by=%s", (outsider,)
    ).fetchone()[0]
    parties = conn.execute(
        "SELECT count(*) FROM wm_agreement_parties WHERE added_by=%s", (outsider,)
    ).fetchone()[0]
    return objects + versions + chains + parties


@pytest.mark.parametrize("kind", ["human", "agent_service"])
@pytest.mark.parametrize("mode", ["tenant", "organization"])
def test_create_chain_rejects_out_of_scope_creator(rigs, mode: str, kind: str):
    rig_a = rigs
    with connect() as conn, conn.transaction():
        outsider = rig_a.insert_user_in(conn, _foreign(rig_a, mode), kind=kind)
        before = conn.execute(
            "SELECT count(*) FROM wm_issue_chains WHERE tenant_id=%s AND organization_id=%s",
            (rig_a.tenant_id, rig_a.organization_id),
        ).fetchone()[0]
        # 同一可提交事务内捕获领域异常：无 savepoint 回滚兜底，任何部分写入都会被
        # 下面的增量核对直接看见。
        with pytest.raises(working.ActorOutOfScopeError):
            working.create_chain_with_first_signal(
                conn,
                title=f"越权链-{mode}-{kind}",
                signal_content=dict(_SIGNAL_CONTENT),
                created_by=outsider,
                tenant_id=rig_a.tenant_id,
                organization_id=rig_a.organization_id,
            )
        after = conn.execute(
            "SELECT count(*) FROM wm_issue_chains WHERE tenant_id=%s AND organization_id=%s",
            (rig_a.tenant_id, rig_a.organization_id),
        ).fetchone()[0]
        assert after == before
        assert _outsider_writes(conn, outsider) == 0


@pytest.mark.parametrize("kind", ["human", "agent_service"])
@pytest.mark.parametrize("mode", ["tenant", "organization"])
def test_chain_object_writes_reject_out_of_scope_actor(rigs, mode: str, kind: str):
    """create_signal / form_issue / create_judgment / append_version 跨 scope 拒绝且零部分写入。"""
    rig_a = rigs
    with connect() as conn, conn.transaction():
        rig_a.insert_user(conn)
        seeded = rig_a.scope.seed_chain(
            conn, title=f"actor scope 链-{mode}-{kind}", confirmed_judgment=True
        )
        chain_id = seeded["chain"]["chain_id"]
        outsider = rig_a.insert_user_in(conn, _foreign(rig_a, mode), kind=kind)
        before = _chain_counts(conn, chain_id)

        attempts = [
            lambda: working.create_signal(
                conn, chain_id=chain_id, content=dict(_SIGNAL_CONTENT), created_by=outsider
            ),
            lambda: working.form_issue(
                conn,
                chain_id=chain_id,
                signal_object_ids=[seeded["signal"]["object_id"]],
                key_question="越权议题？",
                rationale="不应落库",
                created_by=outsider,
            ),
            lambda: working.create_judgment(
                conn,
                chain_id=chain_id,
                issue_id=seeded["issue"]["object_id"],
                statement="越权判断",
                responsible_party="不应落库",
                created_by=outsider,
            ),
            lambda: working.append_version(
                conn,
                seeded["signal"]["object_id"],
                content=dict(_SIGNAL_CONTENT),
                created_by=outsider,
            ),
        ]
        for attempt in attempts:
            with pytest.raises(working.ActorOutOfScopeError):
                attempt()

        assert _chain_counts(conn, chain_id) == before
        assert _outsider_writes(conn, outsider) == 0


@pytest.mark.parametrize("kind", ["human", "agent_service"])
@pytest.mark.parametrize("mode", ["tenant", "organization"])
def test_agreement_mission_close_reject_out_of_scope_actor(rigs, mode: str, kind: str):
    rig_a = rigs
    with connect() as conn, conn.transaction():
        human = rig_a.insert_user(conn)
        seeded = rig_a.scope.seed_chain(
            conn, title=f"承接链-{mode}-{kind}", confirmed_judgment=True
        )
        chain_id = seeded["chain"]["chain_id"]
        issue_id = seeded["issue"]["object_id"]
        judgment_record_id = seeded["judgment"]["record_id"]
        outsider = rig_a.insert_user_in(conn, _foreign(rig_a, mode), kind=kind)

        # create_agreement：created_by 越权即拒（parties 写入也绝不发生）。
        with pytest.raises(working.ActorOutOfScopeError):
            working.create_agreement(
                conn,
                chain_id=chain_id,
                issue_id=issue_id,
                statement="越权共识",
                disposition="strategic_mission",
                confirmed_judgment_record_id=judgment_record_id,
                party_ids=[human],
                created_by=outsider,
            )

        # 正常建一条 confirmed Agreement 作为 Mission/Close 的承接对象。
        agreement = working.create_agreement(
            conn,
            chain_id=chain_id,
            issue_id=issue_id,
            statement="正常共识",
            disposition="strategic_mission",
            confirmed_judgment_record_id=judgment_record_id,
            party_ids=[human],
            created_by=human,
        )
        working.confirm_agreement(conn, agreement["record_id"], confirmer=human)
        before = _chain_counts(conn, chain_id)

        with pytest.raises(working.ActorOutOfScopeError):
            working.create_mission(
                conn,
                chain_id=chain_id,
                issue_id=issue_id,
                title="越权 Mission",
                outcome="不应落库",
                owner="不应落库",
                boundary="不应落库",
                agreement_record_id=agreement["record_id"],
                created_by=outsider,
            )

        # added_by 显式越权同样拒绝（created_by 合法）。
        with pytest.raises(working.ActorOutOfScopeError):
            working.create_agreement(
                conn,
                chain_id=chain_id,
                issue_id=issue_id,
                statement="added_by 越权共识",
                disposition="strategic_mission",
                confirmed_judgment_record_id=judgment_record_id,
                party_ids=[human],
                created_by=human,
                added_by=outsider,
            )

        assert _chain_counts(conn, chain_id) == before
        assert _outsider_writes(conn, outsider) == 0


@pytest.mark.parametrize("kind", ["human", "agent_service"])
@pytest.mark.parametrize("mode", ["tenant", "organization"])
def test_close_rejects_out_of_scope_actor(rigs, mode: str, kind: str):
    rig_a = rigs
    with connect() as conn, conn.transaction():
        human = rig_a.insert_user(conn)
        seeded = rig_a.scope.seed_chain(conn, title=f"关闭链-{mode}-{kind}", confirmed_judgment=True)
        chain_id = seeded["chain"]["chain_id"]
        issue_id = seeded["issue"]["object_id"]
        agreement = working.create_agreement(
            conn,
            chain_id=chain_id,
            issue_id=issue_id,
            statement="关闭共识",
            disposition="close",
            confirmed_judgment_record_id=seeded["judgment"]["record_id"],
            party_ids=[human],
            created_by=human,
        )
        working.confirm_agreement(conn, agreement["record_id"], confirmer=human)
        outsider = rig_a.insert_user_in(conn, _foreign(rig_a, mode), kind=kind)
        before = _chain_counts(conn, chain_id)
        with pytest.raises(working.ActorOutOfScopeError):
            working.create_close(
                conn,
                chain_id=chain_id,
                issue_id=issue_id,
                reason="越权关闭",
                assumptions="不应落库",
                monitor="不应落库",
                reopen_condition="不应落库",
                agreement_record_id=agreement["record_id"],
                created_by=outsider,
            )
        assert _chain_counts(conn, chain_id) == before
        assert _outsider_writes(conn, outsider) == 0


def test_external_refs_from_foreign_scope_rejected(rigs):
    """外部关联对象（signal/issue/judgment/agreement）跨 scope 的 scoped lookup 负向。"""
    rig_a = rigs
    scope_b = Scope()
    try:
        with connect() as conn, conn.transaction():
            human_a = rig_a.insert_user(conn)
            seeded_a = rig_a.scope.seed_chain(conn, title="本域链", confirmed_judgment=True)
            chain_a = seeded_a["chain"]["chain_id"]
            issue_a = seeded_a["issue"]["object_id"]

            # 异 scope 种子链（含 confirmed judgment 与 confirmed agreement）。
            seeded_b = scope_b.seed_chain(conn, title="外部链", confirmed_judgment=True)
            human_b = scope_b.user_id
            agreement_b = working.create_agreement(
                conn,
                chain_id=seeded_b["chain"]["chain_id"],
                issue_id=seeded_b["issue"]["object_id"],
                statement="外部共识",
                disposition="strategic_mission",
                confirmed_judgment_record_id=seeded_b["judgment"]["record_id"],
                party_ids=[human_b],
                created_by=human_b,
            )
            working.confirm_agreement(conn, agreement_b["record_id"], confirmer=human_b)
            before = _chain_counts(conn, chain_a)

            # 跨 scope 引用与“不存在”走同类泛化错误且文案不含外部 ID：不泄露外部
            # chain_id / object_type / status / disposition 等任何元数据。
            foreign_signal = seeded_b["signal"]["object_id"]
            with pytest.raises(working.ObjectNotFoundError) as sig_cross:
                working.form_issue(
                    conn,
                    chain_id=chain_a,
                    signal_object_ids=[foreign_signal],
                    key_question="引用外部信号？",
                    rationale="不应落库",
                    created_by=human_a,
                )
            with pytest.raises(working.ObjectNotFoundError) as sig_missing:
                working.form_issue(
                    conn,
                    chain_id=chain_a,
                    signal_object_ids=[str(uuid.uuid4())],
                    key_question="引用不存在信号？",
                    rationale="不应落库",
                    created_by=human_a,
                )
            assert str(sig_cross.value) == str(sig_missing.value)
            assert foreign_signal not in str(sig_cross.value)

            foreign_issue = seeded_b["issue"]["object_id"]
            with pytest.raises(working.ObjectNotFoundError) as issue_cross:
                working.create_judgment(
                    conn,
                    chain_id=chain_a,
                    issue_id=foreign_issue,
                    statement="引用外部议题",
                    responsible_party="不应落库",
                    created_by=human_a,
                )
            with pytest.raises(working.ObjectNotFoundError) as issue_missing:
                working.create_judgment(
                    conn,
                    chain_id=chain_a,
                    issue_id=str(uuid.uuid4()),
                    statement="引用不存在议题",
                    responsible_party="不应落库",
                    created_by=human_a,
                )
            assert str(issue_cross.value) == str(issue_missing.value)
            assert foreign_issue not in str(issue_cross.value)

            foreign_judgment = seeded_b["judgment"]["record_id"]
            with pytest.raises(working.VersionNotFoundError) as jud_cross:
                working.create_agreement(
                    conn,
                    chain_id=chain_a,
                    issue_id=issue_a,
                    statement="引用外部判断",
                    disposition="strategic_mission",
                    confirmed_judgment_record_id=foreign_judgment,
                    party_ids=[human_a],
                    created_by=human_a,
                )
            with pytest.raises(working.VersionNotFoundError) as jud_missing:
                working.create_agreement(
                    conn,
                    chain_id=chain_a,
                    issue_id=issue_a,
                    statement="引用不存在判断",
                    disposition="strategic_mission",
                    confirmed_judgment_record_id=str(uuid.uuid4()),
                    party_ids=[human_a],
                    created_by=human_a,
                )
            assert str(jud_cross.value) == str(jud_missing.value)
            assert foreign_judgment not in str(jud_cross.value)

            with pytest.raises(working.VersionNotFoundError) as agr_cross:
                working.create_mission(
                    conn,
                    chain_id=chain_a,
                    issue_id=issue_a,
                    title="引用外部共识",
                    outcome="不应落库",
                    owner="不应落库",
                    boundary="不应落库",
                    agreement_record_id=agreement_b["record_id"],
                    created_by=human_a,
                )
            with pytest.raises(working.VersionNotFoundError) as agr_missing:
                working.create_mission(
                    conn,
                    chain_id=chain_a,
                    issue_id=issue_a,
                    title="引用不存在共识",
                    outcome="不应落库",
                    owner="不应落库",
                    boundary="不应落库",
                    agreement_record_id=str(uuid.uuid4()),
                    created_by=human_a,
                )
            assert str(agr_cross.value) == str(agr_missing.value)
            assert agreement_b["record_id"] not in str(agr_cross.value)

            assert _chain_counts(conn, chain_a) == before
    finally:
        scope_b.cleanup()


def test_in_scope_agent_service_may_propose_but_not_confirm(rigs):
    rig_a = rigs
    with connect() as conn, conn.transaction():
        human = rig_a.insert_user(conn)
        agent = rig_a.insert_user(conn, kind="agent_service", display="p0a-agent")
        seeded = rig_a.scope.seed_chain(conn, title="agent 链", confirmed_judgment=False)
        chain_id = seeded["chain"]["chain_id"]

        # 同 scope agent_service 可以发起普通提案。
        signal = working.create_signal(
            conn, chain_id=chain_id, content=dict(_SIGNAL_CONTENT), created_by=agent
        )
        assert signal["object_id"]
        appended = working.append_version(
            conn,
            signal["object_id"],
            content={"title": "agent 修订", "description": "agent 提案"},
            created_by=agent,
        )
        assert appended["version"] == 2

        # 确认路径仍只允许 human。
        with pytest.raises(working.NotHumanApproverError):
            working.confirm_judgment(conn, seeded["judgment"]["record_id"], confirmed_by=agent)
        # human 确认不受影响（正向对照）。
        working.confirm_judgment(conn, seeded["judgment"]["record_id"], confirmed_by=human)


@pytest.mark.parametrize("kind", ["human", "agent_service"])
def test_cross_scope_target_indistinguishable_from_missing(rigs, kind: str):
    """合法 actor 指向跨 scope 目标：与指向随机不存在目标同类同文，不泄露任何元数据。"""
    rig_a = rigs
    scope_b = Scope()
    try:
        with connect() as conn, conn.transaction():
            human_a = rig_a.insert_user(conn)
            actor = (
                human_a
                if kind == "human"
                else rig_a.insert_user(conn, kind="agent_service", display=f"p0a-agent-{kind}")
            )
            seeded_b = scope_b.seed_chain(conn, title=f"外部目标链-{kind}", confirmed_judgment=True)
            chain_b = seeded_b["chain"]["chain_id"]
            judgment_b = seeded_b["judgment"]["record_id"]
            before_b = _chain_counts(conn, chain_b)

            # 提案路径：外部 chain 与随机 chain 同文。
            with pytest.raises(working.ActorOutOfScopeError) as cross:
                working.create_signal(
                    conn, chain_id=chain_b, content=dict(_SIGNAL_CONTENT), created_by=actor
                )
            with pytest.raises(working.ActorOutOfScopeError) as missing:
                working.create_signal(
                    conn,
                    chain_id=str(uuid.uuid4()),
                    content=dict(_SIGNAL_CONTENT),
                    created_by=actor,
                )
            assert str(cross.value) == str(missing.value)
            assert chain_b not in str(cross.value)

            # 确认路径（human）：外部版本行与随机版本行同文，不泄露 type/status。
            with pytest.raises(working.ActorOutOfScopeError) as cross_v:
                working.confirm_judgment(conn, judgment_b, confirmed_by=human_a)
            with pytest.raises(working.ActorOutOfScopeError) as missing_v:
                working.confirm_judgment(conn, str(uuid.uuid4()), confirmed_by=human_a)
            assert str(cross_v.value) == str(missing_v.value)
            assert judgment_b not in str(cross_v.value)

            # 外部链零写入。
            assert _chain_counts(conn, chain_b) == before_b
    finally:
        scope_b.cleanup()


def test_invalid_uuid_actor_is_domain_error(rigs):
    """非法 UUID actor 转稳定领域错误（非 psycopg InvalidTextRepresentation），且零写入。"""
    rig_a = rigs
    with connect() as conn, conn.transaction():
        rig_a.insert_user(conn)
        seeded = rig_a.scope.seed_chain(conn, title="invalid-uuid 链", confirmed_judgment=False)
        chain_id = seeded["chain"]["chain_id"]
        before = _chain_counts(conn, chain_id)

        with pytest.raises(working.ActorOutOfScopeError, match="不是合法的用户标识"):
            working.create_signal(
                conn, chain_id=chain_id, content=dict(_SIGNAL_CONTENT), created_by="not-a-uuid"
            )
        with pytest.raises(working.ActorOutOfScopeError, match="不是合法的用户标识"):
            working.confirm_judgment(
                conn, seeded["judgment"]["record_id"], confirmed_by="not-a-uuid"
            )
        # 领域异常后同一事务仍可继续读（未被服务端类型错误毒化）。
        assert _chain_counts(conn, chain_id) == before


def test_malformed_reference_ids_are_generic_domain_errors(rigs):
    """四类关联目标的非法 UUID：与 missing 同类同文、不送 PG、事务可继续、零部分写入。"""
    rig_a = rigs
    with connect() as conn, conn.transaction():
        human = rig_a.insert_user(conn)
        seeded = rig_a.scope.seed_chain(conn, title="malformed-ref 链", confirmed_judgment=True)
        chain_id = seeded["chain"]["chain_id"]
        issue_id = seeded["issue"]["object_id"]
        judgment_record_id = seeded["judgment"]["record_id"]
        agreement = working.create_agreement(
            conn,
            chain_id=chain_id,
            issue_id=issue_id,
            statement="承接共识",
            disposition="strategic_mission",
            confirmed_judgment_record_id=judgment_record_id,
            party_ids=[human],
            created_by=human,
        )
        working.confirm_agreement(conn, agreement["record_id"], confirmer=human)
        before = _chain_counts(conn, chain_id)

        def _assert_generic(missing_exc, bad_exc) -> None:
            # 非法目标与随机不存在目标同类同文，且不泄露任何标识。
            assert str(missing_exc.value) == str(bad_exc.value)
            assert "not-a-uuid" not in str(bad_exc.value)
            # Python 侧预校验：InvalidTextRepresentation 不送 PG，事务未被毒化。
            assert conn.execute("SELECT 1").fetchone()[0] == 1

        # 1) issue 引用（create_judgment → _validate_issue_ref）
        with pytest.raises(working.ObjectNotFoundError) as issue_bad:
            working.create_judgment(
                conn, chain_id=chain_id, issue_id="not-a-uuid",
                statement="x", responsible_party="y", created_by=human,
            )
        with pytest.raises(working.ObjectNotFoundError) as issue_missing:
            working.create_judgment(
                conn, chain_id=chain_id, issue_id=str(uuid.uuid4()),
                statement="x", responsible_party="y", created_by=human,
            )
        _assert_generic(issue_missing, issue_bad)

        # 2) signal 引用（form_issue 的 formation 来源）
        with pytest.raises(working.ObjectNotFoundError) as signal_bad:
            working.form_issue(
                conn, chain_id=chain_id, signal_object_ids=["not-a-uuid"],
                key_question="q?", rationale="r", created_by=human,
            )
        with pytest.raises(working.ObjectNotFoundError) as signal_missing:
            working.form_issue(
                conn, chain_id=chain_id, signal_object_ids=[str(uuid.uuid4())],
                key_question="q?", rationale="r", created_by=human,
            )
        _assert_generic(signal_missing, signal_bad)

        # 3) confirmed judgment 引用（create_agreement）
        with pytest.raises(working.VersionNotFoundError) as judgment_bad:
            working.create_agreement(
                conn, chain_id=chain_id, issue_id=issue_id, statement="s",
                disposition="strategic_mission", confirmed_judgment_record_id="not-a-uuid",
                party_ids=[human], created_by=human,
            )
        with pytest.raises(working.VersionNotFoundError) as judgment_missing:
            working.create_agreement(
                conn, chain_id=chain_id, issue_id=issue_id, statement="s",
                disposition="strategic_mission", confirmed_judgment_record_id=str(uuid.uuid4()),
                party_ids=[human], created_by=human,
            )
        _assert_generic(judgment_missing, judgment_bad)

        # 4) confirmed agreement 引用（create_mission）
        with pytest.raises(working.VersionNotFoundError) as agreement_bad:
            working.create_mission(
                conn, chain_id=chain_id, issue_id=issue_id, title="t", outcome="o",
                owner="w", boundary="b", agreement_record_id="not-a-uuid", created_by=human,
            )
        with pytest.raises(working.VersionNotFoundError) as agreement_missing:
            working.create_mission(
                conn, chain_id=chain_id, issue_id=issue_id, title="t", outcome="o",
                owner="w", boundary="b", agreement_record_id=str(uuid.uuid4()), created_by=human,
            )
        _assert_generic(agreement_missing, agreement_bad)

        assert _chain_counts(conn, chain_id) == before


def test_reference_validation_holds_target_lock(rigs):
    """两连接 + 短 lock_timeout：关联目标校验查询确实持有目标行锁（FOR SHARE）。"""
    rig_a = rigs
    with connect() as conn, conn.transaction():
        rig_a.insert_user(conn)
        seeded = rig_a.scope.seed_chain(conn, title="锁证明链", confirmed_judgment=True)
        chain_id = seeded["chain"]["chain_id"]
        issue_id = seeded["issue"]["object_id"]
        judgment_record_id = seeded["judgment"]["record_id"]
    # 种子已提交；conn_a 的校验在其隐式事务内持有 FOR SHARE。
    conn_a = connect()
    conn_b = connect()
    try:
        working._validate_confirmed_judgment_ref(
            conn_a, judgment_record_id, chain_id=chain_id, issue_id=issue_id
        )
        conn_b.execute("SET lock_timeout = '500ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            conn_b.execute(
                "SELECT record_id FROM wm_object_versions WHERE record_id=%s FOR UPDATE",
                (judgment_record_id,),
            )
        conn_b.rollback()
        # conn_a 提交释放锁后，conn_b 的 FOR UPDATE 立即成功。
        conn_a.commit()
        row = conn_b.execute(
            "SELECT record_id FROM wm_object_versions WHERE record_id=%s FOR UPDATE",
            (judgment_record_id,),
        ).fetchone()
        assert row is not None
    finally:
        conn_a.close()
        conn_b.close()


def _urn(u: str) -> str:
    return f"urn:uuid:{u}"


def _nohyphen(u: str) -> str:
    return u.replace("-", "")


def test_canonical_uuid_bound_in_sql(rigs):
    """非规范但合法的 UUID 表示（urn 前缀/无连字符）进入 SQL 时必须绑定 canonical uuid.UUID。

    覆盖全写路径：建链 created_by、追加 Signal 的 chain_id+created_by、formation 的
    signal ref、Judgment 的 issue ref 与 confirm 的 record+confirmed_by、Agreement 的
    judgment/issue ref + party + added_by（canonical 去重）、逐人确认的 confirmer、
    append_version 的 object+created_by、Issue 推进的 confirmed_by。
    """
    rig_a = rigs
    with connect() as conn, conn.transaction():
        human = rig_a.insert_user(conn)
        seeded = rig_a.scope.seed_chain(conn, title="canonical 链", confirmed_judgment=True)
        scope_human = rig_a.scope.user_id
        chain_id = seeded["chain"]["chain_id"]
        issue_id = seeded["issue"]["object_id"]
        judgment_record_id = seeded["judgment"]["record_id"]

        rec = RecordingConn(conn)
        # 建链 + 首 Signal：created_by 用 urn 形式（PG 不接受 urn，必须 Python 规范化）。
        chain2 = working.create_chain_with_first_signal(
            rec,
            title="canonical 链 2",
            signal_content=dict(_SIGNAL_CONTENT),
            created_by=_urn(human),
            tenant_id=rig_a.tenant_id,
            organization_id=rig_a.organization_id,
        )
        # 追加 Signal：chain_id 无连字符、created_by urn。
        signal2 = working.create_signal(
            rec,
            chain_id=_nohyphen(chain2["chain_id"]),
            content=dict(_SIGNAL_CONTENT),
            created_by=_urn(human),
        )
        # formation：signal ref 用 urn 形式。
        issue2 = working.form_issue(
            rec,
            chain_id=chain2["chain_id"],
            signal_object_ids=[_urn(signal2["object_id"])],
            key_question="canonical 议题？",
            rationale="canonical",
            created_by=human,
        )
        # Judgment：issue ref 无连字符；confirm 用 urn record + urn confirmed_by。
        judgment2 = working.create_judgment(
            rec,
            chain_id=chain2["chain_id"],
            issue_id=_nohyphen(issue2["object_id"]),
            statement="canonical 判断",
            responsible_party="测试责任人",
            created_by=human,
        )
        working.confirm_judgment(rec, _urn(judgment2["record_id"]), confirmed_by=_urn(human))
        # Agreement：judgment ref 无连字符、issue ref urn、party 同一人两种表示（canonical
        # 去重为一个 party）、added_by 无连字符。
        agreement = working.create_agreement(
            rec,
            chain_id=chain_id,
            issue_id=_urn(issue_id),
            statement="canonical 共识",
            disposition="strategic_mission",
            confirmed_judgment_record_id=_nohyphen(judgment_record_id),
            party_ids=[_urn(scope_human), scope_human],
            created_by=scope_human,
            added_by=_nohyphen(scope_human),
        )
        assert agreement["party_ids"] == [scope_human]
        # 逐人确认：confirmer 用 urn 形式，不得被误判为 NotAPartyError。
        confirmed = working.confirm_agreement(rec, agreement["record_id"], confirmer=_urn(scope_human))
        assert confirmed["is_complete"]
        # append_version：object 无连字符、created_by urn（版本 INSERT）。
        working.append_version(
            rec,
            _nohyphen(signal2["object_id"]),
            content=dict(_SIGNAL_CONTENT),
            created_by=_urn(human),
        )
        # Issue 推进：confirmed_by urn（新版本 INSERT 绑定 created_by/confirmed_by 两次）。
        working.advance_issue_to_strategic(rec, issue_id, confirmed_by=_urn(scope_human))

        flat = rec.bound_params()
        # 原始替代表示不得出现在任何绑定参数中。
        for raw in (
            _urn(human), _nohyphen(chain2["chain_id"]), _urn(signal2["object_id"]),
            _nohyphen(issue2["object_id"]), _urn(judgment2["record_id"]),
            _nohyphen(judgment_record_id), _urn(issue_id), _urn(scope_human),
            _nohyphen(scope_human),
        ):
            assert raw not in flat
        # canonical uuid.UUID 对象实际进入 SQL 绑定。
        for canonical in (
            uuid.UUID(human), uuid.UUID(chain2["chain_id"]), uuid.UUID(signal2["object_id"]),
            uuid.UUID(issue2["object_id"]), uuid.UUID(judgment2["record_id"]),
            uuid.UUID(judgment_record_id), uuid.UUID(issue_id), uuid.UUID(scope_human),
        ):
            assert canonical in flat


def test_append_version_binds_canonical_refs(rigs):
    """append_version 显式 ref 的 urn 表示：校验/继承后一律 canonicalize 为 uuid.UUID 绑定。

    回归目标：旧实现在显式 confirmed_judgment_record_id/agreement_record_id 时把原始
    urn 字符串绑进 INSERT，PG uuid 拒绝并毒化事务。此处证明成功、bound params 无 raw、
    存储值正确、事务保持可读。
    """
    rig_a = rigs
    with connect() as conn, conn.transaction():
        scope_human = rig_a.scope.ensure_human(conn)
        # uq_wm_one_agreement_per_chain：SM 与 Close 两条承接链各自种子。
        seeded_a = rig_a.scope.seed_chain(conn, title="append-ref 链 A", confirmed_judgment=True)
        seeded_b = rig_a.scope.seed_chain(conn, title="append-ref 链 B", confirmed_judgment=True)

        # 链 A：Agreement（strategic_mission）确认后承接 Mission。
        agreement_sm = working.create_agreement(
            conn, chain_id=seeded_a["chain"]["chain_id"], issue_id=seeded_a["issue"]["object_id"],
            statement="承接共识 SM", disposition="strategic_mission",
            confirmed_judgment_record_id=seeded_a["judgment"]["record_id"],
            party_ids=[scope_human], created_by=scope_human,
        )
        working.confirm_agreement(conn, agreement_sm["record_id"], confirmer=scope_human)
        mission = working.create_mission(
            conn, chain_id=seeded_a["chain"]["chain_id"], issue_id=seeded_a["issue"]["object_id"],
            title="M", outcome="o", owner="me", boundary="b",
            agreement_record_id=agreement_sm["record_id"], created_by=scope_human,
        )
        # 链 B：Agreement（close）确认后承接 Close。
        agreement_close = working.create_agreement(
            conn, chain_id=seeded_b["chain"]["chain_id"], issue_id=seeded_b["issue"]["object_id"],
            statement="承接共识 Close", disposition="close",
            confirmed_judgment_record_id=seeded_b["judgment"]["record_id"],
            party_ids=[scope_human], created_by=scope_human,
        )
        working.confirm_agreement(conn, agreement_close["record_id"], confirmer=scope_human)
        close = working.create_close(
            conn, chain_id=seeded_b["chain"]["chain_id"], issue_id=seeded_b["issue"]["object_id"],
            reason="r", assumptions="a", monitor="m", reopen_condition="rc",
            agreement_record_id=agreement_close["record_id"], created_by=scope_human,
        )
        judgment_record_id = seeded_a["judgment"]["record_id"]

        rec = RecordingConn(conn)
        # Agreement append：显式 judgment ref 用 urn 形式（与当前值等价）。
        urn_judgment = _urn(judgment_record_id)
        new_agreement = working.append_version(
            rec, agreement_sm["object_id"], content=dict(agreement_sm["content"]),
            created_by=scope_human, confirmed_judgment_record_id=urn_judgment,
        )
        assert new_agreement["version"] == 2
        # Strategic Mission / Close append：显式 agreement ref 用 urn 形式。
        urn_sm = _urn(agreement_sm["record_id"])
        urn_close = _urn(agreement_close["record_id"])
        new_mission = working.append_version(
            rec, mission["object_id"], content=dict(mission["content"]),
            created_by=scope_human, agreement_record_id=urn_sm,
        )
        new_close = working.append_version(
            rec, close["object_id"], content=dict(close["content"]),
            created_by=scope_human, agreement_record_id=urn_close,
        )

        flat = rec.bound_params()
        for raw in (urn_judgment, urn_sm, urn_close):
            assert raw not in flat
        for canonical in (
            uuid.UUID(judgment_record_id), uuid.UUID(agreement_sm["record_id"]),
            uuid.UUID(agreement_close["record_id"]),
        ):
            assert canonical in flat

        # 存储值正确：新版本 ref 列为 canonical uuid；事务未被毒化（这些 SELECT 即可读）。
        stored = conn.execute(
            "SELECT confirmed_judgment_record_id FROM wm_object_versions WHERE record_id=%s",
            (new_agreement["record_id"],),
        ).fetchone()[0]
        assert str(stored) == judgment_record_id
        stored = conn.execute(
            "SELECT agreement_record_id FROM wm_object_versions WHERE record_id=%s",
            (new_mission["record_id"],),
        ).fetchone()[0]
        assert str(stored) == agreement_sm["record_id"]
        stored = conn.execute(
            "SELECT agreement_record_id FROM wm_object_versions WHERE record_id=%s",
            (new_close["record_id"],),
        ).fetchone()[0]
        assert str(stored) == agreement_close["record_id"]


def _ref_kwargs() -> dict:
    excerpt = "冻结来源摘录"
    return {
        "fragment_id": str(uuid.uuid4()),
        "ordinal": 1,
        "excerpt_snapshot": excerpt,
        "content_hash_snapshot": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
    }


def _source_ref_count(conn) -> int:
    return conn.execute("SELECT count(*) FROM wm_version_source_refs").fetchone()[0]


def test_attach_source_ref_allows_in_scope_human_and_agent(rigs):
    rig_a = rigs
    with connect() as conn, conn.transaction():
        human = rig_a.insert_user(conn)
        agent = rig_a.insert_user(conn, kind="agent_service", display="p0a-attach-agent")
        seeded = rig_a.scope.seed_chain(conn, title="attach 链", confirmed_judgment=False)
        record_id = seeded["judgment"]["record_id"]

        ref1 = working.attach_source_ref(conn, record_id, actor=human, **_ref_kwargs())
        assert ref1["record_id"] == record_id
        kwargs = _ref_kwargs()
        kwargs["ordinal"] = 2  # uq_wm_vsr_ordinal：同 record 内 ordinal 唯一
        ref2 = working.attach_source_ref(conn, record_id, actor=agent, **kwargs)
        assert ref2["fragment_id"] == kwargs["fragment_id"]
        count = conn.execute(
            "SELECT count(*) FROM wm_version_source_refs WHERE chain_id=%s",
            (seeded["chain"]["chain_id"],),
        ).fetchone()[0]
        assert count == 2


@pytest.mark.parametrize("mode", ["tenant", "organization"])
def test_attach_source_ref_out_of_scope_actor_generic_and_zero_write(rigs, mode: str):
    """跨 tenant/跨 org actor：existing 与 missing target 同类同文且零写。"""
    rig_a = rigs
    with connect() as conn, conn.transaction():
        rig_a.insert_user(conn)
        seeded = rig_a.scope.seed_chain(conn, title=f"attach 越权链-{mode}", confirmed_judgment=False)
        record_id = seeded["judgment"]["record_id"]
        outsider = rig_a.insert_user_in(conn, _foreign(rig_a, mode))
        before = _source_ref_count(conn)

        with pytest.raises(working.VersionNotFoundError) as cross:
            working.attach_source_ref(conn, record_id, actor=outsider, **_ref_kwargs())
        with pytest.raises(working.VersionNotFoundError) as missing:
            working.attach_source_ref(conn, str(uuid.uuid4()), actor=outsider, **_ref_kwargs())
        assert str(cross.value) == str(missing.value)
        # 不泄露 record ID / type / status 等元数据。
        assert record_id not in str(cross.value)
        assert _source_ref_count(conn) == before


def test_attach_source_ref_invalid_inputs_keep_transaction_readable(rigs):
    """非法 actor/record/fragment：领域错误、不送 PG、同一事务仍可读、零写。"""
    rig_a = rigs
    with connect() as conn, conn.transaction():
        human = rig_a.insert_user(conn)
        seeded = rig_a.scope.seed_chain(conn, title="attach 非法输入链", confirmed_judgment=False)
        record_id = seeded["judgment"]["record_id"]
        before = _source_ref_count(conn)

        with pytest.raises(working.VersionNotFoundError) as bad_actor:
            working.attach_source_ref(conn, record_id, actor="not-a-uuid", **_ref_kwargs())
        with pytest.raises(working.VersionNotFoundError) as bad_record:
            working.attach_source_ref(conn, "not-a-uuid", actor=human, **_ref_kwargs())
        # 非法 actor 与非法 record 同文泛化，不回显输入。
        assert str(bad_actor.value) == str(bad_record.value)
        assert "not-a-uuid" not in str(bad_actor.value)

        bad_fragment = _ref_kwargs()
        bad_fragment["fragment_id"] = "not-a-uuid"
        with pytest.raises(working.InvalidSourceRefError):
            working.attach_source_ref(conn, record_id, actor=human, **bad_fragment)

        # 每次失败后事务未被毒化，仍可继续读。
        assert conn.execute("SELECT 1").fetchone()[0] == 1
        assert _source_ref_count(conn) == before


def test_attach_source_ref_confirmed_version_error_hides_id(rigs):
    rig_a = rigs
    with connect() as conn, conn.transaction():
        human = rig_a.insert_user(conn)
        seeded = rig_a.scope.seed_chain(conn, title="attach confirmed 链", confirmed_judgment=True)
        record_id = seeded["judgment"]["record_id"]
        before = _source_ref_count(conn)
        with pytest.raises(working.SourceRefImmutableError) as excinfo:
            working.attach_source_ref(conn, record_id, actor=human, **_ref_kwargs())
        assert record_id not in str(excinfo.value)
        assert _source_ref_count(conn) == before
