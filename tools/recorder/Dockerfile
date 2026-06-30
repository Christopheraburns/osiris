# OSIRIS Recorder — direct-API -> Iceberg lakehouse (+ Neo4j projection).
#
# Polls the OSIRIS /api/* routes, normalizes to the PolyBolos shape, appends to
# the Iceberg tables (lake.raw_records / observations / events) via PyIceberg
# (Hive catalog + Ozone S3 gateway), and projects durable entities into Neo4j.
#
# Air-gap note: all wheels are installed at build time; nothing is fetched at
# runtime. Build while networked, then `docker save`/`load` into the air-gap.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY endpoints.json ./

# Defaults; override in docker-compose.
ENV OSIRIS_BASE_URL=http://osiris:3000 \
    ICEBERG_URI=thrift://osiris-hive-metastore:9083 \
    ICEBERG_WAREHOUSE=s3a://osiris-lake/warehouse \
    S3_ENDPOINT=http://osiris-ozone-s3g:9878 \
    S3_ACCESS_KEY=osiris \
    S3_SECRET_KEY=osiris \
    NEO4J_URI=bolt://osiris-neo4j:7687 \
    NEO4J_USER=neo4j \
    CONTROL_PORT=8090 \
    MODE=idle \
    PYTHONUNBUFFERED=1

EXPOSE 8090

ENTRYPOINT ["python", "-m", "recorder"]
CMD ["--mode", "idle"]
