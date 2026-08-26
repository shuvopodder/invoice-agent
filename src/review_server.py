#!/usr/bin/env python3
"""Local server for reviewing and resolving invoices the pipeline flagged.

    python3 src/review_server.py
    (then open http://localhost:8090)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from accounting_client import AccountingClient
from dotenv_loader import load_dotenv
from logging_setup import setup_logging
from verify import verify_amounts

load_dotenv()

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
REVIEW_DIR = ROOT / "data" / "review_queue"
RESULTS_PATH = ROOT / "data" / "results.json"
INVOICES_DIR = ROOT / "invoices"
STATIC_DIR = ROOT / "review_ui"

PORT = int(os.environ.get("REVIEW_UI_PORT", "8090"))
API_URL = os.environ.get("ACCOUNTING_API_URL", "http://localhost:8080")
API_KEY = os.environ.get("ACCOUNTING_API_KEY", "demo-key-1234")

MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def _load_queue_item(name: str) -> dict | None:
    path = REVIEW_DIR / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _update_results_json(file_name: str, new_entry: dict) -> None:
    """Keeps data/results.json (the audit trail pipeline.py writes) in
    sync when the review UI resolves an item -- so results.json stays the
    single source of truth for "what happened to every invoice", whether
    it was resolved automatically or by a human here."""
    if not RESULTS_PATH.exists():
        return
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    for i, r in enumerate(results):
        if r["file"] == file_name:
            results[i] = new_entry
            break
    else:
        results.append(new_entry)
    RESULTS_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        logger.debug("%s %s", self.command, self.path)

    def _send_json(self, status: int, body: dict):
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_file(self, path: Path, content_type: str):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- GET ----

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return

        if path == "/api/queue":
            items = []
            if REVIEW_DIR.exists():
                for p in sorted(REVIEW_DIR.glob("*.json")):
                    item = json.loads(p.read_text(encoding="utf-8"))
                    items.append(
                        {
                            "file": item["file"],
                            "status": item["status"],
                            "reasons": item["detail"] if isinstance(item["detail"], list) else [str(item["detail"])],
                        }
                    )
            self._send_json(200, {"items": items})
            return

        if path.startswith("/api/queue/"):
            name = Path(path.removeprefix("/api/queue/")).stem
            item = _load_queue_item(name)
            if item is None:
                self._send_json(404, {"error": f"no review item for {name!r}"})
                return
            self._send_json(200, item)
            return

        if path == "/api/partners":
            client = AccountingClient(API_URL, API_KEY)
            try:
                partners = client.partners()
            except RuntimeError as e:
                self._send_json(502, {"error": str(e)})
                return
            self._send_json(200, {"partners": partners})
            return

        if path.startswith("/invoices/"):
            fname = Path(path.removeprefix("/invoices/")).name  # strips any path traversal
            fpath = INVOICES_DIR / fname
            suffix = fpath.suffix.lower()
            if not fpath.exists() or suffix not in MIME_BY_SUFFIX:
                self._send_json(404, {"error": "not found"})
                return
            self._send_file(fpath, MIME_BY_SUFFIX[suffix])
            return

        self._send_json(404, {"error": f"no such route: {path}"})

    # ---- POST ----

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "request body is not valid JSON"})
            return

        if path.startswith("/api/queue/") and path.endswith("/resubmit"):
            name = path[len("/api/queue/"):-len("/resubmit")]
            self._handle_resubmit(name, body)
            return

        if path.startswith("/api/queue/") and path.endswith("/dismiss"):
            name = path[len("/api/queue/"):-len("/dismiss")]
            self._handle_dismiss(name, body)
            return

        self._send_json(404, {"error": f"no such route: {path}"})

    def _handle_resubmit(self, name: str, corrected_payload: dict):
        item = _load_queue_item(name)
        if item is None:
            self._send_json(404, {"error": f"no review item for {name!r}"})
            return

        # The correction REPLACES the normalized payload wholesale (the UI
        # sends the full edited form back) -- re-verify it with the exact
        # same function the pipeline used, don't trust the edit blindly.
        verification = verify_amounts(corrected_payload)
        if not verification["ok"]:
            logger.info("%s: resubmit still fails verification: %s", name, verification["issues"])
            self._send_json(
                422,
                {"registered": False, "issues": verification["issues"], "expected": verification["expected"]},
            )
            return

        if not corrected_payload.get("partner_code"):
            self._send_json(422, {"registered": False, "issues": ["partner_code is still not set"]})
            return

        client = AccountingClient(API_URL, API_KEY)
        status, api_body = client.register(corrected_payload)

        if status in (201, 409):
            outcome = "registered" if status == 201 else "duplicate_per_api"
            logger.info("%s: %s via review UI", name, outcome)
            item.update(
                status=outcome,
                detail=api_body["data"] if status == 201 else api_body["error"],
                normalized=corrected_payload,
                resolved_by="review_ui",
            )
            _update_results_json(item["file"], item)
            queue_path = REVIEW_DIR / f"{name}.json"
            if queue_path.exists():
                queue_path.unlink()
            self._send_json(200, {"registered": True, "outcome": outcome, "detail": item["detail"]})
        else:
            logger.info("%s: API rejected the correction: %s", name, api_body.get("error"))
            self._send_json(422, {"registered": False, "issues": [api_body["error"]["message"]], "api_error": api_body["error"]})

    def _handle_dismiss(self, name: str, body: dict):
        """Marks an item as reviewed-but-not-registered (e.g. a genuinely
        new supplier that needs to be added to the partner master by
        someone else first) without pretending it succeeded."""
        item = _load_queue_item(name)
        if item is None:
            self._send_json(404, {"error": f"no review item for {name!r}"})
            return

        reason = body.get("reason", "dismissed without a reason")
        item.update(status="dismissed", detail=reason, resolved_by="review_ui")
        _update_results_json(item["file"], item)
        queue_path = REVIEW_DIR / f"{name}.json"
        if queue_path.exists():
            queue_path.unlink()
        logger.info("%s: dismissed via review UI (%s)", name, reason)
        self._send_json(200, {"dismissed": True})


def main():
    setup_logging()
    print(f"Review UI: http://localhost:{PORT}")
    print(f"Reading:   {REVIEW_DIR}")
    print(f"Accounting API expected at: {API_URL}")
    print("Press Ctrl+C to stop.")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
