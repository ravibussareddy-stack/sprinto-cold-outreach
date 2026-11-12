"""update_crm - write the outcome back to the CRM (Neon crm_outcomes table here).

Retry table: a DB outage is transient (retried); the outcome is then also kept
in the run report, so a manual log by the Growth Lead is always possible.
"""
import psycopg

import db
from retry import with_retry


def update_crm(domain: str, contact: str, outcome: str, record: dict) -> bool:
    try:
        with_retry(lambda: db.save_crm_outcome(domain, contact, outcome, record),
                   transient=(psycopg.OperationalError, OSError))
        return True
    except (psycopg.Error, OSError):
        return False
