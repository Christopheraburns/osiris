"""OSIRIS Ollama — CAI Application entrypoint (GPU / A10G).

Runs `ollama serve` bound to CDSW_APP_PORT so the feeds-gateway (and any other
caller) reaches the local LLM over the Application subdomain. Models live in the
project mount so they survive restarts, and the configured model is pulled and
warmed on boot so the operator's first query isn't a cold load.

Ollama's HTTP API doubles as the health surface: GET / returns 200 "Ollama is
running", so CAI's health check passes with no extra shim.

Deploy notes:
  * Give this Application a **GPU** resource profile (the A10G).
  * Enable **Unauthenticated Access** — the gateway calls it with no auth header.
  * Point the gateway's OLLAMA_URL at this App's URL, OLLAMA_MODEL at the tag below.

Env:
    OLLAMA_MODEL   model tag to pull/serve (default: mistral-small3.2)
    OLLAMA_MODELS  model store (default: /home/cdsw/.ollama, on the project mount)
"""
import os
import subprocess
import sys
import time
import urllib.request

MODEL = os.environ.get("OLLAMA_MODEL", "mistral-small3.2")
PORT = os.environ["CDSW_APP_PORT"]

# Bind Ollama to the CAI app port on localhost (0.0.0.0 = silent 30-min hang).
os.environ["OLLAMA_HOST"] = f"127.0.0.1:{PORT}"
os.environ.setdefault("OLLAMA_MODELS", "/home/cdsw/.ollama")
os.makedirs(os.environ["OLLAMA_MODELS"], exist_ok=True)

BASE = f"http://127.0.0.1:{PORT}"


def _server_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/api/tags", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _which_ollama() -> bool:
    return subprocess.run(["bash", "-lc", "command -v ollama"], capture_output=True).returncode == 0


# Install the ollama binary on cold start if the runtime doesn't ship it (needs egress).
if not _which_ollama():
    print("[ollama] binary not found — installing ...", flush=True)
    try:
        subprocess.check_call(["bash", "-lc", "curl -fsSL https://ollama.com/install.sh | sh"])
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"[ollama] install failed ({exc}); bake ollama into the runtime instead")

print(f"[ollama] starting server on {os.environ['OLLAMA_HOST']} (models: {os.environ['OLLAMA_MODELS']})", flush=True)
proc = subprocess.Popen(["ollama", "serve"])

# Wait for the API to accept connections.
for _ in range(90):
    if _server_up():
        break
    if proc.poll() is not None:
        raise SystemExit(f"[ollama] server exited early with code {proc.returncode}")
    time.sleep(1)
else:
    print("[ollama] WARNING: server not ready after 90s; continuing", flush=True)

# Pull + warm the model so the first real request is fast.
try:
    print(f"[ollama] pulling model {MODEL} (skipped if already present) ...", flush=True)
    subprocess.check_call(["ollama", "pull", MODEL])
    print(f"[ollama] warming {MODEL} ...", flush=True)
    subprocess.run(["ollama", "run", MODEL, "ready"], timeout=240)
    print("[ollama] model warm; serving.", flush=True)
except Exception as exc:  # noqa: BLE001
    print(f"[ollama] pull/warm failed ({exc}); server still serving other models", flush=True)

# Keep the Application process alive on the running server.
sys.exit(proc.wait())
