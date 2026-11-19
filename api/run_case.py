"""Vercel Python function: POST /api/run_case runs test cases through the full pipeline.
GET /api/run_case returns the input format. The logic lives in case_api.py (tested offline)."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# Emails this endpoint sends become real conversations: replies are read from Gmail and answered by the
# reply checker (cron), with follow-ups on the rubric's timers. Everything still goes only to SEND_TO.
os.environ.setdefault("REPLY_MODE", "gmail")

import case_api  # noqa: E402

MAX_BODY = 100_000


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict):
        data = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        self._send(200, {"service": "Sprinto cold outreach - run a test case", "input_format": case_api.INPUT_FORMAT})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return self._send(400, {"error": {"code": "empty_body", "message": "send a JSON body"},
                                    "input_format": case_api.INPUT_FORMAT})
        if length > MAX_BODY:
            return self._send(413, {"error": {"code": "body_too_large", "message": f"body over {MAX_BODY} bytes"}})
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            return self._send(400, {"error": {"code": "invalid_json", "message": f"body is not valid JSON: {e}"},
                                    "input_format": case_api.INPUT_FORMAT})
        try:
            code, out = case_api.run_cases(body)
        except Exception as e:                    # never let a request crash the function
            code, out = 500, {"error": {"code": "internal_error", "message": f"{type(e).__name__}: {e}"}}
        self._send(code, out)

    def do_PUT(self):
        self._send(405, {"error": {"code": "method_not_allowed", "message": "use POST (or GET for the format)"}})

    do_DELETE = do_PATCH = do_PUT

    def log_message(self, fmt, *args):            # keep Vercel logs to the pipeline's own output
        pass
