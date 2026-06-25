"""HTTP control plane for the recorder — start/stop polling without restarting the container.

Runs in --mode idle (default in compose). The UI toggles recording via
POST /control; GET / returns { recording, run_id, ... }.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from recorder import Recorder, _log, _now


class RecorderController:
    """Thread-safe start/stop wrapper around the poll loop."""

    def __init__(self, base_url: str, use_graph: bool = True) -> None:
        self.base_url = base_url
        self.use_graph = use_graph
        self._lock = threading.Lock()
        self._recording = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_id: str | None = None
        self._started_at: str | None = None
        self._rec: Recorder | None = None

    def status(self) -> dict:
        with self._lock:
            return {
                "recording": self._recording,
                "available": True,
                "run_id": self._run_id,
                "started_at": self._started_at,
                "timestamp": _now().isoformat(),
            }

    def start(self) -> dict:
        with self._lock:
            if self._recording:
                return {"ok": True, "already": True, **self.status()}
            self._stop.clear()
            self._run_id = os.environ.get("INGEST_RUN_ID") or f"rec-{uuid.uuid4().hex[:12]}"
            self._started_at = _now().isoformat()
            self._recording = True
            self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="recorder-poll")
            self._thread.start()
            _log(f"control: recording started run_id={self._run_id}")
            return {"ok": True, "recording": True, "run_id": self._run_id}

    def stop(self) -> dict:
        with self._lock:
            if not self._recording:
                return {"ok": True, "already": True, **self.status()}
            self._recording = False
            self._stop.set()
            thread = self._thread
        if thread:
            thread.join(timeout=30)
        with self._lock:
            self._thread = None
            self._rec = None
        _log("control: recording stopped")
        return {"ok": True, "recording": False}

    def _poll_loop(self) -> None:
        try:
            self._rec = Recorder(self.base_url, self._run_id or "rec-unknown", use_graph=self.use_graph)
            next_due = {ep["name"]: 0.0 for ep in self._rec.endpoints}
            _log(f"control: poll loop active run_id={self._run_id} endpoints={len(self._rec.endpoints)}")
            while not self._stop.is_set():
                now = time.time()
                for ep in self._rec.endpoints:
                    if self._stop.is_set():
                        break
                    if now >= next_due[ep["name"]]:
                        self._rec.poll_endpoint(ep)
                        next_due[ep["name"]] = now + ep.get("pollMs", 300_000) / 1000.0
                if self._stop.is_set():
                    break
                sleep_for = max(0.5, min(next_due.values()) - time.time())
                self._stop.wait(timeout=min(sleep_for, 5.0))
        except Exception as exc:  # noqa: BLE001
            _log(f"control: poll loop error: {exc}")
            with self._lock:
                self._recording = False


def run_control_server(
    host: str = "0.0.0.0",
    port: int = 8090,
    base_url: str | None = None,
    use_graph: bool = True,
) -> None:
    controller = RecorderController(
        base_url=base_url or os.environ.get("OSIRIS_BASE_URL", "http://osiris:3000"),
        use_graph=use_graph,
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: ARG002
            return  # quiet; recorder uses _log

        def _json(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/health", "/status"):
                self._json(200, controller.status())
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/control":
                self._json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            action = body.get("action")
            if action == "start":
                self._json(200, controller.start())
            elif action == "stop":
                self._json(200, controller.stop())
            else:
                self._json(400, {"error": "action must be start or stop"})

    server = ThreadingHTTPServer((host, port), Handler)
    _log(f"control: listening on {host}:{port} (recording=false by default)")
    server.serve_forever()
