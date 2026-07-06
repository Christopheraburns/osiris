#!/usr/bin/env python3
"""CAI Application entry point for OSIRIS (Next.js).

Runs `next start`, which serves .next/static itself -- avoiding the standalone
static-staging problem that 404s /_next/static chunks (ChunkLoadError). Requires a
`.next` build (run `npm run build` in a Session first, with NEXT_PUBLIC_* set so
the basemap URL bakes in).

CAI runs this inside a Jupyter kernel where __file__ is undefined -> try/except.
Output is streamed so startup errors show in the app log.
"""
import os, sys, subprocess

def log(*a):
    print("[entry]", *a, flush=True)

try:
    here = os.path.dirname(os.path.abspath(__file__))
except NameError:
    here = os.environ.get("HOME", "/home/cdsw")   # entry.py lives at project root
os.chdir(here)

port = os.environ.get("CDSW_APP_PORT", "8090")

next_bin = os.path.join(here, "node_modules", ".bin", "next")
if not os.path.exists(next_bin):
    raise SystemExit("`next` not found at " + next_bin
                     + "\nRun `npm install` in " + here + " first.")
if not os.path.isdir(os.path.join(here, ".next")):
    raise SystemExit("No .next build in " + here
                     + "\nRun `npm run build` (with NEXT_PUBLIC_* set) first.")

args = [next_bin, "start", "--port", port, "--hostname", "127.0.0.1"]
log("launching:", " ".join(args))

proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1)
for line in proc.stdout:
    print(line, end="", flush=True)
rc = proc.wait()
log("next start exited with code", rc)
sys.exit(rc)