SET 'execution.runtime-mode' = 'batch';
SET 'sql-client.execution.result-mode' = 'tableau';

CREATE CATALOG osiris_iceberg WITH (
  'type' = 'iceberg',
  'catalog-type' = 'hive',
  'uri' = 'thrift://hive-metastore:9083',
  'warehouse' = 's3a://osiris-lake/warehouse'
);

-- Bronze (every message) and the two silver tables written by kafka_to_lakehouse.sql.
SELECT COUNT(*) AS raw_records FROM osiris_iceberg.lake.raw_records;
SELECT feed, COUNT(*) AS n FROM osiris_iceberg.lake.events GROUP BY feed;
SELECT COUNT(*) AS observations FROM osiris_iceberg.lake.observations;
