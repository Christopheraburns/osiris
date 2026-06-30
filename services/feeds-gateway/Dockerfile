# OSIRIS Feeds Gateway — Kafka -> clean feed API (FastAPI/uvicorn/aiokafka).
#
# Consumes the NiFi -> Kafka entity stream (osiris.entities), reshapes records to
# feed-shaped JSON, and serves them to the OSIRIS server in SECURED CONNECTION
# mode. Also the future home of the streaming GraphRAG / LLM / MCP layer.
#
# Air-gap note: all wheels are installed at build time; nothing is fetched at
# runtime. Build while networked, then docker save/load into the air-gap.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# Defaults; override in docker-compose.
ENV KAFKA_BROKERS=osiris-kafka:9092 \
    OSIRIS_ENTITIES_TOPIC=osiris.entities \
    KAFKA_GROUP_ID=osiris-feeds-gateway \
    FEED_TTL_SECONDS=3600 \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

EXPOSE 8091

# Single worker: the in-memory snapshot must be owned by one consumer.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8091", "--workers", "1"]
