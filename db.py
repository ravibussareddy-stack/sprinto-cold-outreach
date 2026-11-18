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
ALTER TABLE threads ADD COLUMN IF NOT EXISTS replied BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE threads ADD COLUMN IF NOT EXISTS sentiment TEXT;          -- positive | negative | unclear
ALTER TABLE threads ADD COLUMN IF NOT EXISTS followups_sent INT NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS crm_outcomes (
    id BIGSERIAL PRIMARY KEY,
    domain TEXT NOT NULL,
    contact TEXT,
    outcome TEXT NOT NULL,
    record JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Clientele: one row per contact, with where they stand.
CREATE TABLE IF NOT EXISTS contacts (
    id BIGSERIAL PRIMARY KEY,
    domain TEXT NOT NULL,
    company TEXT,
    contact_name TEXT,
    contact_email TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL,      -- contacted | engaged | meeting_booked | cold_lead | non_respondent | escalated
    last_outcome TEXT,
    followups_sent INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (domain, contact_email)
);
-- Every email we send, so outcomes can be traced back to the exact email that earned them.
CREATE TABLE IF NOT EXISTS emails (
    id BIGSERIAL PRIMARY KEY,
    thread_id TEXT,
    domain TEXT NOT NULL,
    kind TEXT NOT NULL,        -- cold | followup_1..3 | proposal | clarify | confirm | invite | thanks
    subject TEXT,
    body TEXT,
    quality JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Feedback repo: guidance the writer uses, ranked by the outcome that produced it.
CREATE TABLE IF NOT EXISTS feedback (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,      -- human | outcome
    scope TEXT NOT NULL DEFAULT 'global',   -- global | role:<role> | industry:<industry>
    guidance TEXT NOT NULL,
    outcome_score REAL NOT NULL DEFAULT 0.5, -- 0..1: booked 1.0, positive 0.8, negative 0.3, no reply 0.1
    evidence JSONB,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Emails that earned a positive reply: style examples for similar roles.
CREATE TABLE IF NOT EXISTS exemplars (
    id BIGSERIAL PRIMARY KEY,
    domain TEXT NOT NULL,
    role TEXT,
    subject TEXT,
    body TEXT NOT NULL,
    outcome TEXT,
    outcome_score REAL NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (domain, subject)
);
-- Editable settings (the rubric from the dashboard's Filters & rubric page).
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Token use per model call.
CREATE TABLE IF NOT EXISTS llm_usage (
    id BIGSERIAL PRIMARY KEY,
    domain TEXT,
    purpose TEXT,              -- the structured output asked for, e.g. EmailDraft, ReplyRead
    model TEXT,
    input_tokens INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,
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


def save_run(rec: dict) -> int | None:
    """Store one run; returns its id (None without a database)."""
    if not enabled():
        return None
    try:
        _init()
        q = rec.get("qualification", {})
        with _conn() as c:
            return c.execute(
                "INSERT INTO runs (domain, decision, score, status, record) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (rec["domain"], q.get("decision"), q.get("score"), rec.get("status"),
                 Json(json.loads(json.dumps(rec, default=str))))).fetchone()[0]
    except psycopg.Error as e:
        print(f"  [db] run save skipped: {e}")
        return None


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
        cur = c.execute("SELECT thread_id, domain, subject, ctx, processed_ids, last_outbound_at, created_at, "
                        "replied, sentiment, followups_sent "
                        "FROM threads WHERE status = 'awaiting' ORDER BY created_at")
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def update_thread(thread_id: str, status: str, outcome: str, processed_ids: list[str],
                  touched_outbound: bool, replied: bool | None = None, sentiment: str | None = None,
                  followups_sent: int | None = None):
    _init()
    sets, args = ["status=%s", "outcome=%s", "processed_ids=%s", "updated_at=now()"], [status, outcome, Json(processed_ids)]
    if touched_outbound:
        sets.append("last_outbound_at=now()")
    if replied is not None:
        sets.append("replied=%s"); args.append(replied)
    if sentiment is not None:
        sets.append("sentiment=%s"); args.append(sentiment)
    if followups_sent is not None:
        sets.append("followups_sent=%s"); args.append(followups_sent)
    with _conn() as c:
        c.execute(f"UPDATE threads SET {', '.join(sets)} WHERE thread_id=%s", (*args, thread_id))


# ---- clientele, sent emails, token use (best effort: never stop a run) ------
def _safe(fn):
    def wrapper(*a, **k):
        if not enabled():
            return None
        try:
            _init()
            return fn(*a, **k)
        except psycopg.Error as e:
            print(f"  [db] {fn.__name__} skipped: {e}")
            return None
    return wrapper


@_safe
def upsert_contact(domain: str, company: str, name: str, email: str, title: str | None,
                   status: str, last_outcome: str | None = None, followups_sent: int | None = None):
    with _conn() as c:
        c.execute(
            "INSERT INTO contacts (domain, company, contact_name, contact_email, title, status, last_outcome, followups_sent) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,0)) "
            "ON CONFLICT (domain, contact_email) DO UPDATE SET status=EXCLUDED.status, "
            "last_outcome=COALESCE(EXCLUDED.last_outcome, contacts.last_outcome), "
            "followups_sent=COALESCE(%s, contacts.followups_sent), "
            "company=COALESCE(EXCLUDED.company, contacts.company), title=COALESCE(EXCLUDED.title, contacts.title), "
            "updated_at=now()",
            (domain, company, name, email, title, status, last_outcome, followups_sent, followups_sent))


@_safe
def log_email(thread_id: str | None, domain: str, kind: str, subject: str, body: str, quality=None):
    with _conn() as c:
        c.execute("INSERT INTO emails (thread_id, domain, kind, subject, body, quality) VALUES (%s,%s,%s,%s,%s,%s)",
                  (thread_id, domain, kind, subject, body, Json(quality) if quality is not None else None))


@_safe
def thread_emails(thread_id: str) -> list[dict]:
    with _conn() as c:
        cur = c.execute("SELECT kind, subject, body, created_at FROM emails WHERE thread_id=%s ORDER BY id", (thread_id,))
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@_safe
def record_usage(domain: str | None, purpose: str, model: str, input_tokens: int, output_tokens: int):
    with _conn() as c:
        c.execute("INSERT INTO llm_usage (domain, purpose, model, input_tokens, output_tokens) VALUES (%s,%s,%s,%s,%s)",
                  (domain, purpose, model, input_tokens, output_tokens))


# ---- feedback repo and exemplars (feedback.py, learn.py) ---------------------
@_safe
def add_feedback(source: str, scope: str, guidance: str, outcome_score: float, evidence: dict | None = None) -> int:
    with _conn() as c:
        return c.execute("INSERT INTO feedback (source, scope, guidance, outcome_score, evidence) "
                         "VALUES (%s,%s,%s,%s,%s) RETURNING id",
                         (source, scope, guidance, outcome_score, Json(evidence) if evidence else None)).fetchone()[0]


@_safe
def feedback_for(scopes: list[str], limit: int) -> list[dict]:
    """Active guidance for these scopes, best outcome first, newest first among equals."""
    with _conn() as c:
        cur = c.execute("SELECT id, source, scope, guidance, outcome_score, created_at FROM feedback "
                        "WHERE active AND scope = ANY(%s) ORDER BY outcome_score DESC, created_at DESC LIMIT %s",
                        (scopes, limit))
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@_safe
def all_feedback(limit: int = 200) -> list[dict]:
    with _conn() as c:
        cur = c.execute("SELECT id, source, scope, guidance, outcome_score, active, created_at FROM feedback "
                        "ORDER BY active DESC, outcome_score DESC, created_at DESC LIMIT %s", (limit,))
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@_safe
def add_exemplar(domain: str, role: str | None, subject: str, body: str, outcome: str, outcome_score: float):
    with _conn() as c:
        c.execute("INSERT INTO exemplars (domain, role, subject, body, outcome, outcome_score) VALUES (%s,%s,%s,%s,%s,%s) "
                  "ON CONFLICT (domain, subject) DO UPDATE SET outcome=EXCLUDED.outcome, "
                  "outcome_score=GREATEST(exemplars.outcome_score, EXCLUDED.outcome_score)",
                  (domain, role, subject, body, outcome, outcome_score))


@_safe
def exemplars_for(role: str | None, limit: int) -> list[dict]:
    """Best-scoring winning emails, same role first."""
    with _conn() as c:
        cur = c.execute("SELECT domain, role, subject, body, outcome, outcome_score FROM exemplars "
                        "ORDER BY (role IS NOT DISTINCT FROM %s) DESC, outcome_score DESC, created_at DESC LIMIT %s",
                        (role, limit))
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


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


@_safe
def get_setting(key: str):
    with _conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=%s", (key,)).fetchone()
    return row[0] if row else None
