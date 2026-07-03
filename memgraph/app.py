"""OSIRIS Memgraph Application entrypoint for Cloudera AI.
 
Starts Memgraph (Bolt on localhost, durable to the project mount) and exposes a
thin HTTP shim on CDSW_APP_PORT. Cross-project callers (OSIRIS, the MCP layer)
talk HTTP to this shim; only in-container/in-project code touches Bolt directly.
 
Optional auth: set the MEMGRAPH_API_TOKEN env var to require
`Authorization: Bearer <token>` on /cypher. Unset = open (fine for local testing).
"""
import asyncio
import os
import subprocess
import threading
import time
 
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from neo4j import GraphDatabase
 
DATA_DIR = "/home/cdsw/memgraph-data"
os.makedirs(DATA_DIR, exist_ok=True)
 
API_TOKEN = os.environ.get("MEMGRAPH_API_TOKEN")
 
# 1) Start Memgraph. Bolt on localhost only; durability + logs in the project mount
#    (the .deb's default /var/log/memgraph is owned by 'memgraph', not 'cdsw').
subprocess.Popen([
    "/usr/lib/memgraph/memgraph",
    f"--data-directory={DATA_DIR}",
    "--bolt-address=127.0.0.1",
    "--bolt-port=7687",
    "--storage-snapshot-on-exit=true",
    "--storage-snapshot-interval-sec=300",
    "--storage-wal-enabled=true",
    "--log-level=WARNING",
    f"--log-file={DATA_DIR}/memgraph.log",
])
 
# 2) Wait for Bolt to accept connections before serving HTTP.
driver = GraphDatabase.driver("bolt://127.0.0.1:7687")
for _ in range(60):
    try:
        driver.verify_connectivity()
        break
    except Exception:
        time.sleep(1)
else:
    raise RuntimeError("Memgraph did not become ready within 60s")
 
# 3) HTTP shim on CDSW_APP_PORT — the only surface exposed through the subdomain.
app = FastAPI(title="OSIRIS Memgraph shim")
 
 
def _require_auth(req: Request) -> None:
    if API_TOKEN and req.headers.get("authorization", "") != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")
 
 
@app.get("/health")
def health():
    with driver.session() as s:
        return {"ok": s.run("RETURN 1 AS n").single()["n"] == 1}
 
 
@app.post("/cypher")
async def cypher(req: Request):
    _require_auth(req)
    body = await req.json()
    query = body.get("query")
    if not query:
        raise HTTPException(status_code=400, detail="missing 'query'")
    with driver.session() as s:
        rows = s.run(query, **body.get("params", {}))
        return {"rows": [r.data() for r in rows]}
 
 
def _serve():
    """Run uvicorn in a worker thread.
 
    CAI executes PBJ Application scripts inside a Jupyter kernel that already has
    a running asyncio loop, so a top-level uvicorn.run() (which calls
    asyncio.run()) fails with "cannot be called from a running event loop".
    A worker thread gets its own clean loop. Signal handlers are disabled because
    signal.signal() only works in the main thread.
    """
    port = int(os.environ["CDSW_APP_PORT"])
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    asyncio.run(server.serve())
 
 
# Start unconditionally (not under __name__ == "__main__"): under the PBJ kernel
# this module is not run as "__main__". join() keeps the process alive to serve.
_server_thread = threading.Thread(target=_serve, name="uvicorn", daemon=False)
_server_thread.start()
_server_thread.join()