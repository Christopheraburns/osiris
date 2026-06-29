#!/usr/bin/env bash
# A1 step 1 — class-filter the full Wikidata JSON dump down to the five classes.
#
# Prereq (connected side):
#   npm install -g wikibase-dump-filter
#   download the full dump (~140 GB compressed, CC0):
#   https://dumps.wikimedia.org/wikidatawiki/entities/latest-all.json.gz
#
# Usage:
#   ./filter.sh latest-all.json.gz filtered.ndjson.gz
#
# This is a multi-hour streaming pass; output is far smaller than the input.
set -euo pipefail

DUMP="${1:-latest-all.json.gz}"
OUT="${2:-filtered.ndjson.gz}"

# Country | Airline | Ship | Organization(business+org) | Human
CLASSES="Q6256,Q46970,Q11446,Q4830453,Q43229,Q5"

# NOTE: the OR-separator for multiple values is a comma in current
# wikibase-dump-filter; if your version differs, see `wikibase-dump-filter --help`.
gzip -dc "$DUMP" \
  | wikibase-dump-filter --claim "P31:${CLASSES}" \
  | gzip > "$OUT"

echo "filtered class subset -> $OUT"
echo "next: python3 transform.py $OUT import   (set WIKIDATA_HUMANS=all for the unbounded variant)"
