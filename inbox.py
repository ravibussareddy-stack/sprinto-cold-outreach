"""Read prospect replies from Gmail over IMAP (same account and app password as sending).

Build 1 sends every email to your own inbox (SEND_TO), so a "prospect reply" is a
reply in that inbox to one of our emails. A message counts as a reply when:
- its subject contains the cold email's subject (Gmail adds "Re: "),
- it arrived after the conversation started,
- it is not one of ours (our emails carry the X-Outreach-Sent header),
- it has not been processed before (tracked by Message-ID in the threads table).

Only the new text is passed on: quoted history and our footer are stripped.
"""
import datetime as dt
import email
import imaplib
import re
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

import config
from retry import with_retry
from sender import SENT_HEADER

IMAP_HOST = "imap.gmail.com"

# Where the quoted history starts in a reply.
_QUOTE_START = [
    re.compile(r"^On .{0,200}wrote:\s*$", re.M | re.S),          # Gmail / Apple Mail
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.M | re.I),  # Outlook
    re.compile(r"^From: .+$", re.M),                              # Outlook, forwarded headers
    re.compile(r"^---\s*\n\[Pipeline test send", re.M),           # our own footer
]


def _decode(value: str | None) -> str:
    return str(make_header(decode_header(value))) if value else ""


def _body_text(msg: Message) -> str:
    plain, html = None, None
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and html is None:
            html = BeautifulSoup(text, "html.parser").get_text("\n")
    return plain if plain is not None else (html or "")


def strip_quoted(text: str) -> str:
    """Keep only what the person wrote: cut at the first quote marker, drop '>' lines."""
    text = text.replace("\r\n", "\n")
    cut = len(text)
    for pat in _QUOTE_START:
        m = pat.search(text)
        if m:
            cut = min(cut, m.start())
    lines = [l for l in text[:cut].split("\n") if not l.lstrip().startswith(">")]
    return "\n".join(lines).strip()


def parse_reply(raw: bytes, subject: str, since: dt.datetime, exclude_ids: set[str]) -> dict | None:
    """Turn one raw message into a reply record, or None if it is not a new reply."""
    msg = email.message_from_bytes(raw)
    if msg.get(SENT_HEADER):
        return None
    mid = (msg.get("Message-ID") or "").strip()
    if not mid or mid in exclude_ids:
        return None
    if subject.lower() not in _decode(msg.get("Subject")).lower():
        return None
    try:
        when = parsedate_to_datetime(msg.get("Date"))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    if when < since:
        return None
    text = strip_quoted(_body_text(msg))
    if not text:
        return None
    return {"id": mid, "date": when, "from": _decode(msg.get("From")), "text": text}


def _search_subject(subject: str) -> str:
    # IMAP SUBJECT search is a substring match; keep it ASCII and quote-free.
    s = re.sub(r'[^\x20-\x7e]|"', " ", subject).strip()
    return s[:80]


def find_replies(subject: str, since: dt.datetime, exclude_ids: set[str]) -> list[dict]:
    """New replies to `subject` since `since`, oldest first. Network errors are retried."""
    if not (config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD):
        raise RuntimeError("Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD to read replies")

    def _fetch() -> list[bytes]:
        with imaplib.IMAP4_SSL(IMAP_HOST, 993) as m:
            m.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
            m.select("INBOX", readonly=True)
            day = (since - dt.timedelta(days=1)).strftime("%d-%b-%Y")
            typ, data = m.uid("SEARCH", None, "SINCE", day, "SUBJECT", f'"{_search_subject(subject)}"')
            if typ != "OK" or not data or not data[0]:
                return []
            raws = []
            for uid in data[0].split():
                typ, msg_data = m.uid("FETCH", uid, "(BODY.PEEK[])")   # PEEK: leave it unread
                if typ == "OK" and msg_data and isinstance(msg_data[0], tuple):
                    raws.append(msg_data[0][1])
            return raws

    raws = with_retry(_fetch, transient=(OSError, imaplib.IMAP4.abort))
    found = [r for r in (parse_reply(raw, subject, since, exclude_ids) for raw in raws) if r]
    return sorted(found, key=lambda r: r["date"])
