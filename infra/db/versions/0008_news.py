"""news_item: what the free-feed pipeline stores, dedupes and screens.

`excluded_from_prompts` is not a cache of a screening decision that could be recomputed on
demand — it is the record that a human, an agent and the UI all read the same answer, and it
is set by `atlas_news.injection` with the rule ids in `injection_flags`.

Revision ID: 0008_news
"""

from __future__ import annotations

from alembic import op

from atlas_common.sqlsplit import apply_statements

revision = "0008_news"
down_revision = "0007_audit_canonical"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE news_item (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id text NOT NULL,
    source_name text NOT NULL,
    url_canonical text NOT NULL UNIQUE,
    url text NOT NULL,
    title text NOT NULL,
    summary text NOT NULL DEFAULT '',
    body_text text NOT NULL DEFAULT '',
    language text NOT NULL DEFAULT 'en' CHECK (language IN ('id', 'en')),
    published_at timestamptz,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    tickers text[] NOT NULL DEFAULT '{}',
    injection_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
    screen_version text NOT NULL DEFAULT '1',
    excluded_from_prompts boolean NOT NULL DEFAULT false,
    simhash bigint NOT NULL DEFAULT 0,
    duplicate_of uuid REFERENCES news_item (id),
    cluster_id uuid,
    CHECK (excluded_from_prompts = (jsonb_array_length(injection_flags) > 0)),
    CHECK (duplicate_of IS NULL OR duplicate_of <> id)
);

CREATE INDEX news_item_fetched_at_idx ON news_item (fetched_at DESC);
CREATE INDEX news_item_simhash_idx ON news_item (simhash) WHERE simhash <> 0;
CREATE INDEX news_item_window_idx ON news_item (published_at DESC NULLS LAST, fetched_at DESC);
CREATE INDEX news_item_tickers_idx ON news_item USING gin (tickers);
"""

DOWNGRADE = """
DROP TABLE news_item;
"""


def upgrade() -> None:
    apply_statements(op.get_bind(), UPGRADE)


def downgrade() -> None:
    apply_statements(op.get_bind(), DOWNGRADE)
