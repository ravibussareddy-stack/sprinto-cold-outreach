"""Vercel Python function:
  POST /api/redraft {"run_id": 28}                      redraft with the latest feedback (nothing is sent)
  POST /api/redraft {"run_id": 28, "contact": {...}}    redraft it for a different person
  POST /api/redraft {"run_id": 28, "action": "send"}    send this draft and start its conversation
Logic: redraft.py."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Drafts sent from the dashboard become tracked conversations, like the pipeline's own sends.
os.environ.setdefault("REPLY_MODE", "gmail")

import redraft  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict):
        data = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(405, {"error": {"code": "method_not_allowed", "message": 'POST {"run_id": <id>}'}})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            run_id = int(body.get("run_id"))
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            return self._send(400, {"error": {"code": "invalid_body", "message": 'send {"run_id": <number>}'}})
        try:
            if body.get("action") == "send":            # the draft page's Send button
                return self._send(200, redraft.send_run(run_id))
            self._send(200, redraft.redraft(run_id, contact=body.get("contact")))
        except redraft.RedraftError as e:
            self._send(e.status, {"error": {"code": e.code, "message": e.message}})
        except Exception as e:
            self._send(500, {"error": {"code": "internal_error", "message": f"{type(e).__name__}: {e}"}})

    def log_message(self, fmt, *args):
        pass
