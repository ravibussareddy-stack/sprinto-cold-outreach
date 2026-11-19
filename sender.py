"""Step 7 - Send via Gmail (SMTP + App Password).

Case rule: the To: address is HARD-CODED to your own inbox (SEND_TO in .env),
never to the prospect. The intended recipient goes into the footer so you can
see who it would have reached.
"""
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid

import config
from retry import with_retry


SENT_HEADER = "X-Outreach-Sent"   # marks our own emails so inbox.py never reads them as replies


def send(subject: str, body: str, intended_name: str, intended_email: str,
         attachment: tuple[str, bytes, str, str] | None = None,
         in_reply_to: str | None = None) -> str:
    """attachment = (filename, data, maintype, subtype), e.g. an .ics invite.
    in_reply_to = Message-ID of the prospect's reply, so Gmail keeps one thread."""
    if not (config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD and config.SEND_TO):
        raise RuntimeError("Set GMAIL_ADDRESS, GMAIL_APP_PASSWORD and SEND_TO in .env")
    msg = EmailMessage()
    msg["From"] = config.GMAIL_ADDRESS
    msg["To"] = config.SEND_TO                      # hard-coded: your own inbox
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="sprinto-outreach.local")
    msg[SENT_HEADER] = "1"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    who = f"{intended_name} <{intended_email}>" if intended_email else f"{intended_name} (email not given)"
    msg["X-Intended-Recipient"] = who
    msg.set_content(f"{body}\n\n---\n[Pipeline test send. Intended recipient: {who}]")
    if attachment:
        fname, data, maintype, subtype = attachment
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=fname)
    def _send():
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
            s.send_message(msg)

    # Timeouts and dropped connections are transient; a bad app password is not
    # (SMTPAuthenticationError is not in the retry list, so it raises at once).
    with_retry(_send, transient=(smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError, OSError))
    return config.SEND_TO
