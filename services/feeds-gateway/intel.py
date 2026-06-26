"""GraphRAG orchestration for the flight-intel scenario.

Phase 1 (attribution) is implemented end-to-end:
  retrieve (captured Neo4j graph + reg-prefix + osiris-intel resolver)
  -> provenance-tagged facts -> grounded Ollama synthesis (streamed).

Phases 2-5 are recognised but not yet built; they return a graceful notice.

Stream protocol (NDJSON, one JSON object per line):
  {"type":"facts","facts":[...],"subgraph":{...},"model":"..."}
  {"type":"token","text":"..."}            (repeated)
  {"type":"done","latency_ms":N}
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, AsyncIterator

import httpx

import db
import graph
import llm
from reg_prefixes import country_from_registration

log = logging.getLogger("feeds-gateway.intel")

INTEL_RESOLVER_URL = os.environ.get("INTEL_RESOLVER_URL", "http://osiris-intel:4000")

TIER_QUESTIONS = {
    1: "Who operates this aircraft, what country is it registered in, and what type is it?",
    2: "What else does this operator fly, is the operator sanctioned, and what is the risk level of its registration country?",
    3: "What is near this aircraft right now - a chokepoint, military vessel, active event, or sensitive site?",
    4: "Has it flown this route before, is this normal for it, when did it last appear, and did its transponder drop out?",
    5: "Is anything unusual here - loitering, proximity to a no-fly area, or a correlated jamming signal nearby?",
}

SYSTEM_PROMPT = (
    "You are OSIRIS, a grounded intelligence analyst. Answer ONLY from the FACTS provided. "
    "Each fact carries a source tag: 'captured:*' means it came from OSIRIS's own recorded "
    "knowledge graph; 'derived:*' is computed locally; 'external:*' is third-party enrichment "
    "(Wikidata/OFAC). Prefer captured facts and say so. If the facts do not answer the question, "
    "say what is missing - never invent operators, countries, or types. Be concise (2-4 sentences), "
    "cite the source tag inline in parentheses, and flag any sanctions or elevated threat explicitly."
)


def _fact(subject: str, predicate: str, obj: Any, source: str, confidence: Any = None) -> dict:
    return {"subject": subject, "predicate": predicate, "object": obj, "source": source, "confidence": confidence}


async def _resolver_subgraph(entity: dict) -> dict:
    """Call osiris-intel /resolve for external attribution enrichment."""
    callsign = (entity.get("callsign") or entity.get("icao24") or "").strip()
    if not callsign:
        return {"nodes": [], "links": []}
    params = {"type": "aircraft", "id": callsign}
    for k in ("registration", "model", "icao24"):
        if entity.get(k):
            params[k] = entity[k]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{INTEL_RESOLVER_URL}/resolve", params=params)
            if r.status_code == 200:
                data = r.json()
                return {"nodes": data.get("nodes", []), "links": data.get("links", [])}
    except Exception as exc:  # noqa: BLE001
        log.warning("resolver enrichment failed: %s", exc)
    return {"nodes": [], "links": []}


def _facts_from_resolver(subject: str, sub: dict) -> list[dict]:
    """Translate the resolver's {nodes,links} into provenance-tagged facts."""
    by_id = {n.get("id"): n for n in sub.get("nodes", [])}
    facts: list[dict] = []
    label_map = {
        "OPERATED BY": "operated_by",
        "REGISTERED IN": "registered_in",
        "AIRCRAFT TYPE": "aircraft_type",
        "SANCTIONS MATCH": "sanctions_match",
    }
    for link in sub.get("links", []):
        pred = label_map.get(link.get("label"))
        if not pred:
            continue
        target = by_id.get(link.get("target"), {})
        name = target.get("label")
        if not name:
            continue
        source = "external:ofac" if pred == "sanctions_match" else "external:wikidata"
        facts.append(_fact(subject, pred, name, source))
    return facts


async def _phase1_facts(entity: dict) -> tuple[list[dict], dict]:
    """Retrieve captured + derived + external facts and a merged subgraph."""
    icao24 = (entity.get("icao24") or "").strip()
    callsign = (entity.get("callsign") or icao24 or "aircraft").strip()
    facts: list[dict] = []

    # 1) Captured knowledge graph (Neo4j).
    captured = graph.aircraft_attribution(icao24) if icao24 else None
    if captured:
        a = captured["aircraft"]
        if a.get("model"):
            facts.append(_fact(callsign, "aircraft_type", a["model"], "captured:graph", a.get("confidence")))
        if a.get("subtype"):
            facts.append(_fact(callsign, "subtype", a["subtype"], "captured:graph"))
        if a.get("registration"):
            facts.append(_fact(callsign, "registration", a["registration"], "captured:graph"))
        if a.get("threatLevel") and a["threatLevel"] != "NONE":
            facts.append(_fact(callsign, "threat_level", a["threatLevel"], "captured:graph"))
        if a.get("lastObserved"):
            facts.append(_fact(callsign, "last_observed", str(a["lastObserved"]), "captured:graph"))
        for op in captured["operators"]:
            facts.append(_fact(callsign, "operated_by", op["name"], "captured:graph", op.get("confidence")))
            if op.get("sanctioned"):
                facts.append(_fact(op["name"], "sanctioned", True, "captured:graph"))
        for rc in captured["registeredIn"]:
            facts.append(_fact(callsign, "registered_in", rc["name"], "captured:graph"))
        for fc in captured["flaggedTo"]:
            facts.append(_fact(callsign, "flagged_to", fc["name"], "captured:graph"))

    # 2) Derived: registration-prefix -> country (fill the gap if graph lacks it).
    has_country = any(f["predicate"] == "registered_in" for f in facts)
    reg = entity.get("registration") or (captured and captured["aircraft"].get("registration"))
    if not has_country:
        country = country_from_registration(reg)
        if country:
            facts.append(_fact(callsign, "registered_in", country, "derived:reg-prefix"))

    # 3) External enrichment via osiris-intel resolver (Wikidata/OFAC).
    resolver_sub = await _resolver_subgraph(entity)
    facts.extend(_facts_from_resolver(callsign, resolver_sub))

    subgraph = {
        "captured": captured or {},
        "resolver": resolver_sub,
    }
    return facts, subgraph


def _facts_block(facts: list[dict]) -> str:
    if not facts:
        return "(no facts found in the knowledge graph or enrichment sources)"
    lines = []
    for f in facts:
        conf = f" conf={f['confidence']}" if f.get("confidence") is not None else ""
        lines.append(f"- {f['subject']} {f['predicate']} {f['object']} [{f['source']}{conf}]")
    return "\n".join(lines)


async def ask_stream(entity: dict, tier: int, question: str | None) -> AsyncIterator[bytes]:
    """Run the GraphRAG pipeline and stream NDJSON events."""
    started = time.monotonic()
    q = (question or TIER_QUESTIONS.get(tier) or TIER_QUESTIONS[1]).strip()
    callsign = (entity.get("callsign") or entity.get("icao24") or "aircraft").strip()

    if tier != 1:
        msg = (f"Tier {tier} intelligence is planned but not yet implemented. "
               f"Phase 1 (attribution) is available now.")
        yield _line({"type": "facts", "facts": [], "subgraph": {}, "model": llm.OLLAMA_MODEL})
        yield _line({"type": "token", "text": msg})
        yield _line({"type": "done", "latency_ms": int((time.monotonic() - started) * 1000)})
        return

    facts, subgraph = await _phase1_facts(entity)
    yield _line({"type": "facts", "facts": facts, "subgraph": subgraph, "model": llm.OLLAMA_MODEL})

    user_prompt = (
        f"QUESTION: {q}\n\n"
        f"SUBJECT: aircraft {callsign} (icao24 {entity.get('icao24') or 'unknown'})\n\n"
        f"FACTS:\n{_facts_block(facts)}\n\n"
        f"Answer the question using only these facts, citing source tags."
    )

    answer_parts: list[str] = []
    async for token in llm.chat_stream(SYSTEM_PROMPT, user_prompt):
        answer_parts.append(token)
        yield _line({"type": "token", "text": token})

    latency = int((time.monotonic() - started) * 1000)
    yield _line({"type": "done", "latency_ms": latency})

    # Audit the Q&A (operational log; dedicated intel_audit table is future work).
    db.log_row(
        "info", "intel", f"tier1 attribution: {callsign}",
        data={
            "tier": 1,
            "icao24": entity.get("icao24"),
            "callsign": callsign,
            "question": q,
            "fact_count": len(facts),
            "answer": "".join(answer_parts)[:2000],
            "latency_ms": latency,
            "model": llm.OLLAMA_MODEL,
        },
    )


def _line(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
