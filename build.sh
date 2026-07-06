#!/bin/bash
# Run this ONCE inside a CAI Session that uses the "OSIRIS Node 22" runtime,
# from the project root:  bash cloudera/stage-a/build.sh
#
# Next.js standalone output does NOT auto-copy static assets, so we stage them
# next to the standalone server (same thing the original Dockerfile did).
set -euo pipefail

echo "[build] node: $(node --version)  npm: $(npm --version)"

# 1. Install exact deps and build the standalone server.
npm ci
npm run build   # produces .next/standalone/server.js

# 2. Stage static + public assets alongside the standalone server.
cp -r public        .next/standalone/public
mkdir -p            .next/standalone/.next
cp -r .next/static  .next/standalone/.next/static

echo "[build] done. Standalone server at .next/standalone/server.js"
