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
