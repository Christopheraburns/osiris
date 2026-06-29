#!/usr/bin/env python3
"""A1: turn a class-filtered Wikidata NDJSON into Neo4j bulk-import CSVs.

Input  : NDJSON, one Wikidata entity JSON per line (output of `filter.sh`,
         which runs wikibase-dump-filter over the full dump). May be .gz.
Output : import/nodes.csv and import/rels.csv, ready for
         `neo4j-admin database import full` (see import.sh).

Scope (the five A1 classes), label priority high->low:
    Q6256 Country | Q46970 Airline | Q11446 Ship |
    Q4830453/Q43229 Organization | Q5 Person

Human bound (env WIKIDATA_HUMANS):
    "linked" (default) -> keep only people referenced as CEO/head-of-state by an
                          in-scope org/country, or whose employer (P108) is an
                          in-scope organization. Drops the ~11M-person long tail.
    "all"              -> keep every Q5 (large; needs the hardware).

Two passes over the (already small) filtered file: pass 1 writes non-person
nodes + edges and records org ids + referenced person ids; pass 2 writes the
bounded set of people. Dangling edges are dropped at import time via
--skip-bad-relationships.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import re
import sys

# (P31 target, Neo4j label) in priority order — first match wins.
CLASS_LABEL = [
    ("Q6256", "Country"),
    ("Q46970", "Airline"),
    ("Q11446", "Ship"),
    ("Q4830453", "Organization"),
    ("Q43229", "Organization"),
    ("Q5", "Person"),
]

# relation property -> Neo4j relationship type (target is another entity QID)
REL_PROPS = {
    "P17": "LOCATED_IN",
    "P27": "CITIZEN_OF",
    "P127": "OWNED_BY",
    "P137": "OPERATED_BY",
    "P749": "PARENT_ORG",
    "P169": "HAS_CEO",
    "P108": "EMPLOYED_BY",
    "P35": "HEAD_OF_STATE",
    "P47": "BORDERS",
    "P463": "MEMBER_OF",
    "P8047": "REGISTERED_IN",
}
# person-valued properties used to seed the "linked" human bound
PERSON_REF_PROPS = ("P169", "P35")

# string / external-id properties -> node column
STR_PROPS = {"P474": "callingCode", "P297": "isoA2", "P298": "isoA3",
             "P458": "imo", "P230": "icao"}
# quantity properties -> (node column, neo4j type)
QUANT_PROPS = {"P1082": ("population", "long"), "P2131": ("gdp", "double")}

NODE_HEADER = ["qid:ID", "name", "nameKey", ":LABEL",
               "population:long", "gdp:double", "callingCode",
               "isoA2", "isoA3", "imo", "icao"]
REL_HEADER = [":START_ID", ":END_ID", ":TYPE"]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).strip().lower()).strip("-")


def _open(path: str):
    if path == "-":
        return sys.stdin
    if path.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8")
    return open(path, encoding="utf-8")


def _claims(item: dict, pid: str) -> list:
    return item.get("claims", {}).get(pid, []) or []


def _qid_val(snak: dict):
    try:
        dv = snak["mainsnak"]["datavalue"]
        if dv["type"] == "wikibase-entityid":
            return dv["value"]["id"]
    except (KeyError, TypeError):
        return None
    return None


def _str_val(snak: dict):
    try:
        dv = snak["mainsnak"]["datavalue"]
        if dv["type"] == "string":
            return dv["value"]
        if dv["type"] == "quantity":
            return dv["value"]["amount"].lstrip("+")
    except (KeyError, TypeError):
        return None
    return None


def _p31_set(item: dict) -> set:
    out = set()
    for snak in _claims(item, "P31"):
        q = _qid_val(snak)
        if q:
            out.add(q)
    return out


def label_of(item: dict):
    p31 = _p31_set(item)
    for qid, lab in CLASS_LABEL:
        if qid in p31:
            return lab
    return None


def en_label(item: dict) -> str:
    return ((item.get("labels", {}) or {}).get("en", {}) or {}).get("value", "")


def _parse_line(line: str):
    line = line.strip().rstrip(",")
    if not line or line in ("[", "]"):
        return None
    try:
        return json.loads(line)
    except ValueError:
        return None


def write_node(nw, item: dict, lab: str) -> None:
    name = en_label(item)
    cols = {c: "" for c in ("population", "gdp", "callingCode", "isoA2", "isoA3", "imo", "icao")}
    for pid, (col, _t) in QUANT_PROPS.items():
        for snak in _claims(item, pid):
            v = _str_val(snak)
            if v:
                cols[col] = v
                break
    for pid, col in STR_PROPS.items():
        for snak in _claims(item, pid):
            v = _str_val(snak)
            if v:
                cols[col] = v
                break
    nw.writerow([item["id"], name, _slug(name), f"{lab};Wikidata",
                 cols["population"], cols["gdp"], cols["callingCode"],
                 cols["isoA2"], cols["isoA3"], cols["imo"], cols["icao"]])


def write_rels(rw, item: dict) -> int:
    n = 0
    qid = item["id"]
    for pid, rtype in REL_PROPS.items():
        for snak in _claims(item, pid):
            t = _qid_val(snak)
            if t:
                rw.writerow([qid, t, rtype])
                n += 1
    return n


def main() -> None:
    inp = sys.argv[1] if len(sys.argv) > 1 else "-"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "import"
    humans = os.environ.get("WIKIDATA_HUMANS", "linked").lower()
    os.makedirs(outdir, exist_ok=True)

    if inp == "-" and humans != "all":
        sys.exit("ERROR: 'linked' mode needs two passes and cannot read stdin. "
                 "Pass a file path, or set WIKIDATA_HUMANS=all.")

    org_ids: set = set()
    ref_persons: set = set()
    n_nodes = n_rels = n_people = 0

    nf = open(os.path.join(outdir, "nodes.csv"), "w", newline="", encoding="utf-8")
    rf = open(os.path.join(outdir, "rels.csv"), "w", newline="", encoding="utf-8")
    nw, rw = csv.writer(nf), csv.writer(rf)
    nw.writerow(NODE_HEADER)
    rw.writerow(REL_HEADER)

    # PASS 1 — non-person nodes + edges; collect org ids + referenced persons
    with _open(inp) as fh:
        for line in fh:
            item = _parse_line(line)
            if item is None:
                continue
            lab = label_of(item)
            if lab is None or lab == "Person":
                continue
            if lab in ("Organization", "Airline"):
                org_ids.add(item["id"])
            write_node(nw, item, lab)
            n_nodes += 1
            n_rels += write_rels(rw, item)
            for p in PERSON_REF_PROPS:
                for snak in _claims(item, p):
                    t = _qid_val(snak)
                    if t:
                        ref_persons.add(t)

    # PASS 2 — bounded people
    if humans == "all":
        keep_all = True
    else:
        keep_all = False
    with _open(inp) as fh:
        for line in fh:
            item = _parse_line(line)
            if item is None or label_of(item) != "Person":
                continue
            qid = item["id"]
            keep = keep_all or qid in ref_persons
            if not keep:
                for snak in _claims(item, "P108"):
                    if _qid_val(snak) in org_ids:
                        keep = True
                        break
            if not keep:
                continue
            write_node(nw, item, "Person")
            n_nodes += 1
            n_people += 1
            n_rels += write_rels(rw, item)

    nf.close()
    rf.close()
    print(f"A1 transform complete (humans={humans})")
    print(f"  nodes : {n_nodes}  (people kept: {n_people})")
    print(f"  edges : {n_rels}  (dangling dropped at import)")
    print(f"  orgs  : {len(org_ids)}   referenced people: {len(ref_persons)}")
    print(f"  out   : {outdir}/nodes.csv, {outdir}/rels.csv")


if __name__ == "__main__":
    main()
