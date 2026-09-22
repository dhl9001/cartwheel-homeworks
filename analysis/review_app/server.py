"""Serve the Homework 4 session review interface.

Reads the Module 1 trace export and the notes under ``review_app/state/``.
It does not write ``analysis/state/``, which holds the course demo fixtures.

    python analysis/review_app/server.py
    python analysis/review_app/server.py --port 8021
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from analysis.review_app.batch import (
    first_reading_batch,
    refund_search_batch,
    role_reading_batch,
    traces_from_sessions,
    uniform_stability_batch,
)
from analysis.review_app.sessions import build_sessions
STATE_DIR = HERE / "state"
DEFAULT_EXPORT = REPO / "traces" / "support_traces.json"

API_FILES: dict[str, Path] = {
    "/api/annotations": STATE_DIR / "annotations.json",
    "/api/suggestions": STATE_DIR / "suggestions.json",
    "/api/patterns": STATE_DIR / "patterns.json",
}
API_DEFAULTS: dict[str, Any] = {
    "/api/annotations": [],
    "/api/suggestions": [],
    "/api/patterns": {"modes": []},
}


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def load_sessions(export_path: Path) -> list[dict[str, Any]]:
    """Load and group the export. Raises FileNotFoundError if it is missing."""
    export = json.loads(export_path.read_text())
    if not isinstance(export, dict):
        raise ValueError(f"{export_path} must be a JSON object with a traces array")
    return build_sessions(export)


class ReviewApp:
    """Process-wide session list, the active review queue, and note files."""

    def __init__(
        self,
        sessions: list[dict[str, Any]],
        queue: list[dict[str, str]],
        batches: list[dict[str, Any]],
        queue_batch: int,
    ) -> None:
        self.sessions = sessions
        self.queue = queue
        self.batches = batches
        self.queue_batch = queue_batch


def make_handler(app: ReviewApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
            return

        def _send_json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> Any:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return None
            try:
                return json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return None

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                html = (HERE / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return
            if path == "/api/sessions":
                self._send_json(
                    {
                        "sessions": app.sessions,
                        "queue": app.queue,
                        "queue_batch": app.queue_batch,
                        "batches": app.batches,
                    }
                )
                return
            if path in API_FILES:
                self._send_json(_read_json(API_FILES[path], API_DEFAULTS[path]))
                return
            self._send_json({"error": f"unknown path: {path}"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path not in API_FILES:
                self._send_json({"error": f"cannot POST to {path}"}, status=404)
                return
            data = self._read_body()
            if data is None:
                self._send_json({"error": "expected a JSON body"}, status=400)
                return
            _write_json(API_FILES[path], data)
            count = len(data) if isinstance(data, list) else len(data) if isinstance(data, dict) else 0
            self._send_json({"ok": True, "count": count})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8021)
    parser.add_argument("--export", type=Path, default=DEFAULT_EXPORT)
    args = parser.parse_args()

    sessions = load_sessions(args.export)
    traces = traces_from_sessions(sessions)
    batch1 = [{**pick, "review_batch": "1"} for pick in first_reading_batch(traces)]
    batch2 = role_reading_batch(traces, {pick["trace_id"] for pick in batch1})
    reviewed = {pick["trace_id"] for pick in batch1} | {pick["trace_id"] for pick in batch2}
    batch3 = refund_search_batch(traces, reviewed)
    reviewed |= {pick["trace_id"] for pick in batch3}
    batch4 = uniform_stability_batch(traces, reviewed)
    batches = [
        {
            "number": 1,
            "detail": "Fifteen uniform traces, then fifteen cluster representatives.",
            "picks": batch1,
        },
        {
            "number": 2,
            "detail": "User role, chosen before outcomes: 16 shopper, every remaining merchant, every remaining support.",
            "picks": batch2,
        },
        {
            "number": 3,
            "detail": "Depth search for premature_refund_claim: issue_refund calls, refund-action replies, and refund mentions with no action words. The filter is not a label.",
            "picks": batch3,
        },
        {
            "number": 4,
            "detail": "Fifteen more uniform traces, after the taxonomy draft, to see whether a new consequential failure still appears.",
            "picks": batch4,
        },
    ]
    _write_json(STATE_DIR / "sample_manifest.json", {"seed": 7, "batches": batches})
    app = ReviewApp(sessions, batch4, batches, queue_batch=4)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    turns = sum(len(session["turns"]) for session in sessions)
    print(f"review app on http://{args.host}:{args.port}/")
    print(f"{len(sessions)} sessions, {turns} traces, batch 4 has {len(batch4)} traces")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
