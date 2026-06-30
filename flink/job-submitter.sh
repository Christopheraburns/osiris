#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════
#  OSIRIS Flink job submitter / watchdog
#
#  The Flink session cluster has no HA, so a submitted SQL job is lost when the
#  jobmanager restarts. This sidecar keeps the streaming Kafka -> Iceberg job
#  alive: it waits for the jobmanager, ensures the lakehouse tables exist, and
#  (re)submits flink/sql/kafka_to_lakehouse.sql whenever the job is not present.
#
#  Mounts (see docker-compose flink-job-submitter service):
#    /opt/sql/lakehouse_ddl.sql
#    /opt/sql/kafka_to_lakehouse.sql
# ════════════════════════════════════════════════════════════════════════
set -uo pipefail

JOB_NAME="osiris-kafka-to-lakehouse"
DDL_SQL="/opt/sql/lakehouse_ddl.sql"
JOB_SQL="/opt/sql/kafka_to_lakehouse.sql"
CHECK_INTERVAL="${CHECK_INTERVAL:-30}"

# The jobmanager binds rest on 0.0.0.0; point this client at it by name via a
# private copy of the image config (preserves the Java 17 add-opens/exports).
export FLINK_CONF_DIR=/tmp/flink-conf
cp -r /opt/flink/conf "$FLINK_CONF_DIR"
{
  echo "rest.address: osiris-flink-jobmanager"
  echo "rest.port: 8081"
} >> "$FLINK_CONF_DIR/config.yaml"

log() { echo "[job-submitter] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

job_present() {
  /opt/flink/bin/flink list 2>/dev/null | grep -q "$JOB_NAME"
}

lakehouse_tables_exist() {
  local out
  out=$(/opt/flink/bin/sql-client.sh -e "SHOW TABLES IN osiris_iceberg.lake;" 2>&1) || return 1
  echo "$out" | grep -q raw_records
}

ensure_ddl() {
  log "ensuring lakehouse tables (DDL)"
  local out
  out=$(/opt/flink/bin/sql-client.sh -f "$DDL_SQL" 2>&1) || true
  echo "$out"

  if echo "$out" | grep -qiE 'MetaException|\[ERROR\]'; then
    log "DDL reported errors (is s3://osiris-lake created? run scripts/bootstrap-lakehouse.sh)"
    return 1
  fi

  if lakehouse_tables_exist; then
    log "lakehouse tables verified (raw_records present)"
    return 0
  fi

  log "DDL finished but raw_records not found in catalog"
  return 1
}

# ── Wait for the jobmanager REST to come up ──
log "waiting for jobmanager..."
for _ in $(seq 1 120); do
  if /opt/flink/bin/flink list >/dev/null 2>&1; then
    log "jobmanager reachable"
    break
  fi
  sleep 3
done

ddl_done=0

# ── Watchdog loop ──
while true; do
  if job_present; then
    ddl_done=1   # tables clearly exist if the job is running
    sleep "$CHECK_INTERVAL"
    continue
  fi

  log "job '$JOB_NAME' not found — (re)submitting"

  if [ "$ddl_done" -eq 0 ]; then
    if ensure_ddl; then
      ddl_done=1
    else
      log "DDL not ready; will retry"
      sleep "$CHECK_INTERVAL"
      continue
    fi
  fi

  if /opt/flink/bin/sql-client.sh -f "$JOB_SQL"; then
    log "submitted '$JOB_NAME'"
  else
    log "submit failed; will retry"
  fi

  sleep "$CHECK_INTERVAL"
done
