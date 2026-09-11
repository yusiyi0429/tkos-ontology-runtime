"""A2 reader unit tests — real imports, monkeypatch on db/protocol helpers.

These tests do NOT replace ``sys.modules`` and do NOT install a fake SQL
parser.  They exercise the production ``a2_readers`` module end-to-end by
patching only the small set of data-access helpers the reader calls into:

* ``db.object_row`` / ``db.revision_row`` (the legacy same-domain path)
* ``db.authorize_domain`` (live policy read check)
* ``protocol.current_binding`` / ``protocol.require_read_support`` /
  ``protocol.read_metadata`` (binding + protocol gate)
* the reader's own raw ``_round_*`` / ``_load_*`` helpers (so we can
  supply deterministic head / revision / state rows).

Each test uses a tiny ``SimpleNamespace`` ``ctx`` and canonical UUID
strings.  All assertions use ``GovernedError`` explicitly.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from memory_service_runtime.governed import db, protocol
from memory_service_runtime.governed import a2_readers as r
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.profile import CONTRACT_A_PROTOCOL_ID


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

ROUND_OID = "00000000-0000-0000-0000-0000000000a1"
SUB_OID = "00000000-0000-0000-0000-0000000000b2"
SUB_RID = "00000000-0000-0000-0000-0000000000b3"
DOMAIN_B = "00000000-0000-0000-0000-0000000000bb"
DOMAIN_C = "00000000-0000-0000-0000-0000000000cc"
PRINCIPAL = "00000000-0000-0000-0000-0000000000d1"
ASSIGN_DRI = "00000000-0000-0000-0000-0000000000e1"
ASSIGN_CEO = "00000000-0000-0000-0000-0000000000e2"


def ctx(principal_id=PRINCIPAL, scope_id="scope-1"):
    return SimpleNamespace(principal_id=principal_id, scope_id=scope_id)


def round_head():
    return {
        "object_id": ROUND_OID, "scope_id": "scope-1",
        "object_type": "FormationRound", "domain_id": DOMAIN_C,
        "latest_revision_id": "00000000-0000-0000-0000-0000000000f1",
        "effective_revision_id": "00000000-0000-0000-0000-0000000000f1",
    }


def round_payload(members=None, ceo_aid=ASSIGN_CEO, ceo_pid=PRINCIPAL,
                  company_domain=DOMAIN_C):
    return {
        "members": members or [],
        "ceo_assignment_id": ceo_aid,
        "ceo_principal_id": ceo_pid,
        "company_domain_id": company_domain,
    }


def round_state(company_domain=DOMAIN_C):
    return {"scope_id": "scope-1", "object_id": ROUND_OID,
            "company_domain_id": company_domain}


def live_assignment(assignment_id=ASSIGN_DRI, domain_id=DOMAIN_B,
                    role="DOMAIN_DRI", principal_id=PRINCIPAL):
    return {
        "assignment_id": assignment_id, "scope_id": "scope-1",
        "domain_id": domain_id, "principal_id": principal_id,
        "role": role, "active": True, "valid_from": "2020-01-01",
        "valid_to": None,
        "principal_type": "human", "principal_active": True,
    }


def head_row(oid, otype="DomainSubmission", domain_id=DOMAIN_B):
    return {
        "object_id": oid, "scope_id": "scope-1",
        "object_type": otype, "domain_id": domain_id,
        "latest_revision_id": SUB_RID,
        "effective_revision_id": SUB_RID,
    }


def binding_row(oid):
    return {"object_id": oid, "scope_id": "scope-1",
            "protocol_id": CONTRACT_A_PROTOCOL_ID}


def install_raw(round_oid=ROUND_OID, head=None, rev=None, payload=None,
                state=None, members=None, ceo_aid=ASSIGN_CEO,
                ceo_pid=PRINCIPAL, company_domain=DOMAIN_C,
                formal=None):
    """Patch the reader's raw scoped loaders to return controlled dicts."""

    def _round_def(conn, scope_id, rid):
        if rid != round_oid:
            return None
        h = head or round_head()
        return {"head": h, "payload": payload if payload is not None else round_payload(
            members, ceo_aid, ceo_pid, company_domain),
                "latest_revision_id": h["latest_revision_id"]}

    def _round_state(conn, scope_id, rid):
        return state if state is not None else round_state(company_domain)

    def _formal(conn, scope_id, rid, domain_id):
        return formal

    def _load_head(conn, scope_id, oid):
        if oid == round_oid:
            return head or round_head()
        if oid == SUB_OID:
            return head_row(SUB_OID)
        return None

    def _load_revision(conn, scope_id, oid, rid):
        if rev is not None and oid == SUB_OID:
            return rev
        return None

    return patch.multiple(
        r,
        _round_definition_row=_round_def,
        _round_state_row=_round_state,
        _formal_pointer_row=_formal,
        _load_head=_load_head,
        _load_revision=_load_revision,
    )


# ---------------------------------------------------------------------------
# (1) _actor_current_slot — exact live Round slot resolution
# ---------------------------------------------------------------------------

def _setup_slot(current_assign=None, authorize_allowed=None):
    """Wire up raw loaders + current_assignment + db.authorize_domain.

    Returns ``(patches, current_assignment_fn, authorize_domain_fn)``; the
    caller MUST ``patches.start()`` and ``patches.stop()`` (try/finally) so
    no patch leaks into other tests.
    """
    patches = install_raw(
        members=[{"domain_id": DOMAIN_B, "dri_assignment_id": ASSIGN_DRI,
                  "dri_principal_id": PRINCIPAL}],
        company_domain=DOMAIN_C,
    )

    def _ca(conn, c, aid):
        if current_assign is None:
            return None
        if aid != current_assign["assignment_id"]:
            return None
        return current_assign

    def _az(conn, c, domain_id, action):
        if authorize_allowed is None:
            raise GovernedError("FORBIDDEN")
        return authorize_allowed

    return patches, _ca, _az


def test_slot_current_human_assigned_dri_allowed():
    """Live human DRI + matching authorize_domain → slot returned."""
    patches, ca, az = _setup_slot(
        current_assign=live_assignment(role="DOMAIN_DRI", domain_id=DOMAIN_B),
        authorize_allowed=[{"assignment_id": ASSIGN_DRI}],
    )
    patches.start()
    try:
        with patch.object(r, "current_assignment", side_effect=ca), \
             patch.object(db, "authorize_domain", side_effect=az):
            slot = r._actor_current_slot(object(), ctx(), ROUND_OID)
    finally:
        patches.stop()
    assert slot == {
        "domain_id": DOMAIN_B, "assignment_id": ASSIGN_DRI,
        "principal_id": PRINCIPAL,
        "responsibility_role": "area_accountable",
    }


def test_slot_revoked_designation_with_other_read_denied():
    """authorize_domain no longer lists the stored DRI assignment → None."""
    patches, ca, az = _setup_slot(
        current_assign=live_assignment(role="DOMAIN_DRI", domain_id=DOMAIN_B),
        authorize_allowed=[{"assignment_id": "00000000-0000-0000-0000-000000000099"}],
    )
    patches.start()
    try:
        with patch.object(r, "current_assignment", side_effect=ca), \
             patch.object(db, "authorize_domain", side_effect=az):
            slot = r._actor_current_slot(object(), ctx(), ROUND_OID)
    finally:
        patches.stop()
    assert slot is None


def test_slot_live_assignment_wrong_role_denied():
    """Stored DRI but live assignment is CEO → slot must be None (no OR)."""
    patches, ca, az = _setup_slot(
        current_assign=live_assignment(role="CEO", domain_id=DOMAIN_B),
        authorize_allowed=[{"assignment_id": ASSIGN_DRI}],
    )
    patches.start()
    try:
        with patch.object(r, "current_assignment", side_effect=ca), \
             patch.object(db, "authorize_domain", side_effect=az):
            slot = r._actor_current_slot(object(), ctx(), ROUND_OID)
    finally:
        patches.stop()
    assert slot is None


def test_slot_replacement_principal_denied():
    """Stored principal ≠ current principal → no slot, even if live assignment valid."""
    other = live_assignment(role="DOMAIN_DRI", domain_id=DOMAIN_B)
    other["principal_id"] = "00000000-0000-0000-0000-0000000000ff"
    patches, ca, az = _setup_slot(
        current_assign=other,
        authorize_allowed=[{"assignment_id": ASSIGN_DRI}],
    )
    patches.start()
    try:
        with patch.object(r, "current_assignment", side_effect=ca), \
             patch.object(db, "authorize_domain", side_effect=az):
            slot = r._actor_current_slot(object(), ctx(), ROUND_OID)
    finally:
        patches.stop()
    assert slot is None


def test_slot_replacement_assignment_same_principal_denied():
    """Same person re-appointed under a NEW assignment id: the stored slot's
    assignment_id is no longer live, so the slot must be None — the actor's
    fresh DRI assignment elsewhere in authorize_domain does NOT revive the
    stored designation."""
    patches, ca, az = _setup_slot(
        current_assign=None,  # stored ASSIGN_DRI is gone
        authorize_allowed=[{"assignment_id": "00000000-0000-0000-0000-000000000077"}],
    )
    patches.start()
    try:
        with patch.object(r, "current_assignment", side_effect=ca), \
             patch.object(db, "authorize_domain", side_effect=az):
            slot = r._actor_current_slot(object(), ctx(), ROUND_OID)
    finally:
        patches.stop()
    assert slot is None


def test_slot_current_company_ceo_allowed():
    """Actor holding the round's exact CEO slot in the company domain gets the
    company_decider slot; their DRI member slot must NOT match."""
    patches, ca, _az = _setup_slot(
        current_assign=live_assignment(assignment_id=ASSIGN_CEO,
                                       domain_id=DOMAIN_C, role="CEO"),
        authorize_allowed=[{"assignment_id": ASSIGN_CEO}],
    )

    def _az(conn, c, domain_id, action):
        # The DRI member domain B grants nothing; company domain C grants
        # the exact CEO assignment.
        if domain_id == DOMAIN_C:
            return [{"assignment_id": ASSIGN_CEO}]
        raise GovernedError("FORBIDDEN")

    patches.start()
    try:
        with patch.object(r, "current_assignment", side_effect=ca), \
             patch.object(db, "authorize_domain", side_effect=_az):
            slot = r._actor_current_slot(object(), ctx(), ROUND_OID)
    finally:
        patches.stop()
    assert slot == {
        "domain_id": DOMAIN_C, "assignment_id": ASSIGN_CEO,
        "principal_id": PRINCIPAL,
        "responsibility_role": "company_decider",
    }


# ---------------------------------------------------------------------------
# (2) A reads B's formal submission via target B domain
# ---------------------------------------------------------------------------

def test_visible_object_uses_target_domain_for_formal_pointer():
    """Actor in domain C reads B's submission: formal pointer is keyed by B."""
    sub_rev = {
        "revision_id": SUB_RID, "object_id": SUB_OID, "scope_id": "scope-1",
        "payload": {"domain_id": DOMAIN_B, "round_object_id": ROUND_OID,
                    "submitted_by": PRINCIPAL},
    }
    p = install_raw(
        head=head_row(SUB_OID, "DomainSubmission", DOMAIN_B),
        rev=sub_rev,
        members=[{"domain_id": DOMAIN_B, "dri_assignment_id": ASSIGN_DRI,
                  "dri_principal_id": PRINCIPAL}],
        company_domain=DOMAIN_C,
    )
    p.start()
    try:
        with patch.object(db, "object_row",
                          side_effect=GovernedError("NOT_FOUND")), \
             patch.object(db, "revision_row",
                          side_effect=GovernedError("NOT_FOUND")), \
             patch.object(r, "current_assignment",
                          return_value=live_assignment(role="DOMAIN_DRI",
                                                      domain_id=DOMAIN_B)), \
             patch.object(db, "authorize_domain",
                          return_value=[{"assignment_id": ASSIGN_DRI}]), \
             patch.object(protocol, "current_binding",
                          return_value=binding_row(SUB_OID)):

            def _formal(conn, scope_id, rid, domain_id):
                assert domain_id == DOMAIN_B, "formal pointer must be keyed by target B"
                return {"submission_object_id": SUB_OID,
                        "submission_revision_id": SUB_RID,
                        "domain_id": DOMAIN_B}
            with patch.object(r, "_formal_pointer", side_effect=_formal):
                head = r.visible_object(object(), ctx(), SUB_OID)
            assert head["object_id"] == SUB_OID
    finally:
        p.stop()


def test_visible_object_old_formal_revision_rejected():
    """A's CURRENT formal pointer points at a different revision → NOT_FOUND."""
    sub_rev = {
        "revision_id": SUB_RID, "object_id": SUB_OID, "scope_id": "scope-1",
        "payload": {"domain_id": DOMAIN_B, "round_object_id": ROUND_OID},
    }
    p = install_raw(
        head=head_row(SUB_OID, "DomainSubmission", DOMAIN_B),
        rev=sub_rev,
        members=[{"domain_id": DOMAIN_B, "dri_assignment_id": ASSIGN_DRI,
                  "dri_principal_id": PRINCIPAL}],
        company_domain=DOMAIN_C,
    )
    p.start()
    try:
        with patch.object(db, "object_row", side_effect=GovernedError("NOT_FOUND")), \
             patch.object(db, "revision_row",
                          side_effect=GovernedError("NOT_FOUND")), \
             patch.object(r, "current_assignment",
                          return_value=live_assignment(role="DOMAIN_DRI",
                                                      domain_id=DOMAIN_B)), \
             patch.object(db, "authorize_domain",
                          return_value=[{"assignment_id": ASSIGN_DRI}]), \
             patch.object(protocol, "current_binding",
                          return_value=binding_row(SUB_OID)):
            # Formal pointer says a *different* submission/revision is current.
            def _formal(conn, scope_id, rid, domain_id):
                return {"submission_object_id":
                        "00000000-0000-0000-0000-0000000000aa",
                        "submission_revision_id":
                        "00000000-0000-0000-0000-0000000000bb",
                        "domain_id": DOMAIN_B}
            with patch.object(r, "_formal_pointer", side_effect=_formal):
                with pytest.raises(GovernedError) as ei:
                    r.visible_object(object(), ctx(), SUB_OID)
            assert ei.value.code == "NOT_FOUND"
    finally:
        p.stop()


def test_visible_object_other_object_type_rejected():
    """Formal pointer references some other object (not the requested one)."""
    sub_rev = {
        "revision_id": SUB_RID, "object_id": SUB_OID, "scope_id": "scope-1",
        "payload": {"domain_id": DOMAIN_B, "round_object_id": ROUND_OID},
    }
    p = install_raw(
        head=head_row(SUB_OID, "DomainSubmission", DOMAIN_B),
        rev=sub_rev,
        members=[{"domain_id": DOMAIN_B, "dri_assignment_id": ASSIGN_DRI,
                  "dri_principal_id": PRINCIPAL}],
        company_domain=DOMAIN_C,
    )
    p.start()
    try:
        with patch.object(db, "object_row", side_effect=GovernedError("NOT_FOUND")), \
             patch.object(db, "revision_row",
                          side_effect=GovernedError("NOT_FOUND")), \
             patch.object(r, "current_assignment",
                          return_value=live_assignment(role="DOMAIN_DRI",
                                                      domain_id=DOMAIN_B)), \
             patch.object(db, "authorize_domain",
                          return_value=[{"assignment_id": ASSIGN_DRI}]), \
             patch.object(protocol, "current_binding",
                          return_value=binding_row(SUB_OID)):
            def _formal(conn, scope_id, rid, domain_id):
                return {"submission_object_id":
                        "00000000-0000-0000-0000-0000000000aa",
                        "submission_revision_id": SUB_RID,
                        "domain_id": DOMAIN_B}
            with patch.object(r, "_formal_pointer", side_effect=_formal):
                with pytest.raises(GovernedError) as ei:
                    r.visible_object(object(), ctx(), SUB_OID)
            assert ei.value.code == "NOT_FOUND"
    finally:
        p.stop()


# ---------------------------------------------------------------------------
# (3) published source R1 visible, private R2 / head hidden
# ---------------------------------------------------------------------------

def test_source_r1_published_visible_r2_private_hidden():
    src_oid = "00000000-0000-0000-0000-0000000000c1"
    rid_r1 = "00000000-0000-0000-0000-0000000000c2"
    rid_r2 = "00000000-0000-0000-0000-0000000000c3"
    head = {
        "object_id": src_oid, "scope_id": "scope-1",
        "object_type": "CompanyReference",
        "domain_id": DOMAIN_B,
        "latest_revision_id": rid_r2, "effective_revision_id": rid_r1,
    }

    def _load_head(conn, scope_id, oid):
        return head if oid == src_oid else None

    def _load_revision(conn, scope_id, oid, rid):
        if rid == rid_r1:
            return {"revision_id": rid_r1, "object_id": src_oid,
                    "scope_id": "scope-1",
                    "payload": {"shared_with_domain_ids": [DOMAIN_B]}}
        if rid == rid_r2:
            return {"revision_id": rid_r2, "object_id": src_oid,
                    "scope_id": "scope-1", "payload": {"shared_with_domain_ids": []}}
        return None

    with patch.object(r, "_load_head", side_effect=_load_head), \
         patch.object(r, "_load_revision", side_effect=_load_revision), \
         patch.object(r, "_rows",
                      side_effect=lambda conn, sql, params:
                          [{"assignment_id": ASSIGN_DRI, "domain_id": DOMAIN_B}]), \
         patch.object(db, "object_row", side_effect=GovernedError("NOT_FOUND")), \
         patch.object(db, "revision_row", side_effect=GovernedError("NOT_FOUND")), \
         patch.object(db, "authorize_domain",
                      return_value=[{"assignment_id": ASSIGN_DRI}]), \
         patch.object(protocol, "current_binding",
                      return_value=binding_row(src_oid)), \
         patch.object(protocol, "require_read_support",
                      return_value={"interpretation_status": "OK"}):
        # Direct R1 visible.
        v = r.visible_revision(object(), ctx(), src_oid, rid_r1)
        assert v["revision_id"] == rid_r1
        # Direct R2 (private) hidden.
        with pytest.raises(GovernedError) as ei:
            r.visible_revision(object(), ctx(), src_oid, rid_r2)
        assert ei.value.code == "NOT_FOUND"
        # Head GET hidden because R2 not published.
        with pytest.raises(GovernedError) as ei2:
            r.visible_object(object(), ctx(), src_oid)
        assert ei2.value.code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# (4) _actor_current_read_domains — policy withdrawal removes old read
# ---------------------------------------------------------------------------

def test_actor_current_read_domains_latest_policy_withdrawal():
    """Latest activation policy denies the actor even though DB rows say yes."""
    candidates = [
        {"assignment_id": ASSIGN_DRI, "domain_id": DOMAIN_B},
        {"assignment_id": ASSIGN_CEO, "domain_id": DOMAIN_C},
    ]

    def _rows(conn, sql, params):
        return candidates

    def _az(conn, c, domain_id, action):
        # Latest policy: B denied, C allowed.
        if domain_id == DOMAIN_B:
            raise GovernedError("FORBIDDEN")
        return [{"assignment_id": ASSIGN_CEO}]

    with patch.object(r, "_rows", side_effect=_rows), \
         patch.object(db, "authorize_domain", side_effect=_az):
        allowed = r._actor_current_read_domains(object(), ctx())
    assert allowed == {DOMAIN_C}


def test_actor_current_read_domains_skips_when_authorize_denies():
    candidates = [{"assignment_id": ASSIGN_DRI, "domain_id": DOMAIN_B}]

    def _az(conn, c, domain_id, action):
        raise GovernedError("FORBIDDEN")

    with patch.object(r, "_rows", return_value=candidates), \
         patch.object(db, "authorize_domain", side_effect=_az):
        assert r._actor_current_read_domains(object(), ctx()) == set()


# ---------------------------------------------------------------------------
# (5) _domain_commitment_allowed — nested composition_ref + activation.detail
# ---------------------------------------------------------------------------

def test_domain_commitment_matches_exact_triple():
    activation = {
        "composition_object_id": "00000000-0000-0000-0000-0000000000a1",
        "composition_revision_id": "00000000-0000-0000-0000-0000000000a2",
        "manifest_hash": "hash-XYZ",
        "detail": {
            "domain_commitments": [
                {"domain_id": DOMAIN_B, "object_id": SUB_OID,
                 "revision_id": SUB_RID},
            ],
        },
    }
    payload = {
        "round_object_id": ROUND_OID,
        "domain_id": DOMAIN_B,
        "composition_ref": {
            "object_id": "00000000-0000-0000-0000-0000000000a1",
            "revision_id": "00000000-0000-0000-0000-0000000000a2",
            "manifest_hash": "hash-XYZ",
        },
    }
    assert r._domain_commitment_allowed(
        payload, activation, ROUND_OID, DOMAIN_B, SUB_OID, SUB_RID) is True


@pytest.mark.parametrize("bad_payload,label", [
    ({"round_object_id": ROUND_OID, "domain_id": DOMAIN_B,
      "composition_ref": {"object_id": "x", "revision_id": "y",
                           "manifest_hash": "z"}},
     "missing-triple"),
    ({"round_object_id": "00000000-0000-0000-0000-0000000000ff",
      "domain_id": DOMAIN_B, "composition_ref": {
          "object_id": "00000000-0000-0000-0000-0000000000a1",
          "revision_id": "00000000-0000-0000-0000-0000000000a2",
          "manifest_hash": "hash-XYZ"}}, "wrong-round"),
])
def test_domain_commitment_wrong_hash_or_revision_denied(bad_payload, label):
    activation = {
        "composition_object_id": "00000000-0000-0000-0000-0000000000a1",
        "composition_revision_id": "00000000-0000-0000-0000-0000000000a2",
        "manifest_hash": "hash-XYZ",
        "detail": {"domain_commitments": [
            {"domain_id": DOMAIN_B, "object_id": SUB_OID,
             "revision_id": SUB_RID}]},
    }
    if label == "missing-triple":
        # hash mismatch.
        bad_payload["round_object_id"] = ROUND_OID
    assert r._domain_commitment_allowed(
        bad_payload, activation, ROUND_OID, DOMAIN_B, SUB_OID, SUB_RID) is False


def test_domain_commitment_wrong_target_domain_denied():
    activation = {
        "composition_object_id": "00000000-0000-0000-0000-0000000000a1",
        "composition_revision_id": "00000000-0000-0000-0000-0000000000a2",
        "manifest_hash": "h",
        "detail": {"domain_commitments": [
            {"domain_id": DOMAIN_B, "object_id": SUB_OID,
             "revision_id": SUB_RID}]},
    }
    payload = {
        "round_object_id": ROUND_OID, "domain_id": DOMAIN_B,
        "composition_ref": {
            "object_id": "00000000-0000-0000-0000-0000000000a1",
            "revision_id": "00000000-0000-0000-0000-0000000000a2",
            "manifest_hash": "h"},
    }
    # commitment's own domain_id ≠ target_domain_id.
    assert r._domain_commitment_allowed(
        payload, activation, ROUND_OID, "00000000-0000-0000-0000-0000000000zz",
        SUB_OID, SUB_RID) is False


def test_domain_commitment_signature_requires_object_id_revision_id():
    """Three positional args after activation: target_domain, object_id, revision_id."""
    import inspect
    sig = inspect.signature(r._domain_commitment_allowed)
    params = list(sig.parameters.values())
    # commitment_payload, activation, round_object_id, target_domain_id,
    # object_id, revision_id
    assert [p.name for p in params[3:]] == [
        "target_domain_id", "object_id", "revision_id"]


# ---------------------------------------------------------------------------
# (6) authorize_receipt — ALL refs, no historical actor shortcut
# ---------------------------------------------------------------------------

def _wire_authorize(target_visible=True, refs_visible=True,
                    current_slot=None, ceo_history=False):
    def _authz(conn, c, oid, rid=None):
        if oid == "00000000-0000-0000-0000-0000000000aa":
            return target_visible
        return refs_visible
    slot = current_slot

    def _slot_resolver(conn, c, rid):
        return slot

    def _ceo(conn, c, rid):
        return ceo_history

    def _slot_pol(conn, c, slot_):
        return True

    return _authz, _slot_resolver, _ceo, _slot_pol


def test_authorize_receipt_first_visible_then_private_denies():
    """target visible, but a later referenced object is private → denied."""
    target = "00000000-0000-0000-0000-0000000000aa"
    private_ref = "00000000-0000-0000-0000-0000000000bb"

    def _authz(conn, c, oid, rid=None):
        if oid == target:
            return True
        if oid == private_ref:
            return False
        return True

    receipt = {
        "action_type": "open_formation_round",
        "target_object_id": target,
        "object_versions": [],
        "result": {"referenced_object_ids": [private_ref]},
    }
    with patch.object(r, "_authorize_object", side_effect=_authz), \
         patch.object(r, "_receipt_anchor_round", return_value=None):
        with pytest.raises(GovernedError) as ei:
            r.authorize_receipt(object(), ctx(), receipt)
    assert ei.value.code == "NOT_FOUND"


def test_authorize_receipt_source_action_without_round_uses_current_rights():
    """create_object receipts have no round anchor — pass on reference authz."""
    target = "00000000-0000-0000-0000-0000000000aa"
    receipt = {
        "action_type": "create_object",
        "target_object_id": target, "object_versions": [],
        "result": {"referenced_object_ids": []},
    }
    with patch.object(r, "_authorize_object", return_value=True), \
         patch.object(r, "_receipt_anchor_round", return_value=None):
        r.authorize_receipt(object(), ctx(), receipt)  # no raise


def test_authorize_receipt_revoked_signer_denied_even_for_own_history():
    """Historical actor id alone is not enough — slot must be live."""
    target = "00000000-0000-0000-0000-0000000000aa"
    receipt = {
        "action_type": "open_formation_round",
        "target_object_id": target, "object_versions": [],
        "result": {"referenced_object_ids": []},
    }
    # _authorize_object passes for the target, but no live slot.
    with patch.object(r, "_authorize_object", return_value=True), \
         patch.object(r, "_receipt_anchor_round", return_value=ROUND_OID), \
         patch.object(r, "_actor_current_slot", return_value=None), \
         patch.object(r, "_company_ceo_current_read", return_value=False):
        with pytest.raises(GovernedError) as ei:
            r.authorize_receipt(object(), ctx(), receipt)
    assert ei.value.code == "NOT_FOUND"


def test_authorize_receipt_ceo_history_exception_allowed_when_company_reads():
    target = "00000000-0000-0000-0000-0000000000aa"
    receipt = {
        "action_type": "confirm_company_composition",
        "target_object_id": target, "object_versions": [],
        "result": {"referenced_object_ids": []},
    }
    with patch.object(r, "_authorize_object", return_value=True), \
         patch.object(r, "_receipt_anchor_round", return_value=ROUND_OID), \
         patch.object(r, "_actor_current_slot", return_value=None), \
         patch.object(r, "_company_ceo_current_read", return_value=True):
        r.authorize_receipt(object(), ctx(), receipt)  # no raise


# ---------------------------------------------------------------------------
# (7) native legacy read delegates unaltered
# ---------------------------------------------------------------------------

def test_visible_object_delegates_to_db_for_non_a2_type():
    oid = "00000000-0000-0000-0000-0000000000d9"
    legacy = {"object_id": oid, "scope_id": "scope-1", "object_type": "Task"}
    with patch.object(r, "_load_head",
                      return_value={"object_type": "Task"}), \
         patch.object(db, "object_row", return_value=legacy):
        assert r.visible_object(object(), ctx(), oid) == legacy
        db.object_row.assert_called_once()


def test_visible_revision_delegates_to_db_for_native_read():
    """Legacy revision read path is untouched: a native same-domain read
    returns directly without ever consulting the A2 loaders."""
    oid = "00000000-0000-0000-0000-0000000000d9"
    rid = "00000000-0000-0000-0000-0000000000da"
    legacy_rev = {"revision_id": rid, "object_id": oid, "payload": {}}
    with patch.object(r, "_load_head",
                      side_effect=AssertionError("A2 fallback must not run")), \
         patch.object(db, "revision_row", return_value=legacy_rev):
        assert r.visible_revision(object(), ctx(), oid, rid) == legacy_rev
        db.revision_row.assert_called_once()


# ---------------------------------------------------------------------------
# (8) no silent fallback: unexpected errors propagate, never degrade
# ---------------------------------------------------------------------------

def test_visible_object_propagates_unexpected_db_error():
    """A non-authorization GovernedError from the native path must propagate
    verbatim — the A2 exception only covers NOT_FOUND/FORBIDDEN."""
    oid = "00000000-0000-0000-0000-0000000000d9"
    with patch.object(db, "object_row",
                      side_effect=GovernedError("INVALID_STATE")), \
         patch.object(r, "_load_head",
                      side_effect=AssertionError("no fallback allowed")):
        with pytest.raises(GovernedError) as ei:
            r.visible_object(object(), ctx(), oid)
    assert ei.value.code == "INVALID_STATE"


def test_visible_object_non_contract_a_binding_hidden():
    """A2 object type without a current Contract-A binding → NOT_FOUND."""
    oid = "00000000-0000-0000-0000-0000000000c1"
    with patch.object(r, "_load_head",
                      return_value={"object_id": oid, "scope_id": "scope-1",
                                    "object_type": "CompanyReference",
                                    "domain_id": DOMAIN_B}), \
         patch.object(db, "object_row", side_effect=GovernedError("NOT_FOUND")), \
         patch.object(protocol, "current_binding",
                      return_value={"object_id": oid, "protocol_id": "legacy/0"}):
        with pytest.raises(GovernedError) as ei:
            r.visible_object(object(), ctx(), oid)
    assert ei.value.code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# (9) cross-domain revision read keys the formal pointer on the TARGET domain
# ---------------------------------------------------------------------------

def _wire_submission_revision_read(formal):
    """Common wiring for a domain-B submission read by a domain-C actor."""
    sub_rev = {
        "revision_id": SUB_RID, "object_id": SUB_OID, "scope_id": "scope-1",
        "payload": {"domain_id": DOMAIN_B, "round_object_id": ROUND_OID},
    }
    return patch.object(r, "_load_head",
                        return_value=head_row(SUB_OID, "DomainSubmission",
                                              DOMAIN_B)), \
        patch.object(r, "_load_revision", return_value=sub_rev), \
        patch.object(db, "revision_row",
                     side_effect=GovernedError("NOT_FOUND")), \
        patch.object(protocol, "current_binding",
                     return_value=binding_row(SUB_OID)), \
        patch.object(r, "_actor_current_slot",
                     return_value={"domain_id": DOMAIN_B,
                                   "assignment_id": ASSIGN_DRI,
                                   "principal_id": PRINCIPAL,
                                   "responsibility_role": "area_accountable"}), \
        patch.object(r, "_slot_has_current_read_policy", return_value=True), \
        patch.object(r, "_formal_pointer", side_effect=formal)


def test_visible_revision_formal_pointer_exact_match_allowed():
    """Exact (object_id, revision_id) match on the target domain's CURRENT
    formal pointer makes the submission revision visible cross-domain."""
    calls = []

    def _formal(conn, scope_id, rid, domain_id):
        calls.append(domain_id)
        return {"submission_object_id": SUB_OID,
                "submission_revision_id": SUB_RID,
                "domain_id": DOMAIN_B}

    patches = _wire_submission_revision_read(_formal)
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6]:
        rev = r.visible_revision(object(), ctx(), SUB_OID, SUB_RID)
    assert rev["revision_id"] == SUB_RID
    # The pointer lookup must be keyed by the submission's OWN domain (B),
    # never by the actor's slot domain.
    assert calls == [DOMAIN_B]


def test_visible_revision_stale_formal_revision_denied():
    """The formal pointer moved to a newer revision: the old revision is
    NOT_FOUND even though the object itself is still the formal submission."""
    def _formal(conn, scope_id, rid, domain_id):
        return {"submission_object_id": SUB_OID,
                "submission_revision_id": "00000000-0000-0000-0000-0000000000ff",
                "domain_id": DOMAIN_B}

    patches = _wire_submission_revision_read(_formal)
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6]:
        with pytest.raises(GovernedError) as ei:
            r.visible_revision(object(), ctx(), SUB_OID, SUB_RID)
    assert ei.value.code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# (10) DomainCommitment nested composition_ref shape enforcement
# ---------------------------------------------------------------------------

def test_domain_commitment_ref_must_be_dict():
    activation = {
        "composition_object_id": "00000000-0000-0000-0000-0000000000a1",
        "composition_revision_id": "00000000-0000-0000-0000-0000000000a2",
        "manifest_hash": "hash-XYZ",
        "detail": {"domain_commitments": [
            {"domain_id": DOMAIN_B, "object_id": SUB_OID,
             "revision_id": SUB_RID}]},
    }
    base = {"round_object_id": ROUND_OID, "domain_id": DOMAIN_B}
    for bad_ref in (None, "a-string", ["x"]):
        payload = dict(base)
        if bad_ref is not None:
            payload["composition_ref"] = bad_ref
        assert r._domain_commitment_allowed(
            payload, activation, ROUND_OID, DOMAIN_B, SUB_OID, SUB_RID) is False


def test_domain_commitment_detail_triple_must_match_exactly():
    """The activation detail names this commitment under a DIFFERENT object
    id → not allowed, even though the composition_ref triple matches."""
    activation = {
        "composition_object_id": "00000000-0000-0000-0000-0000000000a1",
        "composition_revision_id": "00000000-0000-0000-0000-0000000000a2",
        "manifest_hash": "hash-XYZ",
        "detail": {"domain_commitments": [
            {"domain_id": DOMAIN_B,
             "object_id": "00000000-0000-0000-0000-000000000099",
             "revision_id": SUB_RID}]},
    }
    payload = {
        "round_object_id": ROUND_OID, "domain_id": DOMAIN_B,
        "composition_ref": {
            "object_id": "00000000-0000-0000-0000-0000000000a1",
            "revision_id": "00000000-0000-0000-0000-0000000000a2",
            "manifest_hash": "hash-XYZ"},
    }
    assert r._domain_commitment_allowed(
        payload, activation, ROUND_OID, DOMAIN_B, SUB_OID, SUB_RID) is False


# ---------------------------------------------------------------------------
# (11) authorize_receipt — every object_versions entry authorized; withdrawn
#      read policy denies; legacy receipts never touch the round anchor
# ---------------------------------------------------------------------------

def test_authorize_receipt_object_versions_entry_denied():
    """target passes, but one object_versions entry is private → NOT_FOUND."""
    denied = "00000000-0000-0000-0000-0000000000bb"

    def _authz(conn, c, oid, rid=None):
        return oid != denied

    receipt = {
        "action_type": "open_formation_round",
        "target_object_id": "00000000-0000-0000-0000-0000000000aa",
        "object_versions": [{"object_id": denied,
                             "revision_id": SUB_RID}],
        "result": {"referenced_object_ids": []},
    }
    with patch.object(r, "_authorize_object", side_effect=_authz):
        with pytest.raises(GovernedError) as ei:
            r.authorize_receipt(object(), ctx(), receipt)
    assert ei.value.code == "NOT_FOUND"


def test_authorize_receipt_slot_read_policy_withdrawn_denied():
    """Slot still resolves, but the latest read policy no longer grants the
    slot's domain → NOT_FOUND (no historic-policy fallback)."""
    receipt = {
        "action_type": "confirm_company_composition",
        "target_object_id": "00000000-0000-0000-0000-0000000000aa",
        "object_versions": [],
        "result": {"referenced_object_ids": []},
    }
    with patch.object(r, "_authorize_object", return_value=True), \
         patch.object(r, "_receipt_anchor_round", return_value=ROUND_OID), \
         patch.object(r, "_actor_current_slot",
                      return_value={"domain_id": DOMAIN_B,
                                    "assignment_id": ASSIGN_DRI,
                                    "principal_id": PRINCIPAL,
                                    "responsibility_role": "area_accountable"}), \
         patch.object(r, "_slot_has_current_read_policy", return_value=False):
        with pytest.raises(GovernedError) as ei:
            r.authorize_receipt(object(), ctx(), receipt)
    assert ei.value.code == "NOT_FOUND"


def test_authorize_receipt_legacy_action_never_resolves_round_anchor():
    """Non-A2 action types pass on reference authorization alone; the round
    anchor machinery must not run at all (legacy receipts unchanged)."""
    receipt = {
        "action_type": "accept_commitment",
        "target_object_id": "00000000-0000-0000-0000-0000000000aa",
        "object_versions": [],
        "result": {"referenced_object_ids": []},
    }
    with patch.object(r, "_authorize_object", return_value=True), \
         patch.object(r, "_receipt_anchor_round",
                      side_effect=AssertionError("must not run for legacy")):
        r.authorize_receipt(object(), ctx(), receipt)  # no raise
