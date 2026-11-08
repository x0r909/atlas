-- Runs once, when the data directory is created (docker-entrypoint-initdb.d).
-- TimescaleDB is preloaded by the image; pgvector must be created explicitly.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Guard rail: nothing in ATLAS writes a timestamp without a timezone.
ALTER DATABASE atlas SET timezone TO 'UTC';
