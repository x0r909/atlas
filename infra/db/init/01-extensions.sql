-- Runs once, when the data directory is created (docker-entrypoint-initdb.d).
--
-- TimescaleDB ships as a binary in the -ha image but is not enabled per database, so
-- the hypertable and continuous-aggregate DDL in 0002_market.sql fails without this.
CREATE EXTENSION IF NOT EXISTS timescaledb;
-- pgvector arrives as a separate extension in the same image.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Guard rail: nothing in ATLAS writes a timestamp without a timezone.
ALTER DATABASE atlas SET timezone TO 'UTC';
