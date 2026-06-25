-- OSIRIS verification job: Kafka -> Iceberg (HiveCatalog, warehouse on Ozone).

SET 'execution.checkpointing.interval' = '5s';

CREATE CATALOG osiris_iceberg WITH (
  'type' = 'iceberg',
  'catalog-type' = 'hive',
  'uri' = 'thrift://hive-metastore:9083',
  'warehouse' = 's3a://osiris-lake/warehouse'
);

CREATE DATABASE IF NOT EXISTS osiris_iceberg.lake;

CREATE TABLE IF NOT EXISTS osiris_iceberg.lake.entities (
  source      STRING,
  captured_at STRING,
  domain      STRING,
  entity_id   STRING,
  name        STRING,
  lat         DOUBLE,
  lng         DOUBLE
);

CREATE TEMPORARY TABLE kafka_entities (
  source      STRING,
  captured_at STRING,
  domain      STRING,
  entity_id   STRING,
  name        STRING,
  lat         DOUBLE,
  lng         DOUBLE
) WITH (
  'connector' = 'kafka',
  'topic' = 'osiris.entities',
  'properties.bootstrap.servers' = 'kafka:9092',
  'properties.group.id' = 'flink-osiris',
  'scan.startup.mode' = 'earliest-offset',
  'format' = 'json',
  'json.ignore-parse-errors' = 'true'
);

INSERT INTO osiris_iceberg.lake.entities SELECT * FROM kafka_entities;
