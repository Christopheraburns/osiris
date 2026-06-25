SET 'execution.runtime-mode' = 'batch';
SET 'sql-client.execution.result-mode' = 'tableau';

CREATE CATALOG osiris_iceberg WITH (
  'type' = 'iceberg',
  'catalog-type' = 'hive',
  'uri' = 'thrift://hive-metastore:9083',
  'warehouse' = 's3a://osiris-lake/warehouse'
);

SELECT domain, COUNT(*) AS n FROM osiris_iceberg.lake.entities GROUP BY domain;
SELECT COUNT(*) AS total_rows FROM osiris_iceberg.lake.entities;
