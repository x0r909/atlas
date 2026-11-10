"""0010 stop invariants: what replaces `position_stop_below_entry` in the schema itself.

0009 dropped the check that tied a stop to the entry price, because a long's stop has to be able
to tighten past break-even. That left three properties enforced only by `execute.accept`, i.e.
by whichever caller remembers to compare:

* a long's stop never falls — a wider stop is a refusal, never an overwrite;
* `stop_generation` never moves backwards, so a late monitor message cannot beat a newer one;
* a stop is never set at or above the last known market price, because such a "stop" is a
  marketable sell that would close the position the moment it rests.

The generation floor could be a CHECK (`NEW >= OLD`), but a trigger states all three in one
place and lets the market check read `quote_snapshot`. The market bound applies when the stop is
first written or moved, not when a later tick falls through it: a stop above a fallen market is
the trigger doing its job, and updating an unrelated column must not fail.
"""

from __future__ import annotations

from alembic import op

from atlas_common.sqlsplit import apply_statements

revision = "0010_stop_invariants"
down_revision = "0009_protective_stop"
branch_labels = None
depends_on = None

UPGRADE = r"""
CREATE OR REPLACE FUNCTION position_stop_invariants() RETURNS trigger AS $$
DECLARE
    last_price numeric;
BEGIN
    IF NEW.stop_price IS NULL THEN
        RETURN NEW;
    END IF;

    IF TG_OP = 'UPDATE' AND OLD.stop_price IS NOT NULL
       AND NEW.stop_price < OLD.stop_price THEN
        RAISE EXCEPTION
            'stop_price may not fall (% -> %) on position %', OLD.stop_price, NEW.stop_price,
            NEW.id
            USING ERRCODE = 'check_violation';
    END IF;

    IF TG_OP = 'UPDATE' AND NEW.stop_generation < OLD.stop_generation THEN
        RAISE EXCEPTION
            'stop_generation may not decrease (% -> %) on position %',
            OLD.stop_generation, NEW.stop_generation, NEW.id
            USING ERRCODE = 'check_violation';
    END IF;

    -- Atlas holds spot long only (no shorts), so the bound always points down.
    IF TG_OP = 'INSERT' OR NEW.stop_price IS DISTINCT FROM OLD.stop_price THEN
        SELECT q.last INTO last_price
          FROM quote_snapshot q
         WHERE q.venue_id = NEW.venue_id AND q.pair = NEW.pair;
        IF last_price IS NOT NULL AND NEW.stop_price >= last_price THEN
            RAISE EXCEPTION
                'stop_price % is at or above the last known market price % for position %',
                NEW.stop_price, last_price, NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER position_stop_invariants
    BEFORE INSERT OR UPDATE ON position
    FOR EACH ROW EXECUTE FUNCTION position_stop_invariants();
"""

DOWNGRADE = r"""
DROP TRIGGER IF EXISTS position_stop_invariants ON position;
DROP FUNCTION IF EXISTS position_stop_invariants();
"""


def upgrade() -> None:
    apply_statements(op.get_bind(), UPGRADE)


def downgrade() -> None:
    apply_statements(op.get_bind(), DOWNGRADE)
