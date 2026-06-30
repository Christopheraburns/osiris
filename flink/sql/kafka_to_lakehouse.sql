-- ════════════════════════════════════════════════════════════════════════
--  OSIRIS — Kafka -> Lakehouse (Pattern B: single source, atomic fan-out)
--
--  Reads the canonical osiris.entities messages ONCE (as the raw payload),
--  then a single STATEMENT SET writes all three Iceberg tables in one job /
--  one shared checkpoint lifecycle:
--    * lake.raw_records   — the exact payload (bronze, loss-less)
--    * lake.observations  — normalized tracks/readings (non-event feeds)
--    * lake.events        — normalized immutable occurrences (event feeds)
--
--  Canonical Kafka message (one JSON object per record on topic osiris.entities):
--    {
--      "schema_version": 1,
--      "ingest_run_id": "<uuid>",
--      "source": "flights",
--      "captured_at": "2026-06-24T20:45:51Z",
--      "entity": {
--        "id": "a1b2c3", "name": "OSY101", "domain": "AIR",
--        "entityType": "COMMERCIAL",
--        "position": {"lat":50.45,"lng":30.52,"alt":11000,"heading":270,"speed":450},
--        "threat":"NONE","classification":"UNCLASSIFIED","confidence":0.9,
--        "timestamp":"2026-06-24T20:45:50Z",
--        "source":{"provider":"osiris","feed":"flights","originalId":"a1b2c3","confidence":0.9},
--        "properties": { ... source-specific long tail ... }
--      }
--    }
--
--  Submit:
--    docker compose cp flink/sql/kafka_to_lakehouse.sql osiris-flink-jobmanager:/tmp/kafka_to_lakehouse.sql
--    docker compose exec osiris-flink-jobmanager ./bin/sql-client.sh -f /tmp/kafka_to_lakehouse.sql
-- ════════════════════════════════════════════════════════════════════════

-- Feeds emit UTC ISO-8601; interpret naive timestamps as UTC.
SET 'table.local-time-zone' = 'UTC';
SET 'execution.checkpointing.interval' = '10s';
SET 'pipeline.name' = 'osiris-kafka-to-lakehouse';

CREATE CATALOG osiris_iceberg WITH (
  'type' = 'iceberg',
  'catalog-type' = 'hive',
  'uri' = 'thrift://osiris-hive-metastore:9083',
  'warehouse' = 's3a://osiris-lake/warehouse'
);

-- Single source: the whole Kafka value as one UTF-8 string (preserves exact bytes).
CREATE TEMPORARY TABLE kafka_src (
  payload STRING
) WITH (
  'connector' = 'kafka',
  'topic' = 'osiris.entities',
  'properties.bootstrap.servers' = 'osiris-kafka:9092',
  'properties.group.id' = 'flink-lakehouse',
  'scan.startup.mode' = 'earliest-offset',
  'format' = 'raw'
);

-- Parse once. ISO-8601 'YYYY-MM-DDTHH:MM:SSZ' -> normalize -> TIMESTAMP -> LTZ (UTC).
CREATE TEMPORARY VIEW parsed AS
SELECT
  payload,
  JSON_VALUE(payload, '$.source')                                         AS source,
  JSON_VALUE(payload, '$.ingest_run_id')                                  AS ingest_run_id,
  CAST(JSON_VALUE(payload, '$.schema_version') AS INT)                    AS schema_version,
  CAST(TO_TIMESTAMP(REPLACE(REPLACE(JSON_VALUE(payload,'$.captured_at'),'T',' '),'Z','')) AS TIMESTAMP_LTZ(6)) AS captured_at,
  JSON_VALUE(payload, '$.entity.id')                                      AS entity_id,
  JSON_VALUE(payload, '$.entity.name')                                    AS name,
  JSON_VALUE(payload, '$.entity.domain')                                  AS domain,
  JSON_VALUE(payload, '$.entity.entityType')                              AS entity_type,
  CAST(JSON_VALUE(payload, '$.entity.position.lat') AS DOUBLE)            AS lat,
  CAST(JSON_VALUE(payload, '$.entity.position.lng') AS DOUBLE)            AS lng,
  CAST(JSON_VALUE(payload, '$.entity.position.alt') AS DOUBLE)            AS alt,
  CAST(JSON_VALUE(payload, '$.entity.position.heading') AS DOUBLE)        AS heading,
  CAST(JSON_VALUE(payload, '$.entity.position.speed') AS DOUBLE)          AS speed,
  JSON_VALUE(payload, '$.entity.threat')                                  AS threat,
  JSON_VALUE(payload, '$.entity.classification')                          AS classification,
  CAST(JSON_VALUE(payload, '$.entity.confidence') AS DOUBLE)             AS confidence,
  JSON_VALUE(payload, '$.entity.source.provider')                         AS provider,
  JSON_VALUE(payload, '$.entity.source.feed')                             AS feed,
  JSON_VALUE(payload, '$.entity.source.originalId')                       AS source_original_id,
  CAST(TO_TIMESTAMP(REPLACE(REPLACE(JSON_VALUE(payload,'$.entity.timestamp'),'T',' '),'Z','')) AS TIMESTAMP_LTZ(6)) AS event_time,
  JSON_QUERY(payload, '$.entity.properties')                              AS properties,
  CAST(JSON_VALUE(payload, '$.entity.properties.mag') AS DOUBLE)          AS magnitude,
  CAST(JSON_VALUE(payload, '$.entity.properties.brightness') AS DOUBLE)   AS brightness
FROM kafka_src;

EXECUTE STATEMENT SET
BEGIN
  -- Bronze: every message, exact payload.
  INSERT INTO osiris_iceberg.lake.raw_records
  SELECT
    source, payload, provider, feed, ingest_run_id, schema_version,
    captured_at, CURRENT_TIMESTAMP, CAST(CURRENT_TIMESTAMP AS DATE)
  FROM parsed;

  -- Silver observations: non-event feeds (tracks / readings).
  INSERT INTO osiris_iceberg.lake.observations
  SELECT
    entity_id, CAST(NULL AS STRING) AS canonical_id, domain, entity_type, name,
    lat, lng, alt, heading, speed, threat, classification, confidence,
    provider, feed, source_original_id,
    event_time, captured_at, CURRENT_TIMESTAMP, ingest_run_id, schema_version,
    properties, CAST(event_time AS DATE)
  FROM parsed
  WHERE source NOT IN ('earthquakes','fires','gdelt','news','live-news','weather');

  -- Silver events: immutable occurrences.
  INSERT INTO osiris_iceberg.lake.events
  SELECT
    entity_id AS event_id, entity_type AS event_type, domain, name,
    lat, lng, magnitude, brightness,
    provider, feed, source_original_id,
    event_time, captured_at, CURRENT_TIMESTAMP, ingest_run_id, schema_version,
    properties, CAST(event_time AS DATE)
  FROM parsed
  WHERE source IN ('earthquakes','fires','gdelt','news','live-news','weather');
END;
