-- ════════════════════════════════════════════════════════════════════════
--  OSIRIS Lakehouse schema (Iceberg on Ozone, HiveCatalog)
--
--  Layers:
--    lake.raw_records   bronze  — exact, loss-less payload as ingested
--                                 (named raw_records, not raw: RAW is a Flink
--                                  SQL reserved keyword)
--    lake.observations  silver  — normalized Tier-3 time-series (PolyBolos)
--    lake.events        silver  — normalized Tier-2 immutable occurrences
--
--  Design notes:
--    * format-version=2 (row-level deletes / upserts available later).
--    * Real timestamptz (TIMESTAMP WITH LOCAL TIME ZONE), never STRING.
--    * Two time semantics: event time (observed_at/occurred_at) vs processing
--      time (captured_at/ingested_at).
--    * Flink SQL only supports IDENTITY partitioning, so we add explicit DATE
--      partition columns (obs_date/event_date/ingest_date) populated by the
--      writer — gives time-based pruning with full Flink support. The partition
--      spec can still be evolved later via another engine if needed.
--    * `properties` / `payload` hold the source-specific long tail as JSON
--      text — new feeds need NO schema change. Promote a field to a typed
--      column only when you must query it (additive, no rebuild in Iceberg).
--    * canonical_id is present from day one (nullable) so entity resolution
--      can populate it later without a migration.
--
--  Apply with the Flink SQL client:
--    docker compose cp flink/sql/lakehouse_ddl.sql osiris-flink-jobmanager:/tmp/lakehouse_ddl.sql
--    docker compose exec osiris-flink-jobmanager ./bin/sql-client.sh -f /tmp/lakehouse_ddl.sql
-- ════════════════════════════════════════════════════════════════════════

CREATE CATALOG osiris_iceberg WITH (
  'type' = 'iceberg',
  'catalog-type' = 'hive',
  'uri' = 'thrift://osiris-hive-metastore:9083',
  'warehouse' = 's3a://osiris-lake/warehouse'
);

CREATE DATABASE IF NOT EXISTS osiris_iceberg.lake;

-- Retire the throwaway smoke-test table from the Kafka->Iceberg verification.
DROP TABLE IF EXISTS osiris_iceberg.lake.entities;

-- ── Bronze: raw, loss-less landing ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS osiris_iceberg.lake.raw_records (
  source         STRING,
  payload        STRING,                          -- exact message bytes (UTF-8)
  provider       STRING,
  feed           STRING,
  ingest_run_id  STRING,
  schema_version INT,
  captured_at    TIMESTAMP(6) WITH LOCAL TIME ZONE,
  ingested_at    TIMESTAMP(6) WITH LOCAL TIME ZONE,
  ingest_date    DATE                             -- partition: CAST(ingested_at AS DATE)
)
PARTITIONED BY (ingest_date)
WITH ('format-version' = '2');

-- ── Silver: normalized observations (Tier-3 time-series) ────────────────
CREATE TABLE IF NOT EXISTS osiris_iceberg.lake.observations (
  entity_id          STRING,
  canonical_id       STRING,                       -- nullable until entity resolution
  domain             STRING,
  entity_type        STRING,
  name               STRING,
  lat                DOUBLE,
  lng                DOUBLE,
  alt                DOUBLE,
  heading            DOUBLE,
  speed              DOUBLE,
  threat             STRING,
  classification     STRING,
  confidence         DOUBLE,
  provider           STRING,
  feed               STRING,
  source_original_id STRING,
  observed_at        TIMESTAMP(6) WITH LOCAL TIME ZONE,
  captured_at        TIMESTAMP(6) WITH LOCAL TIME ZONE,
  ingested_at        TIMESTAMP(6) WITH LOCAL TIME ZONE,
  ingest_run_id      STRING,
  schema_version     INT,
  properties         STRING,                       -- source-specific long tail (JSON)
  obs_date           DATE                          -- partition: CAST(observed_at AS DATE)
)
PARTITIONED BY (obs_date, domain)
WITH ('format-version' = '2');

-- ── Silver: normalized events (Tier-2 immutable occurrences) ────────────
CREATE TABLE IF NOT EXISTS osiris_iceberg.lake.events (
  event_id           STRING,
  event_type         STRING,
  domain             STRING,
  name               STRING,
  lat                DOUBLE,
  lng                DOUBLE,
  magnitude          DOUBLE,
  brightness         DOUBLE,
  provider           STRING,
  feed               STRING,
  source_original_id STRING,
  occurred_at        TIMESTAMP(6) WITH LOCAL TIME ZONE,
  captured_at        TIMESTAMP(6) WITH LOCAL TIME ZONE,
  ingested_at        TIMESTAMP(6) WITH LOCAL TIME ZONE,
  ingest_run_id      STRING,
  schema_version     INT,
  properties         STRING,
  event_date         DATE                          -- partition: CAST(occurred_at AS DATE)
)
PARTITIONED BY (event_date, event_type)
WITH ('format-version' = '2');
