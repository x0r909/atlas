"""0002 market: bars hypertable, rollups, quotes, snapshots, health.

Retention and compression are part of the schema, not an afterthought: an unbounded
market table fills the VPS disk and takes the audit writer with it.
"""

from __future__ import annotations

from alembic import op

from atlas_common.sqlsplit import apply_statements

revision = "0002_market"
down_revision = "0001_core"
branch_labels = None
depends_on = None


UPGRADE = """
CREATE TYPE bar_source AS ENUM ('LIVE', 'BACKFILL', 'SYNTHETIC');
CREATE TYPE health_status AS ENUM ('OK', 'STALE', 'DOWN');

CREATE TABLE market_bar (
    venue_id    text NOT NULL REFERENCES venue (id),
    pair        text NOT NULL,
    timeframe   text NOT NULL,
    ts          timestamptz NOT NULL,
    open        numeric(20,8) NOT NULL,
    high        numeric(20,8) NOT NULL,
    low         numeric(20,8) NOT NULL,
    close       numeric(20,8) NOT NULL,
    volume      numeric(28,10) NOT NULL DEFAULT 0,
    trade_count int NOT NULL DEFAULT 0,
    source      bar_source NOT NULL DEFAULT 'LIVE',
    ingested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (venue_id, pair, timeframe, ts),
    CONSTRAINT market_bar_price_order CHECK (
        high >= low AND high >= open AND high >= close AND low <= open AND low <= close
        AND low > 0
    ),
    CONSTRAINT market_bar_volume_non_negative CHECK (volume >= 0 AND trade_count >= 0)
);

SELECT create_hypertable(
    'market_bar', 'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

ALTER TABLE market_bar SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'venue_id, pair, timeframe',
    timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('market_bar', INTERVAL '7 days', if_not_exists => TRUE);
SELECT add_retention_policy('market_bar', INTERVAL '90 days', if_not_exists => TRUE);

CREATE MATERIALIZED VIEW market_bar_5m
WITH (timescaledb.continuous) AS
SELECT
    venue_id,
    pair,
    time_bucket(INTERVAL '5 minutes', ts) AS bucket,
    first(open, ts) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, ts) AS close,
    sum(volume) AS volume,
    sum(trade_count) AS trade_count
FROM market_bar
GROUP BY venue_id, pair, bucket
WITH NO DATA;

CREATE MATERIALIZED VIEW market_bar_1h
WITH (timescaledb.continuous) AS
SELECT
    venue_id,
    pair,
    time_bucket(INTERVAL '1 hour', ts) AS bucket,
    first(open, ts) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, ts) AS close,
    sum(volume) AS volume,
    sum(trade_count) AS trade_count
FROM market_bar
GROUP BY venue_id, pair, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy('market_bar_5m',
    start_offset => INTERVAL '1 day',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE);
SELECT add_continuous_aggregate_policy('market_bar_1h',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE);
SELECT add_retention_policy('market_bar_5m', INTERVAL '2 years', if_not_exists => TRUE);
SELECT add_retention_policy('market_bar_1h', INTERVAL '2 years', if_not_exists => TRUE);

CREATE TABLE quote_snapshot (
    venue_id    text NOT NULL REFERENCES venue (id),
    pair        text NOT NULL,
    bid         numeric(20,8) NOT NULL,
    ask         numeric(20,8) NOT NULL,
    bid_size    numeric(28,10) NOT NULL,
    ask_size    numeric(28,10) NOT NULL,
    venue_ts    timestamptz,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    seq         bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (venue_id, pair),
    CONSTRAINT quote_snapshot_not_crossed CHECK (bid > 0 AND ask > 0 AND bid <= ask),
    CONSTRAINT quote_snapshot_sizes_non_negative CHECK (bid_size >= 0 AND ask_size >= 0)
);
CREATE INDEX quote_snapshot_ingested_idx ON quote_snapshot (ingested_at DESC);

CREATE TABLE order_book_snapshot (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_id    text NOT NULL REFERENCES venue (id),
    pair        text NOT NULL,
    ts          timestamptz NOT NULL,
    bids        jsonb NOT NULL,
    asks        jsonb NOT NULL,
    checksum    text,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT order_book_snapshot_bids_is_array CHECK (jsonb_typeof(bids) = 'array'),
    CONSTRAINT order_book_snapshot_asks_is_array CHECK (jsonb_typeof(asks) = 'array')
);
CREATE INDEX order_book_snapshot_pair_ts_idx ON order_book_snapshot (pair, ts DESC);

CREATE TABLE data_health (
    source_id            text PRIMARY KEY,
    last_message_at      timestamptz,
    expected_interval_s  int NOT NULL,
    status               health_status NOT NULL DEFAULT 'DOWN',
    consecutive_failures int NOT NULL DEFAULT 0,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT data_health_interval_positive CHECK (expected_interval_s > 0),
    CONSTRAINT data_health_failures_non_negative CHECK (consecutive_failures >= 0)
);
"""

DOWNGRADE = """
DROP TABLE IF EXISTS data_health;
DROP TABLE IF EXISTS order_book_snapshot;
DROP TABLE IF EXISTS quote_snapshot;
DROP MATERIALIZED VIEW IF EXISTS market_bar_1h;
DROP MATERIALIZED VIEW IF EXISTS market_bar_5m;
DROP TABLE IF EXISTS market_bar;
DROP TYPE IF EXISTS health_status;
DROP TYPE IF EXISTS bar_source;
"""


def upgrade() -> None:
    apply_statements(op.get_bind(), UPGRADE)


def downgrade() -> None:
    apply_statements(op.get_bind(), DOWNGRADE)
