-- 0017_dri_delivery: a governed DRI delivery loop with independent judgments.
-- No roles or grants are created here.  gov_work_item_state is mutable;
-- gov_delivery_acceptances and gov_outcome_assessments are append-only.

ALTER TABLE gov_objects DROP CONSTRAINT ck_gov_object_type;
ALTER TABLE gov_objects ADD CONSTRAINT ck_gov_object_type CHECK (
    object_type IN (
        'CompanyOutcome', 'BusinessCommitment', 'ExecutionCommitment',
        'FeedbackThread', 'ManagementAdjustment', 'Decision',
        'MetricObservation', 'EvidenceAsset', 'WorkItem', 'Deliverable'
    )
);

ALTER TABLE gov_objects DROP CONSTRAINT ck_gov_object_status;
ALTER TABLE gov_objects ADD CONSTRAINT ck_gov_object_status CHECK (
    lifecycle_status IN (
        'draft', 'offered', 'proposed', 'open', 'routed', 'accepted',
        'investigating', 'awaiting_acceptance', 'active', 'superseded',
        'applied', 'confirmed', 'closed', 'dismissed', 'recorded', 'stored',
        'in_progress', 'submitted', 'changes_requested', 'delivery_accepted'
    )
);

CREATE TABLE gov_work_item_state (
    object_id                     uuid PRIMARY KEY,
    scope_id                      uuid NOT NULL,
    work_item_revision_id          uuid NOT NULL,
    dri_assignment_id              uuid NOT NULL,
    acceptor_assignment_id         uuid NOT NULL,
    accepted_by                   uuid,
    accepted_at                   timestamptz,
    deliverable_object_id          uuid,
    submission_seq                integer NOT NULL DEFAULT 0,
    latest_submission_revision_id  uuid,
    latest_acceptance_id           uuid,
    updated_at                    timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_work_item_state_scope UNIQUE (scope_id, object_id),
    CONSTRAINT uq_gov_work_item_state_revision
        UNIQUE (scope_id, object_id, work_item_revision_id),
    CONSTRAINT fk_gov_work_item_revision
        FOREIGN KEY (scope_id, object_id, work_item_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_work_item_dri FOREIGN KEY (scope_id, dri_assignment_id)
        REFERENCES gov_role_assignments (scope_id, assignment_id),
    CONSTRAINT fk_gov_work_item_acceptor FOREIGN KEY (scope_id, acceptor_assignment_id)
        REFERENCES gov_role_assignments (scope_id, assignment_id),
    CONSTRAINT fk_gov_work_item_accepted_by
        FOREIGN KEY (scope_id, dri_assignment_id, accepted_by)
        REFERENCES gov_role_assignments (scope_id, assignment_id, principal_id),
    CONSTRAINT fk_gov_work_item_deliverable FOREIGN KEY (scope_id, deliverable_object_id)
        REFERENCES gov_objects (scope_id, object_id),
    CONSTRAINT fk_gov_work_item_submission
        FOREIGN KEY (scope_id, deliverable_object_id, latest_submission_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT ck_gov_work_item_accepted CHECK (
        (accepted_by IS NULL) = (accepted_at IS NULL)
    ),
    CONSTRAINT ck_gov_work_item_submission CHECK (
        (submission_seq = 0 AND deliverable_object_id IS NULL
            AND latest_submission_revision_id IS NULL AND latest_acceptance_id IS NULL)
        OR (submission_seq >= 1 AND accepted_by IS NOT NULL
            AND deliverable_object_id IS NOT NULL AND latest_submission_revision_id IS NOT NULL)
    )
);

CREATE TABLE gov_delivery_acceptances (
    acceptance_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id                 uuid NOT NULL,
    work_item_object_id      uuid NOT NULL,
    work_item_revision_id    uuid NOT NULL,
    deliverable_object_id    uuid NOT NULL,
    deliverable_revision_id  uuid NOT NULL,
    submission_seq          integer NOT NULL,
    payload_hash            text NOT NULL,
    verification_result     text NOT NULL,
    criterion_results       jsonb NOT NULL,
    review_note             text NOT NULL,
    verifier_assignment_id  uuid NOT NULL,
    verifier_principal_id   uuid NOT NULL,
    action_id               uuid NOT NULL,
    recorded_at             timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_delivery_acceptance_scope UNIQUE (scope_id, acceptance_id),
    CONSTRAINT uq_gov_delivery_acceptance_work_item
        UNIQUE (scope_id, work_item_object_id, acceptance_id),
    CONSTRAINT uq_gov_delivery_acceptance_submission UNIQUE (scope_id, deliverable_revision_id),
    CONSTRAINT fk_gov_delivery_acceptance_work_item
        FOREIGN KEY (scope_id, work_item_object_id, work_item_revision_id)
        REFERENCES gov_work_item_state (scope_id, object_id, work_item_revision_id),
    CONSTRAINT fk_gov_delivery_acceptance_deliverable
        FOREIGN KEY (scope_id, deliverable_object_id, deliverable_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_delivery_acceptance_verifier
        FOREIGN KEY (scope_id, verifier_assignment_id, verifier_principal_id)
        REFERENCES gov_role_assignments (scope_id, assignment_id, principal_id),
    CONSTRAINT fk_gov_delivery_acceptance_receipt
        FOREIGN KEY (scope_id, action_id)
        REFERENCES gov_action_receipts (scope_id, receipt_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_gov_delivery_acceptance_seq CHECK (submission_seq >= 1),
    CONSTRAINT ck_gov_delivery_acceptance_hash CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_gov_delivery_acceptance_result CHECK (
        verification_result IN ('accepted', 'changes_requested')
    ),
    CONSTRAINT ck_gov_delivery_acceptance_criteria CHECK (
        jsonb_typeof(criterion_results) = 'array' AND jsonb_array_length(criterion_results) >= 1
    ),
    CONSTRAINT ck_gov_delivery_acceptance_note CHECK (length(btrim(review_note)) >= 1)
);

ALTER TABLE gov_work_item_state ADD CONSTRAINT fk_gov_work_item_latest_acceptance
    FOREIGN KEY (scope_id, object_id, latest_acceptance_id)
    REFERENCES gov_delivery_acceptances (scope_id, work_item_object_id, acceptance_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE gov_outcome_assessments (
    assessment_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id                  uuid NOT NULL,
    outcome_object_id         uuid NOT NULL,
    outcome_revision_id       uuid NOT NULL,
    assessment_result         text NOT NULL,
    observation_revision_ids  jsonb NOT NULL,
    evidence_revision_ids     jsonb NOT NULL,
    delivery_acceptance_ids   jsonb NOT NULL DEFAULT '[]'::jsonb,
    assessment_note           text NOT NULL,
    assessor_assignment_id    uuid NOT NULL,
    assessor_principal_id     uuid NOT NULL,
    action_id                 uuid NOT NULL,
    recorded_at               timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_outcome_assessment_scope UNIQUE (scope_id, assessment_id),
    CONSTRAINT fk_gov_outcome_assessment_revision
        FOREIGN KEY (scope_id, outcome_object_id, outcome_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_outcome_assessment_assessor
        FOREIGN KEY (scope_id, assessor_assignment_id, assessor_principal_id)
        REFERENCES gov_role_assignments (scope_id, assignment_id, principal_id),
    CONSTRAINT fk_gov_outcome_assessment_receipt
        FOREIGN KEY (scope_id, action_id)
        REFERENCES gov_action_receipts (scope_id, receipt_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_gov_outcome_assessment_result CHECK (
        assessment_result IN ('achieved', 'not_achieved', 'inconclusive')
    ),
    CONSTRAINT ck_gov_outcome_assessment_observations CHECK (
        jsonb_typeof(observation_revision_ids) = 'array'
        AND jsonb_array_length(observation_revision_ids) >= 1
    ),
    CONSTRAINT ck_gov_outcome_assessment_evidence CHECK (
        jsonb_typeof(evidence_revision_ids) = 'array'
        AND jsonb_array_length(evidence_revision_ids) >= 1
    ),
    CONSTRAINT ck_gov_outcome_assessment_deliveries CHECK (
        jsonb_typeof(delivery_acceptance_ids) = 'array'
    ),
    CONSTRAINT ck_gov_outcome_assessment_note CHECK (length(btrim(assessment_note)) >= 1)
);

CREATE INDEX idx_gov_delivery_acceptances_work_item
    ON gov_delivery_acceptances (scope_id, work_item_object_id, recorded_at);
CREATE INDEX idx_gov_outcome_assessments_outcome
    ON gov_outcome_assessments (scope_id, outcome_object_id, recorded_at);

-- JSON arrays cannot carry ordinary FKs.  Resolve each exact revision in its
-- scope, and reject duplicate references rather than treating them as evidence.
CREATE FUNCTION gov_validate_outcome_assessment_refs()
RETURNS trigger LANGUAGE plpgsql AS $gov_validate_outcome_assessment_refs$
DECLARE ref jsonb;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM gov_objects
        WHERE scope_id=NEW.scope_id AND object_id=NEW.outcome_object_id
          AND object_type='CompanyOutcome'
    ) THEN
        RAISE EXCEPTION 'outcome assessment must reference an in-scope CompanyOutcome'
            USING ERRCODE='23503';
    END IF;
    IF (SELECT count(*) <> count(DISTINCT value)
        FROM jsonb_array_elements(NEW.observation_revision_ids))
        OR (SELECT count(*) <> count(DISTINCT value)
            FROM jsonb_array_elements(NEW.evidence_revision_ids))
        OR (SELECT count(*) <> count(DISTINCT value)
            FROM jsonb_array_elements(NEW.delivery_acceptance_ids)) THEN
        RAISE EXCEPTION 'outcome assessment references must be unique'
            USING ERRCODE='23514';
    END IF;
    FOR ref IN SELECT value FROM jsonb_array_elements(NEW.observation_revision_ids)
    LOOP
        IF jsonb_typeof(ref) <> 'string' OR NOT EXISTS (
            SELECT 1 FROM gov_object_revisions r
            JOIN gov_objects o ON (o.scope_id,o.object_id)=(r.scope_id,r.object_id)
            WHERE r.scope_id=NEW.scope_id AND r.revision_id=(ref #>> '{}')::uuid
              AND o.object_type='MetricObservation'
        ) THEN
            RAISE EXCEPTION 'outcome observation must reference an in-scope metric revision'
                USING ERRCODE='23503';
        END IF;
    END LOOP;
    FOR ref IN SELECT value FROM jsonb_array_elements(NEW.evidence_revision_ids)
    LOOP
        IF jsonb_typeof(ref) <> 'string' OR NOT EXISTS (
            SELECT 1 FROM gov_object_revisions r
            JOIN gov_objects o ON (o.scope_id,o.object_id)=(r.scope_id,r.object_id)
            WHERE r.scope_id=NEW.scope_id AND r.revision_id=(ref #>> '{}')::uuid
              AND o.object_type='EvidenceAsset'
        ) THEN
            RAISE EXCEPTION 'outcome evidence must reference an in-scope evidence revision'
                USING ERRCODE='23503';
        END IF;
    END LOOP;
    FOR ref IN SELECT value FROM jsonb_array_elements(NEW.delivery_acceptance_ids)
    LOOP
        IF jsonb_typeof(ref) <> 'string' OR NOT EXISTS (
            SELECT 1 FROM gov_delivery_acceptances
            WHERE scope_id=NEW.scope_id AND acceptance_id=(ref #>> '{}')::uuid
        ) THEN
            RAISE EXCEPTION 'outcome delivery reference must be an in-scope acceptance'
                USING ERRCODE='23503';
        END IF;
    END LOOP;
    RETURN NEW;
END
$gov_validate_outcome_assessment_refs$;

CREATE TRIGGER trg_gov_outcome_assessment_refs
    BEFORE INSERT ON gov_outcome_assessments
    FOR EACH ROW EXECUTE FUNCTION gov_validate_outcome_assessment_refs();

CREATE TRIGGER trg_gov_delivery_acceptances_append_only
    BEFORE UPDATE OR DELETE ON gov_delivery_acceptances
    FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
CREATE TRIGGER trg_gov_outcome_assessments_append_only
    BEFORE UPDATE OR DELETE ON gov_outcome_assessments
    FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();

ALTER TABLE gov_work_item_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_work_item_state FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_work_item_state_scope ON gov_work_item_state
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

ALTER TABLE gov_delivery_acceptances ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_delivery_acceptances FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_delivery_acceptances_scope ON gov_delivery_acceptances
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

ALTER TABLE gov_outcome_assessments ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_outcome_assessments FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_outcome_assessments_scope ON gov_outcome_assessments
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

COMMENT ON TABLE gov_work_item_state IS
    'DRI acceptance and current submission pointers; business criteria live in the frozen WorkItem revision.';
COMMENT ON TABLE gov_delivery_acceptances IS
    'Immutable exact-version delivery judgments; do not imply Outcome achievement or MF closure.';
COMMENT ON TABLE gov_outcome_assessments IS
    'Independent evidence-backed Outcome judgments; do not confirm or close any FeedbackThread.';
