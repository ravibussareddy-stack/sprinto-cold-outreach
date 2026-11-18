"""Vercel cron (daily, see vercel.json): runs the same reply check as the GitHub workflow.
A Postgres advisory lock in check_replies.main() stops two checks from overlapping.
If CRON_SECRET is set in Vercel, Vercel sends it as a bearer token and other callers are refused."""
import contextlib
import io
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("REPLY_MODE", "gmail")

import check_replies  # noqa: E402
import tune  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict):
        data = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if "tune=1" in (self.path or ""):          # "Check now" on the rubric page: tuning only, always allowed
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    result = tune.run(force=True)
                return self._send(200, {"ok": True, **result})
            except Exception as e:
                return self._send(500, {"ok": False, "error": {"code": "internal_error", "message": f"{type(e).__name__}: {e}"}})
        secret = os.getenv("CRON_SECRET")
        if secret and self.headers.get("Authorization") != f"Bearer {secret}":
            return self._send(401, {"error": {"code": "unauthorized", "message": "cron secret required"}})
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                summary = check_replies.main(trigger="vercel-cron")
            self._send(200, {"ok": True, **(summary or {}), "log": out.getvalue().splitlines()[-60:]})
        except SystemExit as e:
            self._send(500, {"ok": False, "error": {"code": "not_configured", "message": str(e)}})
        except Exception as e:
            self._send(500, {"ok": False, "error": {"code": "internal_error", "message": f"{type(e).__name__}: {e}"},
                             "log": out.getvalue().splitlines()[-60:]})

    def log_message(self, fmt, *args):
        pass
