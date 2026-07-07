"""OSIRIS MCP server — CAI Application entrypoint.

Runs the FastMCP streamable-HTTP app (server.py) on CDSW_APP_PORT. Same pattern as
the feeds-gateway launcher: uvicorn runs in a worker thread (the PBJ Jupyter kernel
already owns an asyncio loop, so a top-level asyncio.run() fails), signal handlers
are disabled (not the main thread), and join() keeps the process alive so CAI sees
a running Application.

Deploy:
  * Enable **Unauthenticated Access** (Agent Studio / MCP clients send no auth header).
  * Put this Application in Osiris Prime so in-project HTTP to the gateway is cheap.
  * Agent Studio connects to the remote MCP endpoint at  https://<app-url>/mcp

Required Application env vars:
    FEEDS_GATEWAY_URL   e.g. https://feeds-gateway.<suffix>   (no trailing slash)
    MEMGRAPH_URL        the Memgraph shim URL (POST /cypher)
    INTEL_URL           the osiris-intel resolver URL (GET /resolve)
Optional:
    MEMGRAPH_API_TOKEN  bearer token for the shim, if set
    SECURE_MODE_DEFAULT default "true" (resolve against the air-gapped graph)
"""
import asyncio
import importlib.util
import os
import subprocess
import sys
import threading

try:
    HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:  # PBJ kernel: __file__ is undefined
    HERE = os.environ.get("MCP_DIR", "/home/cdsw/mcp-server")
os.chdir(HERE)
sys.path.insert(0, HERE)

# Install dependencies on cold start (skipped if already importable).
_REQUIRED = ("mcp", "httpx", "uvicorn", "starlette")
if any(importlib.util.find_spec(m) is None for m in _REQUIRED):
    print("[launcher] installing mcp-server requirements ...", flush=True)
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", "-r", os.path.join(HERE, "requirements.txt")]
    )

import uvicorn  # noqa: E402  (after the conditional install)

from server import app  # noqa: E402  FastMCP streamable-HTTP ASGI app (+ GET / health)


def _serve() -> None:
    port = int(os.environ["CDSW_APP_PORT"])
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # not the main thread
    asyncio.run(server.serve())


_server_thread = threading.Thread(target=_serve, name="mcp-uvicorn", daemon=False)
_server_thread.start()
_server_thread.join()
