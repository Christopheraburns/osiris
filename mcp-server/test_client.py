#!/usr/bin/env python3
"""Standalone MCP client to test the OSIRIS MCP server remotely.

Independent of Agent Studio — it does the real MCP handshake (initialize →
list_tools → call_tool) so you can prove whether the *server* is healthy and see
the exact tool schemas a client receives. If this lists the tools cleanly but
Agent Studio still can't, the problem is Agent Studio's config/transport, not the
server.

Install once:  pip install "mcp>=1.9.0"

List every tool + its input schema (auto-detect transport):
    python test_client.py --url https://<app-subdomain>            list

Dump the full JSON input schemas (what a client actually parses):
    python test_client.py --url https://<app-subdomain> --raw       list

Call a tool:
    python test_client.py --url https://<app-subdomain> \
        call assets_of_interest --args '{"max_results":5}'
    python test_client.py --url https://<app-subdomain> call health

Options:
    --url        base app URL (with or without /mcp|/sse). Or set MCP_URL.
    --transport  auto (default) | streamable-http | sse
    --token      bearer token, if the app requires auth. Or set MCP_TOKEN.
    --raw        (list) print full inputSchema JSON per tool
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys


def _clean_base(url: str) -> str:
    url = url.rstrip("/")
    for suffix in ("/mcp", "/sse"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


def _flatten(exc, depth: int = 0) -> list[str]:
    """Recursively unwrap ExceptionGroups / __cause__ so the real error shows."""
    indent = "  " * depth
    out = [f"{indent}{type(exc).__name__}: {exc}"]
    for sub in getattr(exc, "exceptions", None) or []:      # ExceptionGroup
        out.extend(_flatten(sub, depth + 1))
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc and not getattr(exc, "exceptions", None):
        out.append(f"{indent}  ↳ caused by:")
        out.extend(_flatten(cause, depth + 2))
    return out


async def _connect(transport: str, base: str, headers: dict | None):
    """Return an (context-manager, url, label) for the chosen transport."""
    if transport == "streamable-http":
        from mcp.client.streamable_http import streamablehttp_client
        url = base + "/mcp"
        return streamablehttp_client(url, headers=headers), url, "streamable-http"
    from mcp.client.sse import sse_client
    url = base + "/sse"
    return sse_client(url, headers=headers), url, "sse"


async def _session_do(cm, url: str, label: str, do) -> None:
    from mcp.client.session import ClientSession

    async with cm as streams:
        read, write = streams[0], streams[1]  # 2- or 3-tuple across SDK versions
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            si = getattr(init, "serverInfo", None)
            print(f"✓ connected via {label}  →  {url}")
            print(f"  server: {getattr(si, 'name', '?')} v{getattr(si, 'version', '?')}"
                  f"   protocol {getattr(init, 'protocolVersion', '?')}\n")
            await do(session)


def _print_tools(res, raw: bool) -> None:
    tools = res.tools
    print(f"{len(tools)} tools advertised:\n")
    for t in tools:
        print(f"● {t.name}")
        if t.description:
            print("   " + t.description.strip().splitlines()[0])
        schema = t.inputSchema or {}
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        if not props:
            print("     (no parameters)")
        for pname, p in props.items():
            star = "*" if pname in required else " "
            typ = p.get("type") or p.get("anyOf") or "?"
            default = f" = {p['default']}" if "default" in p else ""
            print(f"    {star} {pname}: {typ}{default}")
        if raw:
            print("   inputSchema:")
            print("   " + json.dumps(schema, indent=2).replace("\n", "\n   "))
        print()


async def main() -> int:
    ap = argparse.ArgumentParser(description="Test the OSIRIS MCP server remotely.")
    ap.add_argument("--url", default=os.environ.get("MCP_URL"), help="base app URL")
    ap.add_argument("--transport", default="auto",
                    choices=["auto", "streamable-http", "sse"])
    ap.add_argument("--token", default=os.environ.get("MCP_TOKEN"))
    ap.add_argument("--raw", action="store_true", help="dump full JSON input schemas")
    ap.add_argument("command", nargs="?", default="list", choices=["list", "call"])
    ap.add_argument("tool", nargs="?", help="tool name (for `call`)")
    ap.add_argument("--args", default="{}", help="JSON arguments for `call`")
    a = ap.parse_args()

    if not a.url:
        print("error: pass --url https://<app-subdomain> (or set MCP_URL)", file=sys.stderr)
        return 2

    base = _clean_base(a.url)
    headers = {"Authorization": f"Bearer {a.token}"} if a.token else None
    order = [a.transport] if a.transport != "auto" else ["streamable-http", "sse"]

    async def do(session):
        if a.command == "list":
            _print_tools(await session.list_tools(), a.raw)
        else:
            if not a.tool:
                print("error: `call` needs a tool name", file=sys.stderr)
                return
            arguments = json.loads(a.args)
            print(f"calling {a.tool}({arguments}) ...\n")
            res = await session.call_tool(a.tool, arguments)
            for block in res.content:
                text = getattr(block, "text", None)
                print(text if text is not None else block)
            sc = getattr(res, "structuredContent", None)
            if sc:
                print("\nstructuredContent:")
                print(json.dumps(sc, indent=2)[:6000])

    last_err = None
    for t in order:
        try:
            cm, url, label = await _connect(t, base, headers)
            await _session_do(cm, url, label, do)
            return 0
        except ImportError as exc:
            print("error: could not import the MCP SDK for THIS interpreter.\n"
                  f"  detail : {exc}\n"
                  f"  python : {sys.executable}\n"
                  f"  version: {sys.version.split()[0]}\n"
                  "Fix: install into the same interpreter that runs this script:\n"
                  '  python -m pip install "mcp>=1.9.0"\n'
                  "(plain `pip` can target a different Python than `python`.)",
                  file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"✗ {t} failed:", file=sys.stderr)
            for line in _flatten(exc):
                print("   " + line, file=sys.stderr)

    print(f"\nAll transports failed. Last error: {last_err}", file=sys.stderr)
    print("Hints: confirm the app is Running and Unauthenticated Access is on; try "
          "`curl https://<app-subdomain>/` (should return the health line); if only "
          "SSE connects, set MCP_TRANSPORT=sse on the server for Agent Studio.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
