"""Prospect replies, and reading their intent.

Replies come from REPLIES_DIR/<domain>.txt (blocks separated by a line '---').
The pipeline cannot see replies to a To: address that is your own inbox, so
replies are supplied as files. Each block is one turn of the conversation.

The model only classifies the reply into a fixed set of intents; code decides
what happens next.
"""
import os
from typing import Literal, Optional

from pydantic import BaseModel, Field

import config
from llm import ask


class ReplyRead(BaseModel):
    intent: Literal["interested", "confirms_time", "counter_proposes", "not_interested", "unclear"]
    chosen_slot_index: Optional[int] = Field(None, description="If confirms_time: 0-based index of the offered slot they picked, else null")
    proposed_times: list[str] = Field([], description="If counter_proposes: the times they suggested, as written")


SYSTEM = """You classify one reply from a prospect to a cold email about SOC 2 / ISO 27001 compliance.
- interested: wants to talk, asks questions, no specific time yet
- confirms_time: picks one of the offered slots (give chosen_slot_index)
- counter_proposes: wants a different time (give proposed_times)
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
    user = f"Offered slots:\n{slots}\n\nProspect reply:\n{reply}"
    return ask(ReplyRead, SYSTEM, user)
