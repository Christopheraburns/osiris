#!/usr/bin/env bash
# A1 step 3 — bulk-load the generated CSVs into Neo4j (offline importer).
#
# IMPORTANT: `neo4j-admin database import full` loads into a STOPPED / empty
# database and OVERWRITES it. It is the only sane loader at tens of millions of
# nodes (LOAD CSV / neosemantics are far too slow). Because it overwrites, treat
# the Wikidata core as the FOUNDATION: import it first, then re-run the recorder
# rebuild + graph_enrich_layer1 to layer the live OSIRIS entities on top
# (recorder writes are idempotent MERGEs), then run link_to_osiris.cypher.
#
# Run inside the osiris-neo4j container (Neo4j 5.x):
#   docker compose cp tools/wikidata-a1/import osiris-neo4j:/var/lib/neo4j/import
#   docker compose stop neo4j        # importer needs the db offline
#   docker compose run --rm neo4j neo4j-admin database import full \
#       --nodes=/var/lib/neo4j/import/nodes.csv \
#       --relationships=/var/lib/neo4j/import/rels.csv \
#       --id-type=string --array-delimiter=';' \
#       --skip-bad-relationships=true --skip-duplicate-nodes=true \
#       --overwrite-destination=true neo4j
#   docker compose start neo4j
#
# This wrapper runs the same command when executed inside the container.
set -euo pipefail

IMPORT_DIR="${1:-/var/lib/neo4j/import}"
DB="${2:-neo4j}"

neo4j-admin database import full \
  --nodes="${IMPORT_DIR}/nodes.csv" \
  --relationships="${IMPORT_DIR}/rels.csv" \
  --id-type=string \
  --array-delimiter=';' \
  --skip-bad-relationships=true \
  --skip-duplicate-nodes=true \
  --overwrite-destination=true \
  "${DB}"

echo "imported. start neo4j, then:"
echo "  CREATE INDEX wd_namekey IF NOT EXISTS FOR (n:Wikidata) ON (n.nameKey);"
echo "  :play link_to_osiris.cypher"

# Neo4j 4.x variant:
#   neo4j-admin import --database=neo4j --id-type=string --array-delimiter=';' \
#     --skip-bad-relationships=true --skip-duplicate-nodes=true \
#     --nodes=nodes.csv --relationships=rels.csv
