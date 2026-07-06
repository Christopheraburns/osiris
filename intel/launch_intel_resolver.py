"""OSIRIS Intel Resolver (osiris-intel) — CAI Application entrypoint.

Launches the osiris-intel Node service (the live enrichment "brain": GET /resolve
over Wikidata + OpenSanctions/OFAC) on CDSW_APP_PORT. Same shape as the TileServer
launcher: this Python script runs under the CAI PBJ kernel and subprocess-launches
the real (Node) server, forcing it to bind CDSW_APP_PORT instead of the Docker-era
hardcoded :4000. proc.wait() keeps the process alive so CAI sees a running app.

Required Application env vars:
    (none strictly required, but see egress note below)

Optional:
    INTEL_RESOLVER_DIR  path to the intel service source
                        (default: /home/cdsw/services/intel — set to match the repo)
    INTEL_START_CMD     start command as a shell string, if not `npm start`
                        (e.g. "node src/index.js")
    LOG_LEVEL           passed through to the service

Two things to verify before this works (both bit us before):
  1. PORT BINDING — the service MUST listen on process.env.PORT (we set it to
     CDSW_APP_PORT below). If :4000 is hardcoded in the source, change that one
     line to `const PORT = process.env.PORT || 4000;` or CAI won't route to it.
  2. EGRESS — on startup osiris-intel downloads the OFAC SDN CSV and at request
     time queries Wikidata SPARQL. The Application must be allowed to reach
     data.opensanctions.org and query.wikidata.org, or startup hangs/fails
     (same class of issue as curl-works-but-InvokeHTTP-doesn't).

Remember: enable "Unauthenticated Access" on this Application — OSIRIS's
securedProxy() sends no Authorization header.
"""
import os
import shlex
import subprocess
import sys

# ── 1) Locate the intel service source (git-pulled into the project) ──
INTEL_DIR = os.environ.get("INTEL_RESOLVER_DIR", "/home/cdsw/intel")
if not os.path.isdir(INTEL_DIR):
    raise RuntimeError(
        f"osiris-intel source not found at {INTEL_DIR} — "
        "git-pull the repo into the project or set INTEL_RESOLVER_DIR"
    )
os.chdir(INTEL_DIR)

# ── 2) Install Node deps on cold start (skipped if node_modules exists) ──
if not os.path.isdir(os.path.join(INTEL_DIR, "node_modules")):
    print("[launcher] installing osiris-intel dependencies ...", flush=True)
    # npm ci if a lockfile is present (reproducible), else npm install
    installer = "ci" if os.path.exists(os.path.join(INTEL_DIR, "package-lock.json")) else "install"
    subprocess.check_call(["npm", installer])

# ── 3) Bind CDSW_APP_PORT and start the Node server ──
port = os.environ["CDSW_APP_PORT"]
print(f"[launcher] CDSW_APP_PORT={port}  inherited INTEL_PORT={os.environ.get('INTEL_PORT')}", flush=True)
child_env = {
    **os.environ,
    "INTEL_PORT": port,    # server.js reads INTEL_PORT, not PORT
    "HOST": "127.0.0.1",
}

start_cmd = os.environ.get("INTEL_START_CMD", "npm start")
print(f"[launcher] starting osiris-intel on port {port}: {start_cmd}", flush=True)

# Run in the foreground and wait — keeps the CAI Application process alive.
proc = subprocess.Popen(shlex.split(start_cmd), env=child_env)
sys.exit(proc.wait())
