"""Step 7 - Send via Gmail (SMTP + App Password).

Case rule: the To: address is HARD-CODED to your own inbox (SEND_TO in .env),
never to the prospect. The intended recipient goes into the footer so you can
see who it would have reached.
"""
import smtplib
from email.message import EmailMessage

import config
from retry import with_retry


def send(subject: str, body: str, intended_name: str, intended_email: str,
         attachment: tuple[str, bytes, str, str] | None = None) -> str:
    """attachment = (filename, data, maintype, subtype), e.g. an .ics invite."""
    if not (config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD and config.SEND_TO):
        raise RuntimeError("Set GMAIL_ADDRESS, GMAIL_APP_PASSWORD and SEND_TO in .env")
    msg = EmailMessage()
    msg["From"] = config.GMAIL_ADDRESS
    msg["To"] = config.SEND_TO                      # hard-coded: your own inbox
    msg["Subject"] = subject
    msg["X-Intended-Recipient"] = f"{intended_name} <{intended_email}>"
    msg.set_content(f"{body}\n\n---\n[Pipeline test send. Intended recipient: "
                    f"{intended_name} <{intended_email}>]")
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
