"""Step 7 - Send via Gmail (SMTP + App Password).

Case rule: the To: address is HARD-CODED to your own inbox (SEND_TO in .env),
never to the prospect. The intended recipient goes into the footer so you can
see who it would have reached.
"""
import smtplib
from email.message import EmailMessage

import config


def send(subject: str, body: str, intended_name: str, intended_email: str) -> str:
    if not (config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD and config.SEND_TO):
        raise RuntimeError("Set GMAIL_ADDRESS, GMAIL_APP_PASSWORD and SEND_TO in .env")
    msg = EmailMessage()
    msg["From"] = config.GMAIL_ADDRESS
    msg["To"] = config.SEND_TO                      # hard-coded: your own inbox
    msg["Subject"] = subject
    msg["X-Intended-Recipient"] = f"{intended_name} <{intended_email}>"
    msg.set_content(f"{body}\n\n---\n[Pipeline test send. Intended recipient: "
                    f"{intended_name} <{intended_email}>]")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
        s.send_message(msg)
    return config.SEND_TO
