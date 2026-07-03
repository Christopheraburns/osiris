# Process Group Template: UpstreamJsonIngest

Reusable pattern for JSON/GeoJSON upstream feeds. Each feed is a **Process Group instance**
cloned from this template with feed-specific Parameter Context values.

## Pipeline

```
GenerateFlowFile (CRON from pollMs)
    -> InvokeHTTP (upstream.url)
    -> ExecuteScript (transform + normalize + split)
    -> PublishKafka (topic osiris.entities)
```

## Parameter Context (per feed instance)

| Parameter | Example (earthquakes) |
|-----------|------------------------|
| `feed.name` | `earthquakes` |
| `upstream.url` | `https://earthquake.usgs.gov/.../2.5_day.geojson` |
| `poll.cron` | `0 0/5 * * * ?` |
| `script.file` | `/opt/nifi/conf/osiris/scripts/earthquakes-ingest.groovy` |
| `kafka.topic` | `osiris.entities` |
| `kafka.brokers` | `osiris-kafka:9092` |

## First instance

See [`../flows/osiris-earthquakes.json`](../flows/osiris-earthquakes.json) — import via NiFi UI
or [`../deploy/import-earthquakes.ps1`](../deploy/import-earthquakes.ps1).

## Adding the next feed

1. Add entry to [`../sources.json`](../sources.json)
2. Add `nifi/scripts/transforms/<id>.groovy` (upstream -> OSIRIS API shape)
3. Clone earthquakes Process Group, swap URL + script + CRON
4. Export updated flow JSON to `nifi/flows/`
