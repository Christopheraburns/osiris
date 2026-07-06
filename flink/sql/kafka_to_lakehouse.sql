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
SET 'table.local-time-zone' = 'UTC';

-- Read the whole Kafka value as one JSON string.
CREATE TEMPORARY TABLE IF NOT EXISTS osiris_events_raw (
    payload STRING
) WITH (
    'connector' = 'kafka',
    'topic' = 'osiris-events',
    'scan.startup.mode' = 'earliest-offset',
    'format' = 'raw',
    'properties.group.id' = 'flink-lakehouse-events',
    -- ↓↓ paste bootstrap.servers / security.protocol / sasl.* / ssl.* from your working table ↓↓
    'properties.bootstrap.servers' = 'intelligence-service-kafka-corebroker0.se-sandb.a465-9q4k.cloudera.site:9093,
    intelligence-service-kafka-corebroker2.se-sandb.a465-9q4k.cloudera.site:9093,
    intelligence-service-kafka-corebroker1.se-sandb.a465-9q4k.cloudera.site:9093',
    'properties.security.protocol' = 'SASL_SSL',
    'properties.sasl.mechanism' = 'PLAIN',
    'properties.sasl.jaas.config' = 'org.apache.kafka.common.security.plain.PlainLoginModule required username="cburns" password="SuperSecret#1";'
    -- ,'properties.ssl.truststore.location' = '...' -- include if your working table has them
    -- ,'properties.ssl.truststore.password' = '...'
);

INSERT INTO events_iceberg
SELECT
    JSON_VALUE(payload, '$.entity.id') AS event_id,
    COALESCE(JSON_VALUE(payload,'$.entity.source.originalId'), JSON_VALUE(payload,'$.entity.id')) AS asset_id, JSON_VALUE(payload, '$.entity.entityType') AS asset_type,
    CAST(JSON_VALUE(payload, '$.entity.position.lat') AS DOUBLE) AS lat,
    CAST(JSON_VALUE(payload, '$.entity.position.lng') AS DOUBLE) AS lon,
    CAST(TO_TIMESTAMP(REPLACE(REPLACE(JSON_VALUE(payload,'$.entity.timestamp'),'T',' '),'Z','')) AS TIMESTAMP(6)) AS event_time,
    CAST(LOCALTIMESTAMP AS TIMESTAMP(6)) AS ingest_time,
    JSON_VALUE(payload, '$.trace_id') AS trace_id,
    JSON_VALUE(payload, '$.ingest_run_id') AS ingest_run_id,
    JSON_VALUE(payload, '$.entity.source.provider') AS source_provider,
    JSON_VALUE(payload, '$.entity.source.feed') AS source_feed,
    JSON_VALUE(payload, '$.captured_at') AS captured_at,
    CAST(JSON_VALUE(payload, '$.schema_version') AS BIGINT) AS schema_version,
    CAST(JSON_VALUE(payload, '$.entity.confidence') AS DOUBLE) AS source_confidence,
    payload AS payload
FROM osiris_events_raw;