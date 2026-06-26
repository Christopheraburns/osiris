# OSIRIS Source-Provenance Map

> A route-by-route audit of where every OSIRIS feed actually gets its data, produced by
> reading the upstream `fetch()` targets in each `src/app/api/*` route handler and the
> bundled `intel/server.js`. Purpose: confirm what the platform depends on and identify
> the **direct authoritative endpoint** to capture from for an air-gapped build.

## Headline finding

**OSIRIS has no hidden runtime dependency on the original creator's cloud infrastructure for
intelligence data.** The UI calls the project's own `/api/*` routes (open-source code in
your own container), and those routes fetch directly from third-party sources.  The creator domain
`osirisai.live` appears only as branding (a User-Agent string, an SEO `SITE_URL`, and a
UI fallback link) — **nothing fetches data from it.**

Every source below is a third party you can reach and re-point yourself.

## Classification legend

- **PRIMARY** — authoritative / canonical origin (government, official operator, or the
  data's system of record). Safe and preferred to mirror.
- **AGGREGATOR** — a legitimate third party that republishes or derives from other
  origins; usable, but not the system of record. Note ToS for redistribution.
- **CREATOR-PROXY** — a local OSIRIS container that normalizes upstream open sources.
  Auditable and replaceable; not an external dependency.

---

## Master map (route → upstream → capture)

| Route | Upstream source(s) | Class | Air-gap capture approach |
|---|---|---|---|
| `earthquakes` | `earthquake.usgs.gov` GeoJSON summary feeds | PRIMARY | Mirror USGS GeoJSON (2.5_day / 4.5_day). NiFi `InvokeHTTP`, periodic. |
| `infrastructure` | `earthquake.usgs.gov` 4.5_day feed (reused) | PRIMARY | Same USGS mirror; route derives infra context from it. |
| `country-risk` | `earthquake.usgs.gov` 4.5_day feed (reused) | PRIMARY | Same USGS mirror. |
| `scm-suppliers` | `earthquake.usgs.gov` 4.5_day feed (reused) | PRIMARY | Same USGS mirror. |
| `fires` | `firms.modaps.eosdis.nasa.gov` (MODIS/VIIRS CSV), `eonet.gsfc.nasa.gov` (volcanoes) | PRIMARY | Mirror NASA FIRMS 24h CSV + EONET API. FIRMS bulk download is air-gap-friendly. |
| `weather` | `api.weather.gov` (NWS alerts), `eonet.gsfc.nasa.gov` | PRIMARY | Mirror NWS active alerts + EONET. |
| `space-weather` | `services.swpc.noaa.gov` (alerts, GOES x-ray, planetary K) | PRIMARY | Mirror NOAA SWPC JSON. |
| `air-quality` | `api.openaq.org/v2/latest` | PRIMARY | OpenAQ has bulk/S3 archives — mirror those. |
| `satellites` | `celestrak.org` (GP/TLE catalogs), `db.satnogs.org` (TLE) | PRIMARY | **Mirror full CelesTrak catalog** (bulk TLE) — ideal reference dataset. |
| `flights` | `api.adsb.lol`, `api.airplanes.live`, `opensky-network.org/api` | AGGREGATOR | APIs; also mirror OpenSky downloadable aircraft DB as reference. |
| `cyber-threats` | `cisa.gov` KEV JSON, `dashboard.shadowserver.org` | PRIMARY (CISA) / AGGREGATOR (Shadowserver) | Mirror CISA KEV (bulk JSON); Shadowserver map stats via API. |
| `malware` | `feodotracker.abuse.ch`, `urlhaus.abuse.ch`, `ip-api.com` (geo) | AGGREGATOR | Mirror abuse.ch blocklists (bulk JSON/CSV). Replace ip-api geo with local GeoIP. |
| `gdelt` | `api.gdeltproject.org/api/v2/geo` | AGGREGATOR | GDELT offers bulk event exports — mirror those rather than the geo API. |
| `frontlines` | `deepstatemap.live/api/history/last` | AGGREGATOR | Single-source community map; snapshot the API response. No primary equivalent. |
| `radar` (internet outages) | `api.ioda.inetintel.cc.gatech.edu` | PRIMARY (Georgia Tech IODA) | Snapshot IODA outage events API. |
| `markets` | `api.coingecko.com`, `query1/2.finance.yahoo.com` | AGGREGATOR | Snapshot quotes; both are redistribution-restricted (see ToS notes). |
| `crypto` | `api.coingecko.com` | AGGREGATOR | Snapshot CoinGecko prices. |
| `news` | `feeds.bbci.co.uk`, `aljazeera.com` RSS, `gdacs.org` RSS, `t.me` (Telegram) | PRIMARY (publishers) / AGGREGATOR | Capture RSS XML on low side; GDACS is authoritative for disasters. |
| `live-news` | `youtube.com`, `rumble.com` live streams | AGGREGATOR | Streams can't cross an air gap live; capture/transcode clips if needed. |
| `region-dossier` | `en.wikipedia.org` REST, `query.wikidata.org` SPARQL, `nominatim.openstreetmap.org`, `restcountries.com` | PRIMARY | Mirror Wikipedia/Wikidata dumps + a local Nominatim/OSM extract + restcountries JSON. |
| `sentinel` (imagery) | `catalogue.dataspace.copernicus.eu` (STAC), `earth-search.aws.element84.com` | PRIMARY (Copernicus) | STAC search + selective scene pull; imagery is heavy — scope tightly. |
| `geo` (IP geolocation) | `ip-api.com`, `freeipapi.com`, `ipapi.co` | AGGREGATOR | Replace entirely with a local **MaxMind/DB-IP** GeoIP database in-enclave. |
| `osint/*` | `bgpview.io`, `api.github.com`, `xposedornot.com`, `check.torproject.org`, `crt.sh`, `cve.circl.lu`, `cveawg.mitre.org`, `dns.google`, `internetdb.shodan.io`, `macvendors.co`, `otx.alienvault.com`, `rdap.org` | MIXED | Per-source: see OSINT breakdown below. |
| `cctv` | ~40 official transport/traffic-cam providers + public livestreams | PRIMARY (gov cams) / AGGREGATOR (streams) | See CCTV note below. |
| `entity` (enrichment) | `osiris-intel:4000` → OpenSanctions, Wikidata, RIPEstat, ip-api | CREATOR-PROXY | Audit/replace `intel/server.js`; mirror its upstreams (below). |
| `recorder` | `osiris-recorder:8090` (lakehouse control plane) | CREATOR-PROXY | Internal only; writes to Iceberg/Neo4j. No external data. |
| `scanner` | `SCANNER_URL` (empty by default — optional external) | OPTIONAL | Off unless configured; supply your own backend. |

---

## OSINT sub-route breakdown (`osint/*`)

| Capability | Upstream | Class | Capture note |
|---|---|---|---|
| ASN / IP routing | `api.bgpview.io`, `stat.ripe.net` (via intel) | PRIMARY (RIPE) / AGGREGATOR (BGPView) | Prefer RIPEstat (authoritative RIR data). |
| Certificates | `crt.sh` | PRIMARY (CT logs) | Snapshot per-domain; or run a local CT-log mirror. |
| CVE lookup | `cveawg.mitre.org`, `cve.circl.lu` | PRIMARY (MITRE) / AGGREGATOR (CIRCL) | Mirror **NVD/MITRE bulk JSON** feeds. |
| DNS | `dns.google` | AGGREGATOR | Use a local resolver in-enclave. |
| Domain RDAP | `rdap.org` | PRIMARY (RDAP bootstrap) | Snapshot; RDAP is the registry system of record. |
| Tor exit nodes | `check.torproject.org/torbulkexitlist` | PRIMARY | Mirror the bulk exit list (single file). |
| IP exposure | `internetdb.shodan.io` | AGGREGATOR (free tier) | ToS-sensitive; snapshot only what you query. |
| Threat intel | `otx.alienvault.com` | AGGREGATOR (free tier) | Account/ToS-bound; mirror subscribed pulses. |
| Breach check | `api.xposedornot.com` | AGGREGATOR | Snapshot per-query. |
| MAC vendor | `macvendors.co` | AGGREGATOR | Replace with the local IEEE OUI registry. |
| GitHub profile | `api.github.com` | PRIMARY | Snapshot per-query. |

---

## Creator-maintained components

### `osiris-intel` (entity enrichment, `intel/server.js`)
Backs the `entity/expand` route. Real upstreams:

- `data.opensanctions.org/datasets/latest/us_ofac_sdn/targets.simple.csv` — **OFAC SDN sanctions** (PRIMARY; bulk CSV — ideal to mirror)
- `query.wikidata.org/sparql`, `www.wikidata.org/w/api.php` — **Wikidata** (PRIMARY)
- `stat.ripe.net/data/{abuse-contact-finder,network-info,whois}` — **RIPEstat** (PRIMARY)
- `ip-api.com/json` — IP geolocation (AGGREGATOR; replace with local GeoIP)

It normalizes these into the ontology; it does **not** originate data or call any
creator-hosted server. For the air gap you can either mirror these four upstreams and keep
the container, or re-implement its transform logic if you'd rather not trust third-party
code. `osirisai.live` here is only the SPARQL User-Agent string.

### `osiris-recorder` (lakehouse writer, `tools/recorder`)
Control-plane service the `recorder` route proxies. Its compose env already wires it to
**Hive Metastore** (`thrift://hive-metastore:9083`), an **Ozone S3 gateway**
(`http://ozone-s3g:9878`, warehouse `s3a://osiris-lake/warehouse`), and **Neo4j**
(`bolt://neo4j:7687`) — i.e., the Iceberg + graph stack is already partly scaffolded in
the repo. It consumes no external internet data.

---

## ToS / redistribution flags (important in a government context)

- **CoinGecko, Yahoo Finance, Shodan InternetDB, AlienVault OTX, MarineTraffic/FlightAware**
  (the latter two are detail-panel deep links, not feeds) have terms that restrict
  caching/redistribution or require attribution/keys. Review before mirroring.
- **YouTube / Rumble / Telegram** live content can't be served across an air gap live and
  carries platform ToS; treat as out-of-scope or capture clips deliberately.
- **Government/official sources** (USGS, NASA, NOAA, NWS, CISA, Copernicus, MITRE, RIPE,
  CT logs, OFAC) are the safest to mirror and should be the backbone of the air-gap build.

---

## Air-gap capture priorities (derived from this map)

1. **Mirror authoritative bulk datasets** (highest value, lowest risk): USGS feeds,
   NASA FIRMS/EONET, NOAA SWPC, NWS, CelesTrak full catalog, CISA KEV, abuse.ch blocklists,
   OFAC SDN, MITRE/NVD CVE, Tor exit list, IEEE OUI, Wikipedia/Wikidata dumps.
2. **Replace live lookup services with in-enclave equivalents**: local GeoIP database
   (for `geo`, `ip-api` uses), local DNS resolver, local Nominatim/OSM.
3. **Snapshot per-query aggregators** where no bulk mirror exists: BGPView/RIPEstat,
   crt.sh, RDAP, IODA, deepstatemap, GitHub.
4. **Audit or replace** `osiris-intel` so the enrichment path points at your mirrored
   OFAC/Wikidata/RIPE copies, not the live internet.
5. **Scope heavy/ToS-restricted sources** (Copernicus imagery, market data, Shodan/OTX)
   deliberately rather than mirroring wholesale.

Companion to the capture plan and the knowledge-graph / lakehouse schemas.
