-- 0015_runtime_tasks: infrastructure-only durable worker queue.
--
-- Migration numbers 0008-0014 remain reserved for the approved governance plan
-- (security/RLS, Action kernel, Evidence, and authority/lifecycle changes).  This
-- migration deliberately has no foreign key to, and grants no authority over,
-- Ontology, Semantic Memory, or Working Memory tables.  It is not a business
-- Action migration.

CREATE TABLE runtime_tasks (
    task_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          text NOT NULL,
    organization_id    text NOT NULL,
    task_type          text NOT NULL,
    idempotency_key    text NOT NULL,
    request_sha256     text NOT NULL,
    payload            jsonb NOT NULL DEFAULT '{}'::jsonb,
    state              text NOT NULL DEFAULT 'queued',
    attempt            integer NOT NULL DEFAULT 0,
    max_attempts       integer NOT NULL DEFAULT 5,
    available_at       timestamptz NOT NULL DEFAULT now(),
    lease_owner        text,
    lease_token        uuid,
    lease_expires_at   timestamptz,
    result             jsonb,
    error_code         text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    started_at         timestamptz,
    finished_at        timestamptz,
    updated_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_runtime_task_scope UNIQUE (task_id, tenant_id, organization_id),
    CONSTRAINT uq_runtime_tasks_idempotency
        UNIQUE (tenant_id, organization_id, idempotency_key),
    CONSTRAINT ck_runtime_task_scope CHECK (
        tenant_id = btrim(tenant_id) AND length(tenant_id) BETWEEN 1 AND 200
        AND organization_id = btrim(organization_id)
        AND length(organization_id) BETWEEN 1 AND 200
    ),
    CONSTRAINT ck_runtime_task_type CHECK (
        task_type = btrim(task_type) AND length(task_type) BETWEEN 1 AND 128
    ),
    CONSTRAINT ck_runtime_task_idempotency_key CHECK (
        idempotency_key = btrim(idempotency_key)
        AND length(idempotency_key) BETWEEN 1 AND 200
    ),
    CONSTRAINT ck_runtime_task_request_sha256 CHECK (
        request_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_runtime_task_payload_object CHECK (
        jsonb_typeof(payload) = 'object'
    ),
    CONSTRAINT ck_runtime_task_result_object CHECK (
        result IS NULL OR jsonb_typeof(result) = 'object'
    ),
    CONSTRAINT ck_runtime_task_state CHECK (
        state IN ('queued', 'in_progress', 'retryable', 'succeeded', 'failed')
    ),
    CONSTRAINT ck_runtime_task_attempts CHECK (
        max_attempts BETWEEN 1 AND 100
        AND attempt BETWEEN 0 AND max_attempts
    ),
    CONSTRAINT ck_runtime_task_error_code CHECK (
        error_code IS NULL OR (
            error_code = btrim(error_code) AND length(error_code) BETWEEN 1 AND 128
        )
    ),
    CONSTRAINT ck_runtime_task_lifecycle CHECK (
        (
            state = 'queued'
            AND attempt = 0
            AND lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL
            AND started_at IS NULL AND finished_at IS NULL
            AND result IS NULL AND error_code IS NULL
        ) OR (
            state = 'in_progress'
            AND attempt >= 1
            AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL
            AND started_at IS NOT NULL AND finished_at IS NULL
            AND result IS NULL AND error_code IS NULL
        ) OR (
            state = 'retryable'
            AND attempt >= 1 AND attempt < max_attempts
            AND lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL
            AND started_at IS NOT NULL AND finished_at IS NULL
            AND result IS NULL AND error_code IS NOT NULL
        ) OR (
            state = 'succeeded'
            AND attempt >= 1
            AND lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL
            AND started_at IS NOT NULL AND finished_at IS NOT NULL
            AND result IS NOT NULL AND error_code IS NULL
        ) OR (
            state = 'failed'
            AND attempt >= 1
            AND lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL
            AND started_at IS NOT NULL AND finished_at IS NOT NULL
            AND result IS NULL AND error_code IS NOT NULL
        )
    )
);

CREATE INDEX idx_runtime_tasks_claimable
    ON runtime_tasks (tenant_id, organization_id, available_at, created_at, task_id)
    WHERE state IN ('queued', 'retryable');

CREATE INDEX idx_runtime_tasks_expired_lease
    ON runtime_tasks (tenant_id, organization_id, lease_expires_at, task_id)
    WHERE state = 'in_progress';

CREATE FUNCTION enforce_runtime_task_transition()
RETURNS trigger
LANGUAGE plpgsql
AS $runtime_task_transition$
BEGIN
    IF OLD.state IN ('succeeded', 'failed') THEN
        RAISE EXCEPTION 'terminal runtime task is immutable'
            USING ERRCODE = '55000';
    END IF;

    IF NOT (
        (OLD.state = 'queued'      AND NEW.state = 'in_progress') OR
        (OLD.state = 'retryable'   AND NEW.state = 'in_progress') OR
        (OLD.state = 'in_progress' AND NEW.state IN
            ('in_progress', 'retryable', 'succeeded', 'failed'))
    ) THEN
        RAISE EXCEPTION 'illegal runtime task transition: % -> %', OLD.state, NEW.state
            USING ERRCODE = '55000';
    END IF;

    RETURN NEW;
END
$runtime_task_transition$;

CREATE TRIGGER trg_runtime_task_transition
BEFORE UPDATE ON runtime_tasks
FOR EACH ROW EXECUTE FUNCTION enforce_runtime_task_transition();

CREATE TABLE runtime_worker_heartbeats (
    tenant_id       text NOT NULL,
    organization_id text NOT NULL,
    worker_id       text NOT NULL,
    status          text NOT NULL DEFAULT 'starting',
    current_task_id uuid,
    started_at      timestamptz NOT NULL DEFAULT now(),
    heartbeat_at    timestamptz NOT NULL DEFAULT now(),
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT pk_runtime_worker_heartbeats
        PRIMARY KEY (tenant_id, organization_id, worker_id),
    CONSTRAINT fk_runtime_worker_current_task
        FOREIGN KEY (current_task_id, tenant_id, organization_id)
        REFERENCES runtime_tasks (task_id, tenant_id, organization_id)
        ON DELETE SET NULL (current_task_id),
    CONSTRAINT ck_runtime_worker_scope CHECK (
        tenant_id = btrim(tenant_id) AND length(tenant_id) BETWEEN 1 AND 200
        AND organization_id = btrim(organization_id)
        AND length(organization_id) BETWEEN 1 AND 200
    ),
    CONSTRAINT ck_runtime_worker_id CHECK (
        worker_id = btrim(worker_id) AND length(worker_id) BETWEEN 1 AND 200
    ),
    CONSTRAINT ck_runtime_worker_status CHECK (
        status IN ('starting', 'running', 'stopping')
    ),
    CONSTRAINT ck_runtime_worker_metadata CHECK (
        jsonb_typeof(metadata) = 'object'
    )
);

CREATE INDEX idx_runtime_worker_heartbeat
    ON runtime_worker_heartbeats (tenant_id, organization_id, heartbeat_at);

COMMENT ON TABLE runtime_tasks IS
    'Infrastructure-only durable queue; not an Ontology Action or business authority table.';
COMMENT ON TABLE runtime_worker_heartbeats IS
    'Mutable liveness projection for infrastructure workers; not business audit evidence.';
