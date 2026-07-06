"""OSIRIS Feeds Gateway — CAI Application entrypoint.
 
Launches the feeds-gateway FastAPI service (services/feeds-gateway/app.py) on
CDSW_APP_PORT under the CAI PBJ kernel. Same pattern as the Memgraph shim:
uvicorn runs in a worker thread (the PBJ Jupyter kernel already owns an asyncio
loop, so a top-level uvicorn.run()/asyncio.run() fails with "cannot be called
from a running event loop"), and signal handlers are disabled because
signal.signal() only works in the main thread.
 
The gateway's Kafka consumer starts via FastAPI's lifespan, so nothing else
needs launching — one uvicorn server, one worker, coherent in-memory snapshot.
 
Required Application env vars:
    KAFKA_BROKERS         e.g. intelligence-service-kafka-corebroker0.<...>.cloudera.site:9093
    OSIRIS_ENTITIES_TOPIC osiris-events
    KAFKA_USER            workload username   (used by your updated consumer.py)
    KAFKA_PASSWORD        workload password
Optional:
    FEEDS_GATEWAY_DIR     path to services/feeds-gateway (default below)
    LOG_LEVEL             gateway log level (default INFO)
 
Remember: enable "Unauthenticated Access" on this Application — the OSIRIS
securedProxy() sends no Authorization header.
"""
import asyncio
import importlib.util
import os
import subprocess
import sys
import threading
 
# ── 1) Locate the gateway package (flat imports: `import db`, `import consumer`) ──
GATEWAY_DIR = os.environ.get(
    "FEEDS_GATEWAY_DIR", "/home/cdsw/services/feeds-gateway"
)
if not os.path.isdir(GATEWAY_DIR):
    raise RuntimeError(
        f"feeds-gateway source not found at {GATEWAY_DIR} — "
        "git-pull the repo into the project or set FEEDS_GATEWAY_DIR"
    )
os.chdir(GATEWAY_DIR)
sys.path.insert(0, GATEWAY_DIR)
 
# ── 2) Install dependencies on cold start (skipped if already importable) ──
_REQUIRED = ("fastapi", "uvicorn", "aiokafka", "psycopg", "neo4j", "httpx")
if any(importlib.util.find_spec(m) is None for m in _REQUIRED):
    print("[launcher] installing feeds-gateway requirements ...", flush=True)
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet",
         "-r", os.path.join(GATEWAY_DIR, "requirements.txt")]
    )
 
import uvicorn  # noqa: E402  (after the conditional install)
 
# Importing the module registers routes + lifespan (which starts the Kafka
# consumer once uvicorn boots). Postgres logging (db.py) degrades to stdout in
# CAI — the osiris-metastore-db host doesn't exist here, and that's fine.
from app import app  # noqa: E402  gateway FastAPI instance
 
 
def _serve() -> None:
    """Run uvicorn in this worker thread's own event loop."""
    port = int(os.environ["CDSW_APP_PORT"])
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # not the main thread
    asyncio.run(server.serve())
 
 
# Start unconditionally (under the PBJ kernel this module is not "__main__");
# join() keeps the process alive so CAI sees a running application.
_server_thread = threading.Thread(target=_serve, name="uvicorn", daemon=False)
_server_thread.start()
_server_thread.join()