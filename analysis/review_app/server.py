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
from datetime import datetime, timezone
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
LABELS_DIR = STATE_DIR / "labels"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def live_labels(directory: Path) -> list[dict[str, Any]]:
    """Return the current label for each trace and mode.

    A flipped label appends a new line and marks the old one superseded.
    """
    labels: list[dict[str, Any]] = []
    if not directory.exists():
        return labels
    for path in sorted(directory.glob("*.jsonl")):
        live: dict[str, dict[str, Any]] = {}
        for row in _read_jsonl(path):
            if row.get("superseded_by"):
                continue
            live[str(row.get("trace_id"))] = row
        labels.extend(live.values())
    return labels


def record_label(directory: Path, mode: str, trace_id: str, label: int) -> dict[str, Any]:
    """Append one present (1) or absent (0) judgment. Same value is a no-op."""
    if label not in (0, 1):
        raise ValueError("label must be 0 or 1")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{mode}.jsonl"
    rows = _read_jsonl(path)
    prior = [row for row in rows if row.get("trace_id") == trace_id and not row.get("superseded_by")]
    if prior and prior[-1].get("label") == label:
        return prior[-1]
    new_id = f"{trace_id}#{sum(1 for row in rows if row.get('trace_id') == trace_id)}"
    if prior:
        prior[-1]["superseded_by"] = new_id
    record = {
        "trace_id": trace_id,
        "mode": mode,
        "label": label,
        "source": "human",
        "ts": datetime.now(timezone.utc).isoformat(),
        "label_id": new_id,
    }
    rows.append(record)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return record


def _mode_names() -> set[str]:
    patterns = _read_json(STATE_DIR / "patterns.json", {"modes": []})
    return {
        str(mode.get("name"))
        for mode in patterns.get("modes") or []
        if isinstance(mode, dict) and mode.get("name")
    }


def _reviewed_ids(app: ReviewApp) -> set[str]:
    return {
        pick["trace_id"]
        for batch in app.batches
        for pick in batch.get("picks") or []
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
            if path == "/api/labels":
                self._send_json({"labels": live_labels(LABELS_DIR)})
                return
            if path in API_FILES:
                self._send_json(_read_json(API_FILES[path], API_DEFAULTS[path]))
                return
            self._send_json({"error": f"unknown path: {path}"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/api/labels":
                data = self._read_body()
                if not isinstance(data, dict):
                    self._send_json({"error": "expected a JSON object"}, status=400)
                    return
                mode = str(data.get("mode") or "")
                trace_id = str(data.get("trace_id") or "")
                try:
                    label = int(data.get("label"))
                except (TypeError, ValueError):
                    self._send_json({"error": "label must be 0 or 1"}, status=400)
                    return
                if mode not in _mode_names():
                    self._send_json({"error": f"unknown mode: {mode}"}, status=400)
                    return
                if trace_id not in _reviewed_ids(app):
                    self._send_json({"error": "trace is not in the review set"}, status=400)
                    return
                try:
                    record = record_label(LABELS_DIR, mode, trace_id, label)
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, "label": record})
                return
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
