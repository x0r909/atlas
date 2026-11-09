"""Store the exact bytes that were hashed, and bind the record type into the digest.

Both changes come from a reliability review of the audit writer:

* the digest used to be ``sha256(prev ‖ canonical_payload ‖ seq)``, so ``record_type`` was
  outside it. A ``KILL_SWITCH`` row could be relabelled ``INCIDENT`` with a single UPDATE and
  still verify - the exact edit an operator who wants to hide a halt would make.
* the writer hashed canonical JSON text but stored ``CAST(:payload AS jsonb)``. jsonb
  normalises numbers (``1e20`` comes back as ``100000000000000000000``), so verification
  recomputed different bytes and reported "hash mismatch" on an honest record. The column
  below holds the hashed text verbatim; jsonb stays for querying.

Rows written before this migration were hashed with the v1 formula and will report a hash
mismatch. That is honest - the formula changed - not a corruption; a pre-v2 chain cannot be
re-verified by a v2 verifier.

Revision ID: 0007_audit_canonical
"""

from __future__ import annotations

from alembic import op

from atlas_common.sqlsplit import apply_statements

revision = "0007_audit_canonical"
down_revision = "0006_risk_config_seed"
branch_labels = None
depends_on = None

UPGRADE = """
-- The bytes that were actually hashed, kept verbatim. Nullable only so a v1 chain can still be
-- read; every writer since this migration fills it, and verification compares against it.
ALTER TABLE audit_record ADD COLUMN payload_canonical text;
COMMENT ON COLUMN audit_record.payload_canonical IS
    'exact canonical JSON text covered by hash; jsonb alone is not byte-stable';
"""

DOWNGRADE = """
ALTER TABLE audit_record DROP COLUMN payload_canonical;
"""


def upgrade() -> None:
    apply_statements(op.get_bind(), UPGRADE)


def downgrade() -> None:
    apply_statements(op.get_bind(), DOWNGRADE)
