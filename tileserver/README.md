# OSIRIS tile server (CARTO replacement)

Self-hosted OpenMapTiles vector tiles, served by tileserver-gl-light. Same binary
runs locally and on CAI — only the data file and a couple of env vars differ.

## What is committed to git vs not
Committed (small, code/config): `package.json`, `config.json`, `serve-tiles.py`,
`styles/dark/style.json`, `fonts/` (glyph packs), `sprites/dark/`.

NOT committed (large binary, handled out-of-band): `osiris-basemap.mbtiles`.
Add to repo `.gitignore`:
```
*.mbtiles
cloudera/stage-a/tileserver/osiris-basemap.mbtiles
```
The `.mbtiles` lives locally for testing and is uploaded directly into the CAI
`tiles` project's storage. (Fonts can be ~30–50 MB total — fine for git. If you'd
rather not commit them, vendor them the same way as the mbtiles.)

## The dark style (schema must match the tiles)
`styles/dark/style.json` must be an **OpenMapTiles-schema** dark style (the schema
Planetiler's basemap profile emits). The classic CARTO `dark-matter-gl-style` is
OpenMapTiles-based and works; point its vector source at the local tileset:
```json
"sources": { "openmaptiles": { "type": "vector", "url": "mbtiles://{osiris}" } }
```
(`{osiris}` matches the `data.osiris` key in config.json.) Verify it locally — if
whole layers are missing, the style was a non-OMT schema (e.g. CARTO's newer
carto.streets); swap in a stock OMT dark style and retry.

## Local test loop (no new container needed)
From this directory:
```bash
npm install                 # installs tileserver-gl-light into node_modules
# put osiris-basemap.mbtiles here (built in Piece 1)
npx tileserver-gl-light -c config.json -p 8080
```
Open http://localhost:8080/ — you should see the styles/data listed and a preview.
Then run OSIRIS locally with the basemap env var pointed here:
```bash
NEXT_PUBLIC_BASEMAP_STYLE_URL=http://localhost:8080/styles/dark/style.json npm run dev
```
Globe renders from your local tiles = Piece 2+3 work. Now push the committed files
(NOT the mbtiles) to git.

## Deploy on CAI
1. New Project from Git (or a `tiles` folder in the OSIRIS project).
2. Upload `osiris-basemap.mbtiles` into this dir via the CAI UI (it's gitignored).
3. Session on the Node 22 runtime → `cd cloudera/stage-a/tileserver && npm install`.
4. Create an Application: Script `cloudera/stage-a/tileserver/serve-tiles.py`,
   subdomain `tiles`, Node 22 runtime, ~2 vCPU/4 GB, **Enable Unauthenticated
   Access**, and set env var `TILESERVER_PUBLIC_URL=https://tiles.<workbench-domain>/`.
5. Set OSIRIS app env var
   `NEXT_PUBLIC_BASEMAP_STYLE_URL=https://tiles.<workbench-domain>/styles/dark/style.json`,
   rebuild OSIRIS, restart.

## Air-gap note
`npm install` here pulls tileserver-gl-light from the npm registry — an internet
step. For a true air-gap, vendor `node_modules/` (commit it, or bake it into the
runtime image) so nothing is fetched at deploy time. For the connected sandbox,
`npm install` in a Session is fine.
