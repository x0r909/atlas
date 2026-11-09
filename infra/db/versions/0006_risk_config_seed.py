"""0006 seeds the baseline risk config.

Every risk rule reads ``risk_config`` for its limits. Without a row the Risk Engine has no
policy to apply and the API has no version to change, so a fresh database needs version 1
with the defaults the spec states. Seeding here rather than in code keeps "which limits was
the system born with" answerable from the database alone.
"""

from __future__ import annotations

from alembic import op

from atlas_common.sqlsplit import apply_statements

revision = "0006_risk_config_seed"
down_revision = "0005_mode_state_rules"
branch_labels = None
depends_on = None

UPGRADE = r"""
INSERT INTO risk_config (version)
SELECT 1
WHERE NOT EXISTS (SELECT 1 FROM risk_config);
"""

# Only the row this migration created, and only if nobody has edited it since.
DOWNGRADE = r"""
DELETE FROM risk_config
WHERE version = 1
  AND NOT EXISTS (SELECT 1 FROM risk_config WHERE version > 1);
"""


def upgrade() -> None:
    apply_statements(op.get_bind(), UPGRADE)


def downgrade() -> None:
    apply_statements(op.get_bind(), DOWNGRADE)
