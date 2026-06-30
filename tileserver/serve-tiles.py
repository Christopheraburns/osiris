#!/usr/bin/env python3
"""CAI Application entry point for the OSIRIS tile server (tileserver-gl-light).
 
Runs tileserver as a CHILD process and streams its stdout/stderr into this app's
log, so startup errors are visible -- instead of exec-replacing the process (which
hid the error). Keeps the kernel alive as supervisor; the child holds the port.
"""
import os, sys, subprocess
 
def log(*a):
    print("[serve-tiles]", *a, flush=True)
 
try:
    here = os.path.dirname(os.path.abspath(__file__))
except NameError:
    here = os.path.join(os.environ.get("HOME", "/home/cdsw"), "tileserver")
os.chdir(here)
 
port = os.environ.get("CDSW_APP_PORT", "8090")
public_url = os.environ.get("TILESERVER_PUBLIC_URL", "")
 
main_js = os.path.join(here, "node_modules", "tileserver-gl-light", "src", "main.js")
if not os.path.exists(main_js):
    raise SystemExit("tileserver-gl-light not installed at " + main_js)
 
args = ["node", main_js, "--verbose", "--port", port, "--bind", "127.0.0.1", "-c", "config.json"]
if public_url:
    args += ["--public_url", public_url]
 
log("launching:", " ".join(args))
log("cwd:", os.getcwd())
 
# Stream child output line-by-line so tileserver's logs/errors appear here.
proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1)
for line in proc.stdout:
    print(line, end="", flush=True)
rc = proc.wait()
log("tileserver exited with code", rc)
sys.exit(rc)
 