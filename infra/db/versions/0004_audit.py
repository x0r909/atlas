"""0004 audit: hash-chained audit record, replay bundles, incidents, promotion, LLM spend.

audit_record is append-only twice over: the chain makes a silent edit detectable, and the
privileges below make it impossible for the application role in the first place.
"""

from __future__ import annotations

from alembic import op

from atlas_common.sqlsplit import apply_statements

revision = "0004_audit"
down_revision = "0003_decision"
branch_labels = None
depends_on = None


UPGRADE = """
CREATE TYPE audit_record_type AS ENUM (
    'DECISION', 'ORDER', 'MODE_CHANGE', 'CONFIG_CHANGE', 'KILL_SWITCH', 'PROMOTION',
    'INCIDENT', 'RECONCILE'
);
CREATE TYPE incident_class AS ENUM ('SAFETY', 'OPERATIONAL');
CREATE TYPE incident_kind AS ENUM (
    'ORDER_WITHOUT_STOP', 'HARD_LIMIT_BREACH', 'DUPLICATE_ORDER', 'POSITION_MISMATCH',
    'KILL_SWITCH_FAILURE', 'STOP_UNCONFIRMED', 'ADOPTED_POSITION_UNPROTECTED',
    'STOP_MONITOR_UNRESPONSIVE',
    'VENUE_DOWN', 'STALE_DATA', 'LLM_FAILURE', 'BUDGET_THROTTLE', 'RECONCILE_DELAY',
    'LOG_CHAIN_BREAK', 'POISON_EVENT'
);
CREATE TYPE incident_severity AS ENUM ('SEV1', 'SEV2', 'SEV3');
CREATE TYPE incident_auto_action AS ENUM ('NONE', 'HALTED', 'THROTTLED');
CREATE TYPE promotion_stage AS ENUM ('PAPER', 'CO_PILOT', 'AUTONOMOUS');
CREATE TYPE llm_tier AS ENUM ('CHEAP', 'STRONG');
CREATE TYPE llm_outcome AS ENUM (
    'OK', 'TIMEOUT', 'INVALID_OUTPUT', 'RATE_LIMITED', 'BUDGET_BLOCKED'
);

CREATE TABLE audit_record (
    seq         bigint PRIMARY KEY,
    record_type audit_record_type NOT NULL,
    payload     jsonb NOT NULL,
    prev_hash   char(64) NOT NULL,
    hash        char(64) NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT audit_record_seq_positive CHECK (seq >= 1),
    CONSTRAINT audit_record_hash_is_sha256 CHECK (hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT audit_record_prev_hash_is_sha256 CHECK (prev_hash ~ '^[0-9a-f]{64}$')
);
CREATE UNIQUE INDEX audit_record_prev_hash_unique ON audit_record (prev_hash)
    WHERE prev_hash <> repeat('0', 64);
CREATE INDEX audit_record_type_created_idx ON audit_record (record_type, created_at DESC);

-- The application may append and read. It may never rewrite history.
REVOKE UPDATE, DELETE, TRUNCATE ON audit_record FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atlas_app') THEN
        REVOKE UPDATE, DELETE, TRUNCATE ON audit_record FROM atlas_app;
        GRANT INSERT, SELECT ON audit_record TO atlas_app;
    END IF;
END
$$;

CREATE TABLE decision_record (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cycle_id              uuid NOT NULL REFERENCES decision_cycle (id),
    proposal_id           uuid REFERENCES trade_proposal (id),
    agent_runs            uuid[] NOT NULL DEFAULT '{}',
    market_snapshot_hash  text NOT NULL,
    news_snapshot_hash    text NOT NULL,
    portfolio_snapshot_id uuid REFERENCES portfolio_snapshot (id),
    order_book_snapshot_id uuid REFERENCES order_book_snapshot (id),
    prompt_bundle         jsonb NOT NULL DEFAULT '{}'::jsonb,
    model_meta            jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_outputs           jsonb NOT NULL DEFAULT '{}'::jsonb,
    verdict_id            uuid REFERENCES risk_verdict (id),
    orders                uuid[] NOT NULL DEFAULT '{}',
    exchange_responses    jsonb NOT NULL DEFAULT '[]'::jsonb,
    code_version          text NOT NULL,
    config_hash           text NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT decision_record_hashes_present CHECK (
        btrim(market_snapshot_hash) <> '' AND btrim(news_snapshot_hash) <> ''
    )
);
CREATE INDEX decision_record_cycle_idx ON decision_record (cycle_id);

CREATE TABLE incident (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    opened_at      timestamptz NOT NULL DEFAULT now(),
    closed_at      timestamptz,
    class          incident_class NOT NULL,
    kind           incident_kind NOT NULL,
    severity       incident_severity NOT NULL,
    auto_action    incident_auto_action NOT NULL DEFAULT 'NONE',
    root_cause     text,
    remediation    text,
    operator_ack_at timestamptz,
    detail         jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT incident_closed_after_opened CHECK (closed_at IS NULL OR closed_at >= opened_at),
    CONSTRAINT incident_safety_is_sev1 CHECK (class <> 'SAFETY' OR severity = 'SEV1'),
    CONSTRAINT incident_safety_kind_matches_class CHECK (
        (class = 'SAFETY') = (kind IN (
            'ORDER_WITHOUT_STOP', 'HARD_LIMIT_BREACH', 'DUPLICATE_ORDER',
            'POSITION_MISMATCH', 'KILL_SWITCH_FAILURE', 'STOP_UNCONFIRMED',
            'ADOPTED_POSITION_UNPROTECTED', 'STOP_MONITOR_UNRESPONSIVE'
        ))
    )
);
CREATE INDEX incident_open_idx ON incident (severity, opened_at DESC) WHERE closed_at IS NULL;

CREATE TABLE promotion_evaluation (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    from_stage              promotion_stage NOT NULL,
    to_stage                promotion_stage NOT NULL,
    window_start            timestamptz NOT NULL,
    window_end              timestamptz NOT NULL,
    metrics                 jsonb NOT NULL,
    criteria                jsonb NOT NULL,
    passed                  boolean NOT NULL,
    failing_criteria        text[] NOT NULL DEFAULT '{}',
    operator_confirmed_by   uuid REFERENCES operator (id),
    operator_confirmed_at   timestamptz,
    resulting_mode_state_id uuid REFERENCES mode_state (id),
    created_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT promotion_evaluation_stage_order CHECK (
        (from_stage = 'PAPER' AND to_stage = 'CO_PILOT')
        OR (from_stage = 'CO_PILOT' AND to_stage = 'AUTONOMOUS')
    ),
    CONSTRAINT promotion_evaluation_window_ordered CHECK (window_end > window_start),
    CONSTRAINT promotion_evaluation_criteria_is_array CHECK (jsonb_typeof(criteria) = 'array'),
    CONSTRAINT promotion_evaluation_passed_matches_failures CHECK (
        passed = (cardinality(failing_criteria) = 0)
    ),
    CONSTRAINT promotion_evaluation_confirm_is_paired CHECK (
        (operator_confirmed_by IS NULL) = (operator_confirmed_at IS NULL)
    )
);

CREATE TABLE llm_spend (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_cycle_id  uuid NOT NULL REFERENCES decision_cycle (id),
    tier               llm_tier NOT NULL,
    provider           text NOT NULL,
    model_id           text NOT NULL,
    model_version      text,
    input_tokens       int NOT NULL,
    output_tokens      int NOT NULL,
    cost_idr           numeric(12,2) NOT NULL,
    latency_ms         int NOT NULL,
    outcome            llm_outcome NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT llm_spend_counts_non_negative CHECK (
        input_tokens >= 0 AND output_tokens >= 0 AND latency_ms >= 0
    ),
    CONSTRAINT llm_spend_cost_non_negative CHECK (cost_idr >= 0)
);
CREATE INDEX llm_spend_created_idx ON llm_spend (created_at DESC);
CREATE INDEX llm_spend_cycle_idx ON llm_spend (decision_cycle_id);
"""

DOWNGRADE = """
DROP TABLE IF EXISTS llm_spend;
DROP TABLE IF EXISTS promotion_evaluation;
DROP TABLE IF EXISTS incident;
DROP TABLE IF EXISTS decision_record;
DROP TABLE IF EXISTS audit_record;
DROP TYPE IF EXISTS llm_outcome;
DROP TYPE IF EXISTS llm_tier;
DROP TYPE IF EXISTS promotion_stage;
DROP TYPE IF EXISTS incident_auto_action;
DROP TYPE IF EXISTS incident_severity;
DROP TYPE IF EXISTS incident_kind;
DROP TYPE IF EXISTS incident_class;
DROP TYPE IF EXISTS audit_record_type;
"""


def upgrade() -> None:
    apply_statements(op.get_bind(), UPGRADE)


def downgrade() -> None:
    apply_statements(op.get_bind(), DOWNGRADE)
