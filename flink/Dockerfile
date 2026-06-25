# Flink 1.20 for OSIRIS with Iceberg (HiveCatalog) + Kafka + S3A/Ozone.
#
# Iceberg's Flink HiveCatalog needs the Hive Metastore client and Hadoop on the
# classpath. We reuse the exact Hadoop 3.3.6 that ships in the Hive image so the
# s3a connector versions line up with the metastore.

FROM apache/hive:4.0.0 AS hive

FROM flink:1.20-java17
USER root

# Hadoop 3.3.6 (same build as the Hive Metastore image) for the Hive connector + s3a.
ENV HADOOP_HOME=/opt/hadoop
ENV HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop
COPY --from=hive /opt/hadoop /opt/hadoop
# S3A endpoint/credentials for Ozone; loaded from the classpath by Iceberg/Hadoop.
COPY conf/core-site.xml /opt/hadoop/etc/hadoop/core-site.xml
ENV HADOOP_CLASSPATH=/opt/hadoop/etc/hadoop:/opt/hadoop/share/hadoop/common/lib/*:/opt/hadoop/share/hadoop/common/*:/opt/hadoop/share/hadoop/hdfs:/opt/hadoop/share/hadoop/hdfs/lib/*:/opt/hadoop/share/hadoop/hdfs/*:/opt/hadoop/share/hadoop/mapreduce/*:/opt/hadoop/share/hadoop/yarn:/opt/hadoop/share/hadoop/yarn/lib/*:/opt/hadoop/share/hadoop/yarn/*

ARG ICEBERG_VERSION=1.9.1
ARG KAFKA_CONNECTOR_VERSION=3.4.0-1.20
ARG HIVE_CONNECTOR_VERSION=1.20.1

# S3A connector + AWS SDK (from the bundled Hadoop tools/lib, version-matched).
RUN cp /opt/hadoop/share/hadoop/tools/lib/hadoop-aws-3.3.6.jar /opt/flink/lib/ \
 && cp /opt/hadoop/share/hadoop/tools/lib/aws-java-sdk-bundle-1.12.367.jar /opt/flink/lib/

# Iceberg runtime + Flink SQL connectors.
RUN cd /opt/flink/lib && \
    curl -fSL -O "https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-flink-runtime-1.20/${ICEBERG_VERSION}/iceberg-flink-runtime-1.20-${ICEBERG_VERSION}.jar" && \
    curl -fSL -O "https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/${KAFKA_CONNECTOR_VERSION}/flink-sql-connector-kafka-${KAFKA_CONNECTOR_VERSION}.jar" && \
    curl -fSL -O "https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-hive-3.1.3_2.12/${HIVE_CONNECTOR_VERSION}/flink-sql-connector-hive-3.1.3_2.12-${HIVE_CONNECTOR_VERSION}.jar"
