"""Prospect replies, and reading their intent.

Replies come from REPLIES_DIR/<domain>.txt (blocks separated by a line '---').
The pipeline cannot see replies to a To: address that is your own inbox, so
replies are supplied as files. Each block is one turn of the conversation.

The model only classifies the reply into a fixed set of intents; code decides
what happens next.
"""
import datetime as dt
import os
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

import config
from llm import ask


class ReplyRead(BaseModel):
    intent: Literal["interested", "confirms_time", "counter_proposes", "not_interested", "unclear"]
    chosen_slot_index: Optional[int] = Field(None, description="If confirms_time: 0-based index of the offered slot they picked, else null")
    proposed_times: list[str] = Field([], description="If counter_proposes: the times they suggested, as written")
    proposed_start: Optional[str] = Field(None, description=(
        "If they name one specific day and time (e.g. 'Tuesday 12pm'), its start as ISO 8601 "
        "with UTC offset, resolved against today's date in the given timezone (the next such "
        "day in the future). Null if no specific time, or if it is a range or vague ('next week')."))


SYSTEM = """You classify one reply from a prospect to a cold email about SOC 2 / ISO 27001 compliance.
- interested: wants to talk, asks questions, no specific time yet
- confirms_time: picks one of the offered slots (give chosen_slot_index)
- counter_proposes: suggests a time of their own (give proposed_times and proposed_start),
  including a first reply like "Yes, can we do Tuesday at 12pm?"
- not_interested: declines, unsubscribe, wrong person, no need
- unclear: anything else, including out-of-office. Never guess."""


def load_replies(domain: str) -> list[str]:
    path = os.path.join(config.REPLIES_DIR, f"{domain}.txt")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        blocks = f.read().split("\n---\n")
    return [b.strip() for b in blocks if b.strip()]


def classify(reply: str, offered_slots: list[str]) -> ReplyRead:
    slots = "\n".join(f"{i}: {s}" for i, s in enumerate(offered_slots)) or "(none offered yet)"
    now = dt.datetime.now(ZoneInfo(config.SLOT_TIMEZONE))
    user = (f"Today is {now:%A %d %B %Y, %H:%M} in timezone {config.SLOT_TIMEZONE} "
            f"(UTC offset {now:%z}).\n\nOffered slots:\n{slots}\n\nProspect reply:\n{reply}")
    return ask(ReplyRead, SYSTEM, user, temperature=0.0)
