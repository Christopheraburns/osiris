#!/usr/bin/env python3
"""OSIRIS · Graph Analytics — companion CAI Application.

A small viewer that renders the distributed graph-analytics results (Spark
GraphFrames: PageRank + community detection) alongside OSIRIS. It reads the
metrics that enrich_memgraph.py wrote back onto the graph, through the same
HTTP shim (POST /cypher), so it needs no lake/Impala credentials:

    * "Most central entities"  — top nodes by PageRank
    * "Communities"            — largest Label-Propagation clusters
    * a force-graph of any selected community (node size ∝ PageRank)

Deploy as its own CAI Application (one HTTP port, CDSW_APP_PORT), next to OSIRIS.

Env:
    MEMGRAPH_URL        base of the graph shim (POST /cypher)
    MEMGRAPH_API_TOKEN  optional bearer token
    CDSW_APP_PORT       port to bind (CAI sets this)
"""
from __future__ import annotations

import asyncio
import os
import threading

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

MEMGRAPH_URL = os.environ.get("MEMGRAPH_URL", "http://localhost:8090").rstrip("/")
MEMGRAPH_API_TOKEN = os.environ.get("MEMGRAPH_API_TOKEN")
PORT = int(os.environ.get("CDSW_APP_PORT", "8093"))

app = FastAPI(title="OSIRIS Graph Analytics")


def _headers() -> dict:
    return {"Authorization": f"Bearer {MEMGRAPH_API_TOKEN}"} if MEMGRAPH_API_TOKEN else {}


async def _cypher(query: str, params: dict | None = None) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{MEMGRAPH_URL}/cypher",
                         json={"query": query, "params": params or {}}, headers=_headers())
        r.raise_for_status()
        data = r.json()
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return v
        return []
    return data if isinstance(data, list) else []


@app.get("/api/central")
async def central(limit: int = 25, label: str | None = None):
    where = "n.pagerank IS NOT NULL"
    params: dict = {"limit": int(limit)}
    if label:
        where += " AND labels(n)[0] = $label"
        params["label"] = label
    rows = await _cypher(
        f"MATCH (n) WHERE {where} "
        "RETURN id(n) AS id, labels(n)[0] AS label, coalesce(n.name, n.callsign, n.imo, n.icao, '') AS name, "
        "n.pagerank AS pagerank, n.degree AS degree "
        "ORDER BY n.pagerank DESC LIMIT $limit", params)
    return JSONResponse({"entities": rows})


@app.get("/api/labels")
async def labels():
    """Node types present in the enriched graph, for the scope dropdown."""
    rows = await _cypher(
        "MATCH (n) WHERE n.pagerank IS NOT NULL "
        "RETURN labels(n)[0] AS label, count(n) AS n ORDER BY n DESC")
    return JSONResponse({"labels": rows})


@app.get("/api/neighborhood/{node_id}")
async def neighborhood(node_id: int, limit: int = 120):
    """1-hop ego network of one node — the interactive relationship view. Neighbors
    are capped to the top `limit` by PageRank so hub nodes stay renderable."""
    center = await _cypher(
        "MATCH (n) WHERE id(n) = $id "
        "RETURN id(n) AS id, coalesce(n.name, n.callsign, n.imo, '') AS name, "
        "labels(n)[0] AS label, coalesce(n.pagerank, 0.0) AS pagerank", {"id": int(node_id)})
    nbrs = await _cypher(
        "MATCH (n)-[r]-(m) WHERE id(n) = $id "
        "RETURN id(m) AS id, coalesce(m.name, m.callsign, m.imo, '') AS name, labels(m)[0] AS label, "
        "coalesce(m.pagerank, 0.0) AS pagerank, type(r) AS rel "
        "ORDER BY m.pagerank DESC LIMIT $limit", {"id": int(node_id), "limit": int(limit)})
    c = center[0] if center else {"id": int(node_id), "name": "", "label": "", "pagerank": 0.0}
    nodes = [c]
    seen = {c["id"]}
    links = []
    for nb in nbrs:
        if nb["id"] not in seen:
            nodes.append({"id": nb["id"], "name": nb["name"], "label": nb["label"], "pagerank": nb["pagerank"]})
            seen.add(nb["id"])
        links.append({"source": c["id"], "target": nb["id"], "rel": nb.get("rel")})
    return JSONResponse({"nodes": nodes, "links": links, "center": c["id"]})


@app.get("/api/clusters")
async def clusters(limit: int = 30):
    rows = await _cypher(
        "MATCH (n) WHERE n.community IS NOT NULL "
        "RETURN n.community AS community, count(n) AS size "
        "ORDER BY size DESC LIMIT $limit", {"limit": int(limit)})
    return JSONResponse({"clusters": rows})


@app.get("/api/community/{cid}")
async def community(cid: str, limit: int = 250, elimit: int = 1200):
    nodes = await _cypher(
        "MATCH (n) WHERE toString(n.community) = $c "
        "RETURN id(n) AS id, coalesce(n.name, n.callsign, n.imo, '') AS name, "
        "labels(n)[0] AS label, coalesce(n.pagerank, 0.0) AS pagerank "
        "ORDER BY n.pagerank DESC LIMIT $limit", {"c": str(cid), "limit": int(limit)})
    links = await _cypher(
        "MATCH (a)-[r]->(b) WHERE toString(a.community) = $c AND toString(b.community) = $c "
        "RETURN id(a) AS source, id(b) AS target, type(r) AS rel LIMIT $elimit",
        {"c": str(cid), "elimit": int(elimit)})
    ids = {n["id"] for n in nodes}
    links = [l for l in links if l["source"] in ids and l["target"] in ids]
    return JSONResponse({"nodes": nodes, "links": links})


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(_PAGE)


# ── Single-page viewer. force-graph from CDN — for air-gap, vendor this file and
#    serve it locally (see the runbook). ───────────────────────────────────────
_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>OSIRIS · Graph Analytics</title>
<script src="https://cdn.jsdelivr.net/npm/force-graph"></script>
<style>
  :root{--bg:#0b1020;--panel:#121a2e;--edge:#26324d;--ink:#e6ecf5;--muted:#8aa;--accent:#448AFF}
  *{box-sizing:border-box} body{margin:0;font:13px/1.4 system-ui,Segoe UI,Roboto,sans-serif;
    background:var(--bg);color:var(--ink);height:100vh;display:flex;flex-direction:column}
  header{padding:10px 16px;border-bottom:1px solid var(--edge);display:flex;align-items:baseline;gap:12px}
  header b{font-size:15px;letter-spacing:.4px} header span{color:var(--muted)}
  main{flex:1;display:flex;min-height:0}
  aside{flex:0 0 340px;border-right:1px solid var(--edge);overflow:auto;padding:10px}
  h2{font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);margin:14px 6px 6px}
  table{width:100%;border-collapse:collapse} td{padding:4px 6px;border-bottom:1px solid var(--edge)}
  td.n{color:var(--muted);text-align:right;font-variant-numeric:tabular-nums;width:64px}
  .lab{display:inline-block;font-size:10px;color:#9db4ff;background:#1b2a4a;padding:0 5px;border-radius:8px;margin-right:6px}
  .row{cursor:pointer} .row:hover{background:#1a2440}
  .clu{display:flex;justify-content:space-between;padding:5px 6px;border-bottom:1px solid var(--edge);cursor:pointer}
  .clu:hover{background:#1a2440} .clu .sz{color:var(--accent);font-variant-numeric:tabular-nums}
  .clu small{color:var(--muted);display:block}
  #graph{flex:1;min-width:0;overflow:hidden;position:relative}
  #hint{position:absolute;top:12px;left:12px;color:var(--ink);z-index:2;background:rgba(11,16,32,0.75);padding:2px 7px;border-radius:4px}
</style></head>
<body>
<header><b>OSIRIS · GRAPH ANALYTICS</b>
  <span>PageRank &amp; communities · Spark GraphFrames → live graph</span></header>
<main>
  <aside>
    <h2>Most central entities
      <select id="labelsel" style="float:right;background:#0b1020;color:var(--ink);border:1px solid var(--edge);border-radius:4px;font-size:10px;padding:1px 3px">
        <option value="">all types</option></select></h2>
    <table id="central"><tbody><tr><td class="muted">loading…</td></tr></tbody></table>
    <p style="color:var(--muted);font-size:10px;margin:12px 6px">Click any entity to explore its relationships in the graph →</p>
  </aside>
  <div id="graph"><div id="hint">Select an entity to explore its relationships</div></div>
</main>
<script>
const PAL={Organization:'#448AFF',Country:'#66BB6A',Vessel:'#26C6DA',Aircraft:'#FFCA28',
  Airline:'#AB47BC',Person:'#EF5350'};
const col=l=>PAL[l]||'#90A4AE';
let G=null;

async function j(u){const r=await fetch(u);if(!r.ok){throw new Error('HTTP '+r.status+' — '+(await r.text()).slice(0,200));}return r.json();}
const err=(m)=>`<span style="color:#EF5350">${m}</span>`;

async function loadLabels(){
  try{
    const d=await j('/api/labels');
    const sel=document.getElementById('labelsel');
    (d.labels||[]).forEach(l=>{const o=document.createElement('option');
      o.value=l.label||'';o.textContent=(l.label||'?')+' ('+l.n+')';sel.appendChild(o);});
    sel.onchange=()=>loadCentral(sel.value);
    // Open on the first non-hub type so the default view is intelligence-relevant
    // (Country/Place dominate raw PageRank and aren't actionable).
    const pref=(d.labels||[]).map(l=>l.label).find(x=>x && !['Country','Place'].includes(x));
    if(pref){sel.value=pref;loadCentral(pref);} else {loadCentral('');}
  }catch(e){loadCentral('');}
}
async function loadCentral(label){
  const q=label?('&label='+encodeURIComponent(label)):'';
  try{
    const d=await j('/api/central?limit=25'+q);
    document.querySelector('#central tbody').innerHTML=(d.entities||[]).map(e=>
      `<tr class="row" onclick="showNeighborhood(${e.id})">
         <td><span class="lab">${e.label||''}</span>${e.name||'—'}</td>
         <td class="n">${(+e.pagerank||0).toFixed(3)}</td></tr>`).join('')
      || '<tr><td class="muted">no PageRank on the graph — run enrich_memgraph.py</td></tr>';
  }catch(e){document.querySelector('#central tbody').innerHTML='<tr><td>'+err(e.message)+'</td></tr>';}
}
async function showNeighborhood(id){
  document.getElementById('hint').textContent='loading relationships …';
  let d;
  try{ d=await j('/api/neighborhood/'+encodeURIComponent(id)); }
  catch(e){ document.getElementById('hint').textContent='error: '+e.message; return; }
  const c=(d.nodes||[]).find(n=>n.id===d.center)||{};
  const rels=Math.max(0,(d.nodes||[]).length-1);
  document.getElementById('hint').textContent=
    (c.name||id)+' — '+rels+(rels?' relationships (click a node to expand)':' — no relationships in the graph');
  const g=document.getElementById('graph');
  if(!G){G=ForceGraph()(g)
      .backgroundColor('#0b1020').linkColor(()=>'#26324d').linkWidth(0.5)
      .linkLabel(l=>l.rel||'')
      .nodeLabel(n=>`${n.label}: ${n.name} (pr ${(+n.pagerank).toFixed(3)})`)
      .onNodeClick(n=>showNeighborhood(n.id))
      .nodeCanvasObject((n,ctx,s)=>{const r=2+Math.sqrt((+n.pagerank||0)*400);
        ctx.beginPath();ctx.arc(n.x,n.y,r,0,2*Math.PI);ctx.fillStyle=col(n.label);ctx.fill();
        if(r>4||s>1.5){ctx.fillStyle='#cdd6e6';ctx.font=`${10/s}px sans-serif`;
          ctx.fillText(n.name||'',n.x+r+1,n.y+3);}});}
  G.width(g.clientWidth).height(g.clientHeight);   // keep canvas inside its flex cell
  G.graphData({nodes:d.nodes.map(n=>({...n})),
    links:d.links.map(l=>({source:l.source,target:l.target,rel:l.rel}))});
}
loadLabels();
</script></body></html>"""


# The CAI PBJ kernel already owns an asyncio loop, so a top-level uvicorn.run()
# (which calls asyncio.run) fails with "cannot be called from a running event loop".
# Run uvicorn in a worker thread with its own loop — same pattern as the gateway/MCP
# launchers. join() keeps the process alive so CAI sees a running Application.
def _serve() -> None:
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # not the main thread
    asyncio.run(server.serve())


_server_thread = threading.Thread(target=_serve, name="graph-viewer-uvicorn", daemon=False)
_server_thread.start()
_server_thread.join()
