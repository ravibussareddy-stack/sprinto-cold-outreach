"""Reading replies from Gmail: which messages count as replies, and what text is kept."""
import datetime as dt
from email.message import EmailMessage

from inbox import parse_reply, strip_quoted
from sender import SENT_HEADER

SUBJECT = "security reviews at Atlan"
START = dt.datetime(2026, 11, 18, 14, 0, tzinfo=dt.timezone.utc)


def raw(body, subject=f"Re: {SUBJECT}", date="Wed, 18 Nov 2026 15:30:00 +0000",
        mid="<r1@mail.gmail.com>", ours=False, html=False):
    m = EmailMessage()
    m["From"] = "Ravi <ravi@example.com>"
    m["To"] = "ravi@example.com"
    m["Subject"] = subject
    m["Date"] = date
    m["Message-ID"] = mid
    if ours:
        m[SENT_HEADER] = "1"
    if html:
        m.set_content("<p>" + body.replace("\n", "<br>") + "</p>", subtype="html")
    else:
        m.set_content(body)
    return m.as_bytes()


GMAIL_REPLY = """Sounds good - what times work for you?

On Wed, 18 Nov 2026 at 20:03, Ravi Bussareddy <ravi@example.com> wrote:
> Good evening Ravi,
> Hope you're doing well.
"""


def test_strips_gmail_quote():
    assert strip_quoted(GMAIL_REPLY) == "Sounds good - what times work for you?"


def test_strips_our_footer_and_outlook_history():
    text = "Friday works.\n\n---\n[Pipeline test send. Intended recipient: X <x@y>]"
    assert strip_quoted(text) == "Friday works."
    outlook = "Second option please.\n\n-----Original Message-----\nFrom: someone"
    assert strip_quoted(outlook) == "Second option please."


def test_reply_is_parsed():
    r = parse_reply(raw(GMAIL_REPLY), SUBJECT, START, set())
    assert r and r["id"] == "<r1@mail.gmail.com>"
    assert r["text"] == "Sounds good - what times work for you?"


def test_html_only_reply_is_parsed():
    r = parse_reply(raw("The second option works.", html=True), SUBJECT, START, set())
    assert r and "second option works" in r["text"]


def test_our_own_email_is_never_a_reply():
    assert parse_reply(raw("Would any of these work?", ours=True), SUBJECT, START, set()) is None


def test_already_processed_is_skipped():
    assert parse_reply(raw(GMAIL_REPLY), SUBJECT, START, {"<r1@mail.gmail.com>"}) is None


def test_other_subject_or_older_mail_is_skipped():
    assert parse_reply(raw(GMAIL_REPLY, subject="Re: lunch?"), SUBJECT, START, set()) is None
    assert parse_reply(raw(GMAIL_REPLY, date="Tue, 17 Nov 2026 09:00:00 +0000"), SUBJECT, START, set()) is None


def test_empty_reply_after_stripping_is_skipped():
    assert parse_reply(raw("> only quoted text"), SUBJECT, START, set()) is None
