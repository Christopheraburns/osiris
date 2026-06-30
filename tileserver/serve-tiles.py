#!/usr/bin/env python3
"""
CAI Application entry point for the OSIRIS tile server (tileserver-gl-light).

Runs the SAME tileserver-gl-light binary you test with locally, so local parity
holds. Selected as the Application "Script".

Two env-driven knobs:
  CDSW_APP_PORT            - injected by CAI; the port to listen on.
  TILESERVER_PUBLIC_URL    - set this on the CAI Application to the tiles app's
                             external URL, e.g. https://tiles.<workbench-domain>/
                             tileserver-gl bakes absolute sprite/glyph/tile URLs
                             into the served style.json, so behind CAI's subdomain
                             proxy it MUST know its public URL or the browser will
                             request the wrong host. Locally leave it unset (or
                             http://localhost:8080/).
"""
import os

here = os.path.dirname(os.path.abspath(__file__))
os.chdir(here)

port = os.environ.get("CDSW_APP_PORT", "8090")
public_url = os.environ.get("TILESERVER_PUBLIC_URL", "")

args = ["tileserver-gl-light", "--port", port, "--bind", "127.0.0.1", "-c", "config.json"]
if public_url:
    args += ["--public_url", public_url]

bin_path = os.path.join(here, "node_modules", ".bin", "tileserver-gl-light")
if not os.path.exists(bin_path):
    raise SystemExit("tileserver-gl-light not installed. Run `npm install` in this dir (Session) first.")

os.execvp(bin_path, args)
