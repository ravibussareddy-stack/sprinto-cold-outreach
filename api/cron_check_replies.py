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
import datetime as dt  # noqa: E402
import db  # noqa: E402


def heartbeat(trigger: str, threads: int | None):
    """Record the latest reply check per caller, so the dashboard can show "last checked 40s ago"."""
    try:
        beats = db.get_setting("reply_checks") or {}
        beats[trigger] = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "threads": threads}
        db.put_setting("reply_checks", beats)
    except Exception:
        pass
import crawl  # noqa: E402
import time  # noqa: E402
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
        if "replies=1" in (self.path or ""):       # "Check replies now" on the dashboard: replies only
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    summary = check_replies.main(trigger="dashboard")
                heartbeat("dashboard", len((summary or {}).get("threads") or []))
                return self._send(200, {"ok": True, **(summary or {})})
            except Exception as e:
                return self._send(500, {"ok": False, "error": {"code": "internal_error", "message": f"{type(e).__name__}: {e}"}})
        if "crawl=1" in (self.path or ""):         # "Crawl now" on the dashboard: feeds + 1 company re-researched
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    result = crawl.run(limit=1, time_budget=150, trigger="dashboard", force=True)
                return self._send(200, {"ok": True, **result})
            except Exception as e:
                return self._send(500, {"ok": False, "error": {"code": "internal_error", "message": f"{type(e).__name__}: {e}"}})
        secret = os.getenv("CRON_SECRET")
        if secret and self.headers.get("Authorization") != f"Bearer {secret}":
            return self._send(401, {"error": {"code": "unauthorized", "message": "cron secret required"}})
        out = io.StringIO()
        try:
            started = time.time()
            from urllib.parse import parse_qs, urlparse
            caller = (parse_qs(urlparse(self.path or "").query).get("from") or [""])[0]
            trigger = caller if caller in ("github", "scheduler") else "vercel-cron"
            with contextlib.redirect_stdout(out):
                summary = check_replies.main(trigger=trigger)
            heartbeat(trigger, len((summary or {}).get("threads") or []))
            with contextlib.redirect_stdout(out):
                try:                               # then keep company data fresh, in the time that is left
                    left = 270 - (time.time() - started)
                    crawled = crawl.run(time_budget=max(0, left - 60), trigger=trigger) if left > 90 else {"skipped": "no time left"}
                except Exception as e:
                    crawled = {"error": f"{type(e).__name__}: {e}"}
            self._send(200, {"ok": True, **(summary or {}), "crawl": crawled, "log": out.getvalue().splitlines()[-60:]})
        except SystemExit as e:
            self._send(500, {"ok": False, "error": {"code": "not_configured", "message": str(e)}})
        except Exception as e:
            self._send(500, {"ok": False, "error": {"code": "internal_error", "message": f"{type(e).__name__}: {e}"},
                             "log": out.getvalue().splitlines()[-60:]})

    def log_message(self, fmt, *args):
        pass
