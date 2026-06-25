# Hive Standalone Metastore for OSIRIS
# - Postgres JDBC driver (metadata store backend)
# - S3A support (hadoop-aws + AWS SDK) so the warehouse can live on Ozone
#   via the S3 gateway. Both jars already ship in the base image's Hadoop
#   tools/lib (Hadoop 3.3.6), so we copy rather than download them.

# Stage 1: fetch the Postgres JDBC driver (base image has no curl/wget).
FROM curlimages/curl:8.11.1 AS fetch
ARG POSTGRES_JDBC_VERSION=42.7.4
RUN curl -fSL -o /tmp/postgresql.jar \
    "https://repo1.maven.org/maven2/org/postgresql/postgresql/${POSTGRES_JDBC_VERSION}/postgresql-${POSTGRES_JDBC_VERSION}.jar"

FROM apache/hive:4.0.0
USER root

COPY --from=fetch /tmp/postgresql.jar /opt/hive/lib/postgresql.jar

# S3A connector + AWS SDK (versions ship with the image's Hadoop 3.3.6).
RUN cp /opt/hadoop/share/hadoop/tools/lib/hadoop-aws-3.3.6.jar /opt/hive/lib/ \
 && cp /opt/hadoop/share/hadoop/tools/lib/aws-java-sdk-bundle-1.12.367.jar /opt/hive/lib/

COPY conf/hive-site.xml /opt/hive/conf/hive-site.xml

USER hive
