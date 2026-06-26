# NiFi — OSIRIS upstream ingest

NiFi collects from **authoritative upstream sources** (not OSIRIS `/api/*`) and publishes
canonical PolyBolos messages to Kafka topic `osiris.entities`.

## Prerequisites

```powershell
docker compose up -d nifi kafka
```

Wait ~30s for NiFi HTTPS on https://localhost:8443/nifi/ (login: `admin` / `osirisadmin1`).

Create Kafka topic (once):

```powershell
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create --if-not-exists `
  --topic osiris.entities --bootstrap-server localhost:9092 `
  --partitions 1 --replication-factor 1
```

## Deploy earthquakes flow (USGS)

**Git Bash / WSL:**

```bash
bash nifi/deploy/import-earthquakes.sh
```

**Or import manually:** NiFi UI → canvas → upload [`flows/osiris-earthquakes.json`](flows/osiris-earthquakes.json)
(right-click root → Upload flow definition).

## Pipeline

```
Poll (5 min) → GET earthquake.usgs.gov → Groovy transform → PublishKafka osiris.entities
```

Scripts mounted at `/opt/nifi/conf/osiris/scripts/` from [`scripts/`](scripts/).

## Verify Kafka output

```powershell
docker exec kafka /opt/kafka/bin/kafka-console-consumer.sh `
  --bootstrap-server localhost:9092 --topic osiris.entities `
  --from-beginning --max-messages 3
```

Expect JSON with `"source":"earthquakes"` and `"entityType":"SEISMIC"`.

## Template

See [`templates/UpstreamJsonIngest.md`](templates/UpstreamJsonIngest.md) — clone the earthquakes
Process Group for the next upstream feed.
