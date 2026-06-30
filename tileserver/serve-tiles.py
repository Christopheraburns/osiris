#!/usr/bin/env python3
"""CAI Application entry point for the OSIRIS tile server (debug-instrumented).
 
Prints what it sees, then starts tileserver if found. The log lines tell us why
the guard fires if main.js isn't where `here` points.
"""
import os, sys
 
def log(*a):
    print("[serve-tiles]", *a, flush=True)
 
log("HOME =", os.environ.get("HOME"))
log("cwd  =", os.getcwd())
 
try:
    log("__file__ =", __file__)
    here = os.path.dirname(os.path.abspath(__file__))
    log("here derived from __file__")
except NameError:
    here = os.path.join(os.environ.get("HOME", "/home/cdsw"), "tileserver")
    log("here from FALLBACK (__file__ undefined)")
 
log("here =", here)
log("here is dir:", os.path.isdir(here))
if os.path.isdir(here):
    log("here contents:", sorted(os.listdir(here)))
    nm = os.path.join(here, "node_modules")
    log("node_modules present under here:", os.path.isdir(nm))
    log("tileserver-gl-light pkg present:",
        os.path.isdir(os.path.join(nm, "tileserver-gl-light")))
 
main_js = os.path.join(here, "node_modules", "tileserver-gl-light", "src", "main.js")
log("main_js =", main_js)
log("main_js exists:", os.path.exists(main_js))
 
if not os.path.exists(main_js):
    log("GUARD WOULD FIRE. Probing likely alternate locations:")
    for cand in [
        os.path.join(os.environ.get("HOME", "/home/cdsw"),
                     "node_modules", "tileserver-gl-light", "src", "main.js"),
        os.path.join(os.getcwd(),
                     "node_modules", "tileserver-gl-light", "src", "main.js"),
    ]:
        log("  probe:", cand, "->", os.path.exists(cand))
    sys.exit(1)
 
os.chdir(here)
port = os.environ.get("CDSW_APP_PORT", "8090")
public_url = os.environ.get("TILESERVER_PUBLIC_URL", "")
args = ["node", main_js, "--port", port, "--bind", "127.0.0.1", "-c", "config.json"]
if public_url:
    args += ["--public_url", public_url]
log("exec:", " ".join(args))
os.execvp("node", args)