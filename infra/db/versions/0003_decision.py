"""0003 decision: cycles, agents, proposals, verdicts, signed orders, orders, fills,
positions, portfolio snapshots, stop-monitor heartbeat.

The invariants that cost money live here as constraints, not as comments: one order per
verdict, one open position per (venue, pair), an unsigned verdict cannot be approved, and a
displaced stop cannot be recorded without its timestamp.
"""

from __future__ import annotations

from alembic import op

from atlas_common.sqlsplit import apply_statements

revision = "0003_decision"
down_revision = "0002_market"
branch_labels = None
depends_on = None


UPGRADE = """
CREATE TYPE cycle_trigger AS ENUM ('SCHEDULE', 'OPERATOR', 'EVENT');
CREATE TYPE cycle_outcome AS ENUM ('COMPLETED', 'SKIPPED', 'HALTED', 'ERROR', 'BUDGET_BLOCKED');
CREATE TYPE agent_name AS ENUM ('ANALYST', 'STRATEGIST', 'CRITIC');
CREATE TYPE proposal_status AS ENUM (
    'DRAFT', 'VALIDATED', 'REJECTED_INVALID', 'SUBMITTED', 'EXPIRED'
);
CREATE TYPE entry_type AS ENUM ('LIMIT', 'MARKET_LIMIT');
CREATE TYPE order_side AS ENUM ('BUY', 'SELL');
CREATE TYPE order_type AS ENUM ('LIMIT', 'MARKET');
CREATE TYPE order_status AS ENUM (
    'PENDING_NEW', 'OPEN', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'REJECTED',
    'EXPIRED', 'UNKNOWN'
);
CREATE TYPE remainder_action AS ENUM ('NONE', 'CANCEL_PENDING', 'CANCELED');
CREATE TYPE position_status AS ENUM ('OPEN', 'CLOSING', 'CLOSED', 'STOP_LOST');
CREATE TYPE stop_protection AS ENUM ('NATIVE_ORDER', 'MONITORED');
CREATE TYPE stop_confirmation AS ENUM ('CONFIRMED', 'UNCONFIRMED');
CREATE TYPE management_type AS ENUM ('MOVE_STOP', 'CLOSE', 'TIME_STOP');
CREATE TYPE cost_model_source AS ENUM ('VERIFIED', 'ASSUMED');
CREATE TYPE close_reason AS ENUM (
    'TARGET', 'STOP', 'TIME_STOP', 'OPERATOR', 'KILL_SWITCH', 'MODE_FALLBACK'
);

CREATE TABLE decision_cycle (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mode           mode_stage NOT NULL,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    trigger        cycle_trigger NOT NULL,
    leader_lock_id text,
    outcome        cycle_outcome,
    skip_reason    text,
    CONSTRAINT decision_cycle_finished_after_started CHECK (
        finished_at IS NULL OR finished_at >= started_at
    ),
    CONSTRAINT decision_cycle_outcome_needs_finish CHECK (
        outcome IS NULL OR finished_at IS NOT NULL
    )
);
CREATE INDEX decision_cycle_started_idx ON decision_cycle (started_at DESC);

CREATE TABLE agent_run (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cycle_id      uuid NOT NULL REFERENCES decision_cycle (id),
    agent         agent_name NOT NULL,
    state_from    text NOT NULL,
    state_to      text NOT NULL,
    prompt_hash   text NOT NULL,
    prompt_text   text,
    model_id      text,
    model_version text,
    params        jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_output    text,
    parsed_output jsonb,
    tokens        int,
    cost_idr      numeric(12,2),
    latency_ms    int,
    error_code    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_run_counts_non_negative CHECK (
        (tokens IS NULL OR tokens >= 0) AND (latency_ms IS NULL OR latency_ms >= 0)
        AND (cost_idr IS NULL OR cost_idr >= 0)
    )
);
CREATE INDEX agent_run_cycle_idx ON agent_run (cycle_id, created_at);

CREATE TABLE trade_proposal (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cycle_id             uuid NOT NULL REFERENCES decision_cycle (id),
    agent_run_id         uuid REFERENCES agent_run (id),
    pair                 text NOT NULL,
    side                 order_side NOT NULL DEFAULT 'BUY',
    entry_type           entry_type NOT NULL,
    entry_price          numeric(20,8) NOT NULL,
    stop_price           numeric(20,8) NOT NULL,
    target_price         numeric(20,8) NOT NULL,
    stop_distance_pct    numeric(10,6)
        GENERATED ALWAYS AS ((entry_price - stop_price) / entry_price * 100) STORED,
    horizon_hours        int NOT NULL,
    intended_notional_idr numeric(28,10) NOT NULL,
    rationale_text       text NOT NULL,
    confidence           numeric(3,2) NOT NULL,
    status               proposal_status NOT NULL DEFAULT 'DRAFT',
    schema_version       text NOT NULL DEFAULT '1.0',
    created_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT trade_proposal_long_only CHECK (side = 'BUY'),
    CONSTRAINT trade_proposal_prices_ordered CHECK (
        stop_price > 0 AND stop_price < entry_price AND entry_price < target_price
    ),
    CONSTRAINT trade_proposal_horizon_range CHECK (horizon_hours BETWEEN 4 AND 120),
    CONSTRAINT trade_proposal_notional_positive CHECK (intended_notional_idr > 0),
    CONSTRAINT trade_proposal_confidence_range CHECK (confidence >= 0 AND confidence <= 1)
);
CREATE INDEX trade_proposal_cycle_idx ON trade_proposal (cycle_id);

CREATE TABLE management_action (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id   uuid NOT NULL,
    type          management_type NOT NULL,
    new_stop      numeric(20,8),
    reason        text NOT NULL,
    agent_run_id  uuid REFERENCES agent_run (id),
    status        proposal_status NOT NULL DEFAULT 'DRAFT',
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT management_action_stop_required CHECK (
        (type = 'MOVE_STOP' AND new_stop IS NOT NULL AND new_stop > 0)
        OR (type <> 'MOVE_STOP' AND new_stop IS NULL)
    )
);

CREATE TABLE risk_verdict (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id           uuid REFERENCES trade_proposal (id),
    management_action_id  uuid REFERENCES management_action (id),
    approved              boolean NOT NULL,
    rule_results          jsonb NOT NULL,
    primary_rule_id       text,
    sized_quantity        numeric(28,10),
    sized_notional_idr    numeric(28,10),
    realised_risk_idr     numeric(28,10),
    realised_risk_pct     numeric(10,6),
    rr_net                numeric(6,3),
    cost_breakdown        jsonb,
    cost_model_source     cost_model_source NOT NULL DEFAULT 'VERIFIED',
    risk_config_version   int NOT NULL REFERENCES risk_config (version),
    venue_profile_id      bigint NOT NULL REFERENCES venue_profile (id),
    portfolio_snapshot_id uuid NOT NULL,
    input_hash            text NOT NULL,
    signature             text,
    verdict_at            timestamptz NOT NULL DEFAULT now(),
    expires_at            timestamptz NOT NULL,
    consumed_at           timestamptz,
    consumed_by_order_id  uuid,
    CONSTRAINT risk_verdict_one_subject CHECK (
        (proposal_id IS NOT NULL)::int + (management_action_id IS NOT NULL)::int = 1
    ),
    CONSTRAINT risk_verdict_rules_is_array CHECK (jsonb_typeof(rule_results) = 'array'),
    CONSTRAINT risk_verdict_signature_requires_approval CHECK (
        approved = (signature IS NOT NULL)
    ),
    CONSTRAINT risk_verdict_primary_rule_only_when_rejected CHECK (
        approved = (primary_rule_id IS NULL)
    ),
    CONSTRAINT risk_verdict_expiry_after_verdict CHECK (expires_at > verdict_at),
    CONSTRAINT risk_verdict_expiry_window CHECK (expires_at <= verdict_at + INTERVAL '60 seconds'),
    CONSTRAINT risk_verdict_sizes_positive CHECK (
        (sized_quantity IS NULL OR sized_quantity > 0)
        AND (sized_notional_idr IS NULL OR sized_notional_idr > 0)
    ),
    -- An approval without a size hands sizing to whoever submits the order.
    CONSTRAINT risk_verdict_approved_is_sized CHECK (
        NOT approved OR (sized_quantity IS NOT NULL AND sized_notional_idr IS NOT NULL)
    ),
    CONSTRAINT risk_verdict_consumption_paired CHECK (
        (consumed_at IS NULL) = (consumed_by_order_id IS NULL)
    ),
    CONSTRAINT risk_verdict_assumed_cost_needs_breakdown CHECK (
        cost_model_source <> 'ASSUMED' OR cost_breakdown IS NOT NULL
    )
);
CREATE UNIQUE INDEX risk_verdict_one_per_proposal ON risk_verdict (proposal_id)
    WHERE proposal_id IS NOT NULL;
CREATE UNIQUE INDEX risk_verdict_one_per_action ON risk_verdict (management_action_id)
    WHERE management_action_id IS NOT NULL;
CREATE INDEX risk_verdict_verdict_at_idx ON risk_verdict (verdict_at DESC);
-- Composite keys so an order can only cite a verdict that approved THAT proposal.
CREATE UNIQUE INDEX risk_verdict_id_proposal_idx ON risk_verdict (id, proposal_id);
CREATE UNIQUE INDEX risk_verdict_id_approved_idx ON risk_verdict (id, approved);

CREATE TABLE signed_order (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    verdict_id        uuid NOT NULL UNIQUE REFERENCES risk_verdict (id),
    proposal_id       uuid NOT NULL REFERENCES trade_proposal (id),
    venue_id          text NOT NULL REFERENCES venue (id),
    pair              text NOT NULL,
    side              order_side NOT NULL,
    type              order_type NOT NULL,
    quantity          numeric(28,10) NOT NULL,
    limit_price       numeric(20,8),
    stop_price        numeric(20,8),    client_order_id   text NOT NULL,
    signature         text NOT NULL,
    signed_at         timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz NOT NULL,
    -- Copy of risk_verdict.approved so the composite FK below can require it.
    verdict_approved  boolean NOT NULL DEFAULT true,
    CONSTRAINT signed_order_quantity_positive CHECK (quantity > 0),
    CONSTRAINT signed_order_limit_price_required CHECK (
        type <> 'LIMIT' OR (limit_price IS NOT NULL AND limit_price > 0)
    ),
    CONSTRAINT signed_order_expiry_after_signing CHECK (expires_at > signed_at),
    CONSTRAINT signed_order_expiry_window CHECK (expires_at <= signed_at + INTERVAL '60 seconds'),
    -- Every signed entry must carry the stop it was approved with.
    CONSTRAINT signed_order_entry_has_stop CHECK (side <> 'BUY' OR stop_price IS NOT NULL),
    CONSTRAINT signed_order_verdict_approved CHECK (verdict_approved),
    CONSTRAINT signed_order_verdict_matches_proposal FOREIGN KEY (verdict_id, proposal_id)
        REFERENCES risk_verdict (id, proposal_id),
    CONSTRAINT signed_order_verdict_is_approved FOREIGN KEY (verdict_id, verdict_approved)
        REFERENCES risk_verdict (id, approved)
);

CREATE TABLE "order" (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_id            text NOT NULL REFERENCES venue (id),
    venue_order_id      text,
    client_order_id     text NOT NULL,
    signed_order_id     uuid NOT NULL UNIQUE REFERENCES signed_order (id),
    position_id         uuid,
    pair                text NOT NULL,
    side                order_side NOT NULL,
    type                order_type NOT NULL,
    quantity            numeric(28,10) NOT NULL,
    limit_price         numeric(20,8),
    status              order_status NOT NULL DEFAULT 'PENDING_NEW',
    filled_quantity     numeric(28,10) NOT NULL DEFAULT 0,
    avg_fill_price      numeric(20,8),
    remaining_quantity  numeric(28,10)
        GENERATED ALWAYS AS (quantity - filled_quantity) STORED,
    remainder_action    remainder_action NOT NULL DEFAULT 'NONE',
    fees_paid_idr       numeric(28,10) NOT NULL DEFAULT 0,
    tax_paid_idr        numeric(28,10) NOT NULL DEFAULT 0,
    reject_reason       text,
    unknown_since       timestamptz,
    resolution_attempts int NOT NULL DEFAULT 0,
    resolved_at         timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    submitted_at        timestamptz,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    raw_responses       jsonb NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT order_quantity_positive CHECK (quantity > 0),
    CONSTRAINT order_filled_within_quantity CHECK (
        filled_quantity >= 0 AND filled_quantity <= quantity
    ),
    CONSTRAINT order_limit_price_required CHECK (
        type <> 'LIMIT' OR (limit_price IS NOT NULL AND limit_price > 0)
    ),
    CONSTRAINT order_partial_fill_is_consistent CHECK (
        status <> 'PARTIALLY_FILLED' OR (filled_quantity > 0 AND filled_quantity < quantity)
    ),
    CONSTRAINT order_open_is_not_full CHECK (
        status <> 'OPEN' OR filled_quantity < quantity
    ),
    CONSTRAINT order_filled_status_is_consistent CHECK (
        status <> 'FILLED' OR filled_quantity = quantity
    ),
    CONSTRAINT order_unknown_needs_timestamp CHECK (
        (status = 'UNKNOWN') = (unknown_since IS NOT NULL)
    ),
    CONSTRAINT order_fees_non_negative CHECK (fees_paid_idr >= 0 AND tax_paid_idr >= 0),
    CONSTRAINT order_venue_order_id_unique UNIQUE (venue_id, venue_order_id),
    CONSTRAINT order_client_order_id_unique UNIQUE (venue_id, client_order_id)
);
CREATE INDEX order_status_idx ON "order" (status, updated_at DESC);
CREATE INDEX order_position_idx ON "order" (position_id);

CREATE TABLE fill (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id       uuid NOT NULL REFERENCES "order" (id),
    venue_id       text NOT NULL REFERENCES venue (id),
    venue_trade_id text NOT NULL,
    quantity       numeric(28,10) NOT NULL,
    price          numeric(20,8) NOT NULL,
    fee_idr        numeric(28,10) NOT NULL DEFAULT 0,
    tax_idr        numeric(28,10) NOT NULL DEFAULT 0,
    is_maker       boolean NOT NULL DEFAULT false,
    filled_at      timestamptz NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fill_quantity_positive CHECK (quantity > 0),
    CONSTRAINT fill_price_positive CHECK (price > 0),
    CONSTRAINT fill_costs_non_negative CHECK (fee_idr >= 0 AND tax_idr >= 0),
    CONSTRAINT fill_trade_unique UNIQUE (venue_id, venue_trade_id)
);
CREATE INDEX fill_order_idx ON fill (order_id);

CREATE TABLE position (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_id           text NOT NULL REFERENCES venue (id),
    pair               text NOT NULL,
    quantity           numeric(28,10) NOT NULL,
    avg_entry_price    numeric(20,8) NOT NULL,
    notional_idr       numeric(28,10) NOT NULL,
    stop_price         numeric(20,8) NOT NULL,
    stop_order_id      uuid REFERENCES "order" (id),
    stop_protection    stop_protection NOT NULL,
    stop_monitor_seen_at timestamptz,
    stop_confirmation  stop_confirmation NOT NULL DEFAULT 'CONFIRMED',
    stop_unconfirmed_at timestamptz,
    target_price       numeric(20,8) NOT NULL,
    opened_at          timestamptz NOT NULL DEFAULT now(),
    time_stop_at       timestamptz NOT NULL,
    status             position_status NOT NULL DEFAULT 'OPEN',
    realised_pnl_idr   numeric(28,10),
    fees_idr           numeric(28,10) NOT NULL DEFAULT 0,
    taxes_idr          numeric(28,10) NOT NULL DEFAULT 0,
    closed_at          timestamptz,
    close_reason       close_reason,
    CONSTRAINT position_quantity_positive CHECK (quantity > 0),
    CONSTRAINT position_prices_positive CHECK (
        avg_entry_price > 0 AND stop_price > 0 AND target_price > 0
    ),
    CONSTRAINT position_stop_below_entry CHECK (stop_price < avg_entry_price),
    CONSTRAINT position_target_above_entry CHECK (target_price > avg_entry_price),
    CONSTRAINT position_time_stop_after_open CHECK (time_stop_at > opened_at),
    CONSTRAINT position_stop_confirmation_paired CHECK (
        (stop_confirmation = 'UNCONFIRMED') = (stop_unconfirmed_at IS NOT NULL)
    ),
    CONSTRAINT position_monitored_has_no_native_stop CHECK (
        stop_protection <> 'NATIVE_ORDER' OR stop_order_id IS NOT NULL
    ),
    CONSTRAINT position_closed_is_complete CHECK (
        (status IN ('CLOSED', 'STOP_LOST')) = (closed_at IS NOT NULL)
    ),
    CONSTRAINT position_close_reason_matches_status CHECK (
        (status IN ('CLOSED', 'STOP_LOST')) = (close_reason IS NOT NULL)
    )
);
CREATE UNIQUE INDEX position_one_open_per_pair
    ON position (venue_id, pair) WHERE status IN ('OPEN', 'CLOSING');
CREATE INDEX position_status_idx ON position (status, opened_at DESC);

CREATE TABLE portfolio_snapshot (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ts                  timestamptz NOT NULL DEFAULT now(),
    equity_idr          numeric(28,10) NOT NULL,
    cash_idr            numeric(28,10) NOT NULL,
    positions_value_idr numeric(28,10) NOT NULL,
    unrealised_pnl_idr  numeric(28,10) NOT NULL,
    realised_pnl_day_idr numeric(28,10) NOT NULL,
    drawdown_pct        numeric(10,6) NOT NULL,
    peak_equity_idr     numeric(28,10) NOT NULL,
    per_venue           jsonb NOT NULL DEFAULT '{}'::jsonb,
    reconciled          boolean NOT NULL DEFAULT false,
    CONSTRAINT portfolio_snapshot_drawdown_range CHECK (
        drawdown_pct >= 0 AND drawdown_pct <= 100
    ),
    CONSTRAINT portfolio_snapshot_equity_positive CHECK (equity_idr > 0)
);
CREATE INDEX portfolio_snapshot_ts_idx ON portfolio_snapshot (ts DESC);
CREATE INDEX portfolio_snapshot_reconciled_idx ON portfolio_snapshot (reconciled, ts DESC);

-- These tables are created after risk_verdict, so their foreign keys land here.
ALTER TABLE risk_verdict
    ADD CONSTRAINT risk_verdict_portfolio_snapshot_fk
        FOREIGN KEY (portfolio_snapshot_id) REFERENCES portfolio_snapshot (id);
ALTER TABLE risk_verdict
    ADD CONSTRAINT risk_verdict_consumed_by_order_fk
        FOREIGN KEY (consumed_by_order_id) REFERENCES "order" (id);

CREATE TABLE stop_monitor_heartbeat (
    process_id       text PRIMARY KEY,
    started_at       timestamptz NOT NULL,
    last_beat_at     timestamptz NOT NULL,
    beat_interval_s  int NOT NULL DEFAULT 10,
    positions_checked int NOT NULL DEFAULT 0,
    last_cycle_ms    int,
    CONSTRAINT stop_monitor_beat_interval_positive CHECK (beat_interval_s > 0),
    CONSTRAINT stop_monitor_last_beat_after_start CHECK (last_beat_at >= started_at),
    CONSTRAINT stop_monitor_counts_non_negative CHECK (
        positions_checked >= 0 AND (last_cycle_ms IS NULL OR last_cycle_ms >= 0)
    )
);
"""

DOWNGRADE = """
DROP TABLE IF EXISTS stop_monitor_heartbeat;
DROP TABLE IF EXISTS portfolio_snapshot;
DROP TABLE IF EXISTS position;
DROP TABLE IF EXISTS fill;
DROP TABLE IF EXISTS "order";
DROP TABLE IF EXISTS signed_order;
DROP TABLE IF EXISTS risk_verdict;
DROP TABLE IF EXISTS management_action;
DROP TABLE IF EXISTS trade_proposal;
DROP TABLE IF EXISTS agent_run;
DROP TABLE IF EXISTS decision_cycle;
DROP TYPE IF EXISTS close_reason;
DROP TYPE IF EXISTS cost_model_source;
DROP TYPE IF EXISTS management_type;
DROP TYPE IF EXISTS stop_confirmation;
DROP TYPE IF EXISTS stop_protection;
DROP TYPE IF EXISTS position_status;
DROP TYPE IF EXISTS remainder_action;
DROP TYPE IF EXISTS order_status;
DROP TYPE IF EXISTS order_type;
DROP TYPE IF EXISTS order_side;
DROP TYPE IF EXISTS entry_type;
DROP TYPE IF EXISTS proposal_status;
DROP TYPE IF EXISTS agent_name;
DROP TYPE IF EXISTS cycle_outcome;
DROP TYPE IF EXISTS cycle_trigger;
"""


def upgrade() -> None:
    apply_statements(op.get_bind(), UPGRADE)


def downgrade() -> None:
    apply_statements(op.get_bind(), DOWNGRADE)
