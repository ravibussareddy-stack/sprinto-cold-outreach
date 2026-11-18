"""Neon (Postgres) storage: page cache and run history.
Every function is a no-op when NEON_DATABASE_URL is blank, so the pipeline
still runs locally without a database."""
import datetime as dt
import json

import psycopg
from psycopg.types.json import Json

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS page_cache (
    url TEXT PRIMARY KEY,
    record JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS runs (
    id BIGSERIAL PRIMARY KEY,
    domain TEXT NOT NULL,
    decision TEXT,
    score INT,
    status TEXT,
    record JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS runs_domain_idx ON runs (domain, created_at DESC);
CREATE TABLE IF NOT EXISTS reviews (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES runs(id),
    domain TEXT NOT NULL,
    email_action TEXT NOT NULL,          -- kept | rewritten | not_drafted
    poc_correct BOOLEAN,                 -- NULL when no PoC was found
    minutes NUMERIC,                     -- human minutes spent on this company
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS threads (
    thread_id TEXT PRIMARY KEY,          -- LangGraph thread (checkpoints live in Neon too)
    domain TEXT NOT NULL,
    subject TEXT NOT NULL,               -- cold email subject; replies are matched on it
    ctx JSONB NOT NULL,                  -- contact, company, sender name
    status TEXT NOT NULL DEFAULT 'awaiting',   -- awaiting | done
    outcome TEXT,
    processed_ids JSONB NOT NULL DEFAULT '[]', -- Message-IDs of replies already handled
    last_outbound_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS crm_outcomes (
    id BIGSERIAL PRIMARY KEY,
    domain TEXT NOT NULL,
    contact TEXT,
    outcome TEXT NOT NULL,
    record JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_ready = False


def _conn():
    return psycopg.connect(config.NEON_DATABASE_URL, autocommit=True)


def enabled() -> bool:
    return bool(config.NEON_DATABASE_URL)


def _init():
    global _ready
    if not _ready:
        with _conn() as c:
            c.execute(SCHEMA)
        _ready = True


def get_cached_page(url: str) -> dict | None:
    if not enabled():
        return None
    try:
        _init()
        with _conn() as c:
            row = c.execute(
                "SELECT record FROM page_cache WHERE url=%s AND fetched_at > now() - make_interval(hours => %s)",
                (url, config.PAGE_CACHE_HOURS)).fetchone()
        return row[0] if row else None
    except psycopg.Error as e:
        print(f"  [db] cache read skipped: {e}")
        return None


def put_cached_page(url: str, record: dict):
    if not enabled():
        return
    try:
        _init()
        with _conn() as c:
            c.execute(
                "INSERT INTO page_cache (url, record, fetched_at) VALUES (%s, %s, now()) "
                "ON CONFLICT (url) DO UPDATE SET record=EXCLUDED.record, fetched_at=now()",
                (url, Json(record)))
    except psycopg.Error as e:
        print(f"  [db] cache write skipped: {e}")


def save_run(rec: dict):
    if not enabled():
        return
    try:
        _init()
        q = rec.get("qualification", {})
        with _conn() as c:
            c.execute(
                "INSERT INTO runs (domain, decision, score, status, record) VALUES (%s,%s,%s,%s,%s)",
                (rec["domain"], q.get("decision"), q.get("score"), rec.get("status"),
                 Json(json.loads(json.dumps(rec, default=str)))))
    except psycopg.Error as e:
        print(f"  [db] run save skipped: {e}")


def save_crm_outcome(domain: str, contact: str, outcome: str, record: dict):
    """Write-back for the update_crm tool. Raises on DB errors so the caller can retry."""
    if not enabled():
        return
    _init()
    with _conn() as c:
        c.execute(
            "INSERT INTO crm_outcomes (domain, contact, outcome, record) VALUES (%s,%s,%s,%s)",
            (domain, contact, outcome, Json(json.loads(json.dumps(record, default=str)))))


# ---- conversations waiting for a reply (check_replies.py) --------------------
def save_thread(thread_id: str, domain: str, subject: str, ctx: dict):
    """Raises on DB errors: without this row the reply could never be matched."""
    _init()
    with _conn() as c:
        c.execute("INSERT INTO threads (thread_id, domain, subject, ctx) VALUES (%s,%s,%s,%s)",
                  (thread_id, domain, subject, Json(ctx)))


def awaiting_threads() -> list[dict]:
    _init()
    with _conn() as c:
        cur = c.execute("SELECT thread_id, domain, subject, ctx, processed_ids, last_outbound_at, created_at "
                        "FROM threads WHERE status = 'awaiting' ORDER BY created_at")
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def update_thread(thread_id: str, status: str, outcome: str, processed_ids: list[str],
                  touched_outbound: bool):
    _init()
    with _conn() as c:
        c.execute("UPDATE threads SET status=%s, outcome=%s, processed_ids=%s, updated_at=now()"
                  + (", last_outbound_at=now()" if touched_outbound else "") + " WHERE thread_id=%s",
                  (status, outcome, Json(processed_ids), thread_id))


def last_run(domain: str) -> dict | None:
    """Most recent saved run record for a domain (used to spot a new certificate
    for a dormant company). None when there is no database or no earlier run."""
    if not enabled():
        return None
    _init()
    with _conn() as c:
        row = c.execute("SELECT record FROM runs WHERE domain=%s ORDER BY created_at DESC LIMIT 1",
                        (domain,)).fetchone()
    return row[0] if row else None


# ---- review queue and measurement (review.py) -------------------------------
QUEUE_SQL = """
SELECT r.id, r.domain, r.decision, r.status, r.created_at
FROM runs r
WHERE (r.decision IN ('QUALIFIED_FLAGGED', 'MANUAL_REVIEW', 'REACTIVATED')
       OR r.status ILIKE '%%escalated%%' OR r.status ILIKE '%%aborted%%'
       OR r.status ILIKE '%%ERROR%%' OR r.status ILIKE '%%DRAFT_FAILED%%')
  AND NOT EXISTS (SELECT 1 FROM reviews v WHERE v.run_id = r.id)
ORDER BY r.created_at DESC
LIMIT %s
"""


def review_queue(limit: int = 50) -> list[tuple]:
    if not enabled():
        return []
    _init()
    with _conn() as c:
        return c.execute(QUEUE_SQL, (limit,)).fetchall()


def add_review(domain: str, email_action: str, poc_correct: bool | None,
               minutes: float | None, note: str) -> int:
    _init()
    with _conn() as c:
        row = c.execute("SELECT id FROM runs WHERE domain=%s ORDER BY created_at DESC LIMIT 1",
                        (domain,)).fetchone()
        if not row:
            raise ValueError(f"no run saved for {domain}; run the pipeline first")
        c.execute(
            "INSERT INTO reviews (run_id, domain, email_action, poc_correct, minutes, note) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (row[0], domain, email_action, poc_correct, minutes, note))
        return row[0]


def review_rows() -> list[tuple]:
    if not enabled():
        return []
    _init()
    with _conn() as c:
        return c.execute("SELECT email_action, poc_correct, minutes FROM reviews").fetchall()
