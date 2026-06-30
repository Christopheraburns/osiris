#!/usr/bin/env bash
# Deploy OSIRIS USGS earthquakes NiFi flow via REST API.
# Usage: bash nifi/deploy/import-earthquakes.sh
set -euo pipefail

BASE="https://localhost:8443/nifi-api"
USER="admin"
PASS="osirisadmin1"
USGS_URL="https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
SCRIPT_FILE="/opt/nifi/conf/osiris/scripts/earthquakes-ingest.groovy"
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
EXPORT="$ROOT_DIR/nifi/flows/osiris-earthquakes.json"

echo "Waiting for NiFi..."
for i in $(seq 1 60); do
  if curl -sk -o /dev/null -w "%{http_code}" -X POST "$BASE/access/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=$USER&password=$PASS" | grep -q 200; then
    break
  fi
  sleep 3
  if [ "$i" -eq 60 ]; then echo "NiFi not reachable"; exit 1; fi
done

TOKEN=$(curl -sk -X POST "$BASE/access/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=$USER&password=$PASS")
AUTH="Authorization: Bearer $TOKEN"
HDR=(-H "$AUTH" -H "Content-Type: application/json")

api() {
  local method=$1 path=$2
  shift 2
  curl -sk -X "$method" "$BASE$path" "${HDR[@]}" "$@"
}

echo "NiFi online. Creating process group..."
ROOT_ID=$(api GET /flow/process-groups/root | python -c "import sys,json; print(json.load(sys.stdin)['processGroupFlow']['id'])")

PG=$(api POST "/process-groups/$ROOT_ID/process-groups" -d '{
  "revision": {"version": 0},
  "component": {"name": "OSIRIS - Earthquakes (USGS)", "position": {"x": 400, "y": 200}}
}')
PG_ID=$(echo "$PG" | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Process group: $PG_ID"

new_proc() {
  local name=$1 type=$2 artifact=$3 x=$4 y=$5
  api POST "/process-groups/$PG_ID/processors" -d "{
    \"revision\": {\"version\": 0},
    \"component\": {
      \"name\": \"$name\",
      \"type\": \"$type\",
      \"bundle\": {\"group\": \"org.apache.nifi\", \"artifact\": \"$artifact\", \"version\": \"2.0.0\"},
      \"position\": {\"x\": $x, \"y\": $y}
    }
  }"
}

put_proc() {
  local id=$1 ver=$2 body=$3
  api PUT "/processors/$id" -d "$body"
}

GEN=$(new_proc "Poll USGS (5 min)" "org.apache.nifi.processors.standard.GenerateFlowFile" "nifi-standard-nar" 0 200)
GEN_ID=$(echo "$GEN" | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
GEN_VER=$(echo "$GEN" | python -c "import sys,json; print(json.load(sys.stdin)['revision']['version'])")
put_proc "$GEN_ID" "$GEN_VER" "{
  \"revision\": {\"version\": $GEN_VER},
  \"component\": {
    \"id\": \"$GEN_ID\",
    \"config\": {
      \"schedulingStrategy\": \"CRON_DRIVEN\",
      \"schedulingPeriod\": \"0 0/5 * * * ?\",
      \"concurrentlySchedulableTaskCount\": 1,
      \"properties\": {\"Batch Size\": \"1\"},
      \"autoTerminatedRelationships\": [\"failure\"]
    }
  }
}" > /dev/null

HTTP=$(new_proc "GET USGS GeoJSON" "org.apache.nifi.processors.standard.InvokeHTTP" "nifi-standard-nar" 400 200)
HTTP_ID=$(echo "$HTTP" | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
HTTP_VER=$(echo "$HTTP" | python -c "import sys,json; print(json.load(sys.stdin)['revision']['version'])")
put_proc "$HTTP_ID" "$HTTP_VER" "{
  \"revision\": {\"version\": $HTTP_VER},
  \"component\": {
    \"id\": \"$HTTP_ID\",
    \"config\": {
      \"autoTerminatedRelationships\": [\"failure\", \"no retry\", \"retry\"]
    }
  }
}" > /dev/null

SCR=$(new_proc "Transform to PolyBolos" "org.apache.nifi.processors.script.ExecuteScript" "nifi-scripting-nar" 800 200)
SCR_ID=$(echo "$SCR" | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
SCR_VER=$(echo "$SCR" | python -c "import sys,json; print(json.load(sys.stdin)['revision']['version'])")
put_proc "$SCR_ID" "$SCR_VER" "{
  \"revision\": {\"version\": $SCR_VER},
  \"component\": {
    \"id\": \"$SCR_ID\",
    \"config\": {
      \"properties\": {
        \"Script Engine\": \"Groovy\",
        \"Script File\": \"$SCRIPT_FILE\"
      },
      \"autoTerminatedRelationships\": [\"failure\"]
    }
  }
}" > /dev/null

KAFKA=$(new_proc "Publish osiris.entities" "org.apache.nifi.processors.kafka.publish.PublishKafka" "nifi-kafka-nar" 1200 200)
KAFKA_ID=$(echo "$KAFKA" | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
KAFKA_VER=$(echo "$KAFKA" | python -c "import sys,json; print(json.load(sys.stdin)['revision']['version'])")
put_proc "$KAFKA_ID" "$KAFKA_VER" "{
  \"revision\": {\"version\": $KAFKA_VER},
  \"component\": {
    \"id\": \"$KAFKA_ID\",
    \"config\": {
      \"properties\": {
        \"Kafka Brokers\": \"osiris-kafka:9092\",
        \"Topic Name\": \"osiris.entities\",
        \"Delivery Guarantee\": \"1\",
        \"Use Transactions\": \"false\",
        \"Message Key Field\": \"kafka.key\",
        \"Character Set\": \"UTF-8\"
      },
      \"autoTerminatedRelationships\": [\"failure\"]
    }
  }
}" > /dev/null

connect() {
  local src=$1 rel=$2 dst=$3
  api POST "/process-groups/$PG_ID/connections" -d "{
    \"revision\": {\"version\": 0},
    \"component\": {
      \"source\": {\"id\": \"$src\", \"groupId\": \"$PG_ID\", \"type\": \"PROCESSOR\"},
      \"destination\": {\"id\": \"$dst\", \"groupId\": \"$PG_ID\", \"type\": \"PROCESSOR\"},
      \"selectedRelationships\": [\"$rel\"]
    }
  }" > /dev/null
}

connect "$GEN_ID" "success" "$HTTP_ID"
connect "$HTTP_ID" "response" "$SCR_ID"
connect "$SCR_ID" "success" "$KAFKA_ID"

PG_STATE=$(api GET "/process-groups/$PG_ID")
PG_VER=$(echo "$PG_STATE" | python -c "import sys,json; print(json.load(sys.stdin)['revision']['version'])")
api PUT "/flow/process-groups/$PG_ID" -d "{\"revision\":{\"version\":$PG_VER},\"id\":\"$PG_ID\",\"state\":\"RUNNING\"}" > /dev/null

mkdir -p "$(dirname "$EXPORT")"
curl -sk -H "$AUTH" -o "$EXPORT" "$BASE/process-groups/$PG_ID/download"
echo "Exported flow to $EXPORT"
echo "Done. UI: https://localhost:8443/nifi/"
