"""0005 rollout guard: mode_state is append-only and its transitions are a closed graph.

The trigger is the last line of defence. Code paths are supposed to check first, but a bug
in a promotion endpoint must not be able to reach LIVE by writing a row.
"""

from __future__ import annotations

from alembic import op

from atlas_common.sqlsplit import apply_statements

revision = "0005_mode_state_rules"
down_revision = "0004_audit"
branch_labels = None
depends_on = None


UPGRADE = r"""
ALTER TABLE mode_state
    ADD COLUMN prev_seq bigint REFERENCES mode_state (seq),
    -- The link is unique, so a fork (two successors of one row) fails on commit even if
    -- two writers manage to read the same predecessor.
    ADD CONSTRAINT mode_state_prev_seq_unique UNIQUE (prev_seq);

ALTER TABLE mode_state
    ADD CONSTRAINT mode_state_capital_cap_by_stage CHECK (
        (mode = 'CO_PILOT' AND capital_cap_idr IS NOT NULL AND capital_cap_idr <= 30000)
        OR (mode <> 'CO_PILOT' AND capital_cap_idr IS NULL)
    ),
    -- A demotion reason must not be recorded against a promotion target.
    ADD CONSTRAINT mode_state_reason_matches_direction CHECK (
        reason_code NOT IN ('AUTO_DEMOTE', 'OPERATOR_DEMOTION')
        OR mode IN ('PAPER', 'CO_PILOT', 'HALTED')
    ),
    ADD CONSTRAINT mode_state_actor_ref_shape CHECK (
        actor_ref IS NULL OR (actor = 'OPERATOR' AND btrim(actor_ref) <> '')
    );

CREATE FUNCTION mode_state_transition_guard() RETURNS trigger AS $guard$
DECLARE
    current_mode mode_stage;
    current_seq  bigint;
BEGIN
    -- Serialises writers: without it two concurrent appends both validate against the
    -- same predecessor and the rollout history forks.
    PERFORM pg_advisory_xact_lock(hashtext('mode_state'));

    SELECT mode, seq INTO current_mode, current_seq
    FROM mode_state
    ORDER BY seq DESC
    LIMIT 1;

    IF NOT FOUND THEN
        -- First row ever written: STARTUP_DEFAULT is the only way in, and it means PAPER.
        IF NEW.mode <> 'PAPER' THEN
            RAISE EXCEPTION
                'mode_state: first row must be PAPER, got %', NEW.mode
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.reason_code <> 'STARTUP_DEFAULT' THEN
            RAISE EXCEPTION
                'mode_state: first row must be STARTUP_DEFAULT, got %', NEW.reason_code
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.previous_mode IS NOT NULL THEN
            RAISE EXCEPTION 'mode_state: first row cannot have a previous_mode'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.reason_code = 'STARTUP_DEFAULT' THEN
        RAISE EXCEPTION 'mode_state: STARTUP_DEFAULT is reserved for the seed row'
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.previous_mode IS DISTINCT FROM current_mode THEN
        RAISE EXCEPTION
            'mode_state: previous_mode % does not match current mode %',
            NEW.previous_mode, current_mode
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.mode = current_mode THEN
        RAISE EXCEPTION 'mode_state: % is already the current mode', NEW.mode
            USING ERRCODE = 'check_violation';
    END IF;

    IF NOT (
        (current_mode = 'PAPER' AND NEW.mode IN ('CO_PILOT', 'HALTED'))
        OR (current_mode = 'CO_PILOT'
            AND NEW.mode IN ('AUTONOMOUS', 'PAPER', 'HALTED'))
        OR (current_mode = 'AUTONOMOUS' AND NEW.mode IN ('CO_PILOT', 'PAPER', 'HALTED'))
        OR (current_mode = 'BACKTEST' AND NEW.mode IN ('PAPER', 'HALTED'))
        OR (current_mode = 'HALTED' AND NEW.mode IN ('PAPER', 'CO_PILOT'))
    ) THEN
        RAISE EXCEPTION
            'mode_state: transition % -> % is not allowed', current_mode, NEW.mode
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.reason_code IN ('OPERATOR_PROMOTION', 'OPERATOR_DEMOTION')
       AND NEW.evidence_ref IS NULL THEN
        RAISE EXCEPTION
            'mode_state: % requires promotion evidence', NEW.reason_code
            USING ERRCODE = 'check_violation';
    END IF;

    NEW.prev_seq := current_seq;
    RETURN NEW;
END
$guard$ LANGUAGE plpgsql;

CREATE TRIGGER mode_state_transition_guard
    BEFORE INSERT ON mode_state
    FOR EACH ROW EXECUTE FUNCTION mode_state_transition_guard();

CREATE FUNCTION mode_state_append_only() RETURNS trigger AS $guard$
BEGIN
    RAISE EXCEPTION 'mode_state is append-only: % is not permitted', TG_OP
        USING ERRCODE = 'check_violation';
END
$guard$ LANGUAGE plpgsql;

CREATE TRIGGER mode_state_no_update
    BEFORE UPDATE ON mode_state
    FOR EACH ROW EXECUTE FUNCTION mode_state_append_only();

CREATE TRIGGER mode_state_no_delete
    BEFORE DELETE ON mode_state
    FOR EACH ROW EXECUTE FUNCTION mode_state_append_only();

-- Row triggers do not fire on TRUNCATE, and one statement would erase the whole history.
CREATE TRIGGER mode_state_no_truncate
    BEFORE TRUNCATE ON mode_state
    FOR EACH STATEMENT EXECUTE FUNCTION mode_state_append_only();

REVOKE TRUNCATE ON mode_state FROM PUBLIC;

INSERT INTO mode_state (mode, previous_mode, reason_code, actor)
VALUES ('PAPER', NULL, 'STARTUP_DEFAULT', 'SYSTEM');
"""

DOWNGRADE = """
DROP TRIGGER IF EXISTS mode_state_no_truncate ON mode_state;
DROP TRIGGER IF EXISTS mode_state_no_delete ON mode_state;
DROP TRIGGER IF EXISTS mode_state_no_update ON mode_state;
DELETE FROM mode_state WHERE reason_code = 'STARTUP_DEFAULT' AND previous_mode IS NULL;
DROP TRIGGER IF EXISTS mode_state_transition_guard ON mode_state;
DROP FUNCTION IF EXISTS mode_state_append_only();
DROP FUNCTION IF EXISTS mode_state_transition_guard();
ALTER TABLE mode_state DROP CONSTRAINT IF EXISTS mode_state_actor_ref_shape;
ALTER TABLE mode_state DROP CONSTRAINT IF EXISTS mode_state_reason_matches_direction;
ALTER TABLE mode_state DROP CONSTRAINT IF EXISTS mode_state_capital_cap_by_stage;
ALTER TABLE mode_state DROP CONSTRAINT IF EXISTS mode_state_prev_seq_unique;
ALTER TABLE mode_state DROP COLUMN IF EXISTS prev_seq;
"""


def upgrade() -> None:
    apply_statements(op.get_bind(), UPGRADE)


def downgrade() -> None:
    apply_statements(op.get_bind(), DOWNGRADE)
