"""0009 protective stops: an order without a verdict, and a stop that may tighten.

A protective stop is reduce-only and has no signed order behind it — the thing it reduces is an
exposure the system already holds (FR-063). Making ``signed_order_id`` optional is what lets one
order table hold both kinds, and ``is_protective`` keeps the two distinguishable in every query
that matters.

``position.stop_generation`` is the monitor's fencing token: a late request from an older
monitor incarnation must not overwrite a stop a newer one already placed.
"""

from __future__ import annotations

from alembic import op

from atlas_common.sqlsplit import apply_statements

revision = "0009_protective_stop"
down_revision = "0008_news"
branch_labels = None
depends_on = None

UPGRADE = r"""
ALTER TABLE "order" ALTER COLUMN signed_order_id DROP NOT NULL;

ALTER TABLE "order" ADD COLUMN is_protective boolean NOT NULL DEFAULT false;

-- Entries still need a verdict: only the protective kind may exist without a signed order.
ALTER TABLE "order"
    ADD CONSTRAINT order_entry_has_signed_order CHECK (
        is_protective OR signed_order_id IS NOT NULL
    ),
    ADD CONSTRAINT order_protective_shape CHECK (
        NOT is_protective OR (
            side = 'SELL' AND type = 'LIMIT'
            AND signed_order_id IS NULL AND limit_price IS NOT NULL
        )
    );

ALTER TABLE position
    ADD COLUMN stop_generation integer NOT NULL DEFAULT 0,
    ADD COLUMN adopted boolean NOT NULL DEFAULT false,
    ADD CONSTRAINT position_stop_generation_non_negative CHECK (stop_generation >= 0);

-- A stop may tighten past the entry price (break-even, then trailing). The old check forbade
-- that, so it has to go; the invariant that survives is "the stop is a positive price", which
-- position_prices_positive already states.
ALTER TABLE position DROP CONSTRAINT position_stop_below_entry;
"""

DOWNGRADE = r"""
-- A protective stop has no signed order to fall back on, so the rows this feature produces are
-- the rows it takes with it. A stop that a monitor tightened past entry would then fail the old
-- check: loud failure is right there, because silently widening a live stop is not.
UPDATE position SET stop_order_id = NULL
 WHERE stop_order_id IN (SELECT id FROM "order" WHERE is_protective);
DELETE FROM "order" WHERE is_protective;
ALTER TABLE position ADD CONSTRAINT position_stop_below_entry CHECK (stop_price < avg_entry_price);
ALTER TABLE position
    DROP CONSTRAINT position_stop_generation_non_negative,
    DROP COLUMN adopted,
    DROP COLUMN stop_generation;

ALTER TABLE "order"
    DROP CONSTRAINT order_protective_shape,
    DROP CONSTRAINT order_entry_has_signed_order,
    DROP COLUMN is_protective;

ALTER TABLE "order" ALTER COLUMN signed_order_id SET NOT NULL;
"""


def upgrade() -> None:
    apply_statements(op.get_bind(), UPGRADE)


def downgrade() -> None:
    apply_statements(op.get_bind(), DOWNGRADE)
