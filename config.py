"""Central configuration. Every threshold the system uses lives here so each
decision is visible and defensible in one place."""
import os
from dotenv import load_dotenv

load_dotenv()

# --- LLM: Google Gemini ----------------------------------------------------
# Keys are tried in order: GEMINI_API_KEY1..N, then GEMINI_API_KEY as a fallback.
# On free-tier quota exhaustion llm.py rotates to the next key.
GEMINI_API_KEYS = [
    v for v in (
        [os.getenv(f"GEMINI_API_KEY{i}", "") for i in range(1, 10)]
        + [os.getenv("GEMINI_API_KEY", "")]
    ) if v
]
GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""
# Check the current model list at ai.google.dev before a real run.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

# --- Storage: Neon (serverless Postgres) -----------------------------------
# Leave blank to run without a database (no page cache, no run history).
NEON_DATABASE_URL = os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL", "")

# --- Credentials -----------------------------------------------------------
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
# Case rule: the "to:" address is hard-coded to YOUR OWN inbox.
SEND_TO = os.getenv("SEND_TO", GMAIL_ADDRESS)

# --- Scraping --------------------------------------------------------------
USER_AGENT = "Mozilla/5.0 (compatible; SprintoOutreachResearch/1.0; student project)"
REQUEST_TIMEOUT = 15
DELAY_BETWEEN_REQUESTS = 1.5
THIN_PAGE_WORDS = 150
MAX_CLEAN_WORDS = 1500
USE_JINA_FALLBACK = os.getenv("USE_JINA_FALLBACK", "1") == "1"
PAGE_CACHE_HOURS = 24 * 7

# --- Agent loop ------------------------------------------------------------
MAX_EXTRA_FETCHES = 4
# Per-question limits from the evidence map: certs 1, saas 1, stage 2 (worst case 4)
FETCH_LIMITS = {"certs": 1, "saas": 1, "stage": 2}

# --- ICP thresholds --------------------------------------------------------
QUALIFY_SCORE = 50
REVIEW_SCORE = 35
MIN_EMPLOYEES, MAX_EMPLOYEES = 10, 1000

# --- Sequence 2: scheduling (L5 workflow) ----------------------------------
# Meeting slots are offered in this timezone (business hours only).
SLOT_TIMEZONE = os.getenv("SLOT_TIMEZONE", "Asia/Kolkata")
# "Good morning / afternoon / evening" in drafts is chosen by the local time here
# when the draft is written (the recipient's own timezone is not known).
GREETING_TIMEZONE = os.getenv("GREETING_TIMEZONE") or SLOT_TIMEZONE
SLOT_HOURS = (10, 15)              # 10:00 and 15:00 local
SLOTS_TO_OFFER = 3                 # "2-3 specific times"
SLOT_LOOKAHEAD_DAYS = 7
# Calendar source. Real calendar OAuth is not set up; point this at a JSON file
# of busy intervals: [["2026-11-13T10:00:00+05:30", "2026-11-13T11:00:00+05:30"], ...]
# Blank = no calendar access, which the agent treats as an escalation.
CALENDAR_BUSY_FILE = os.getenv("CALENDAR_BUSY_FILE", "")
# Calendly personal access token (availability:read). When set, live busy times
# from Calendly are used instead of CALENDAR_BUSY_FILE.
CALENDLY_TOKEN = os.getenv("CALENDLY_TOKEN", "")
# CALENDLY_BOOKING=1: offer only times your Calendly event type can be booked at, and book
# the agreed time through Calendly (it lands on the Google Calendar connected to Calendly,
# with a Meet link). Build 1: the Calendly invitee is SEND_TO, never the prospect.
CALENDLY_BOOKING = os.getenv("CALENDLY_BOOKING", "0") == "1"
CALENDLY_EVENT_TYPE = os.getenv("CALENDLY_EVENT_TYPE", "")   # event type URI; blank = first active one
# Prospect replies are read from REPLIES_DIR/<domain>.txt, one reply per block,
# blocks separated by a line containing only ---.
REPLIES_DIR = os.getenv("REPLIES_DIR", "replies")
# Where replies come from:
#   file  - REPLIES_DIR/<domain>.txt, read in the same run (tests and demos)
#   gmail - real replies in your inbox over IMAP; the conversation is saved in Neon
#           and check_replies.py continues it whenever a reply arrives
REPLY_MODE = os.getenv("REPLY_MODE", "file")
FOLLOWUP_HOURS = 48                # no reply within this window -> hand off
MAX_REPLY_ROUNDS = 2               # counter-proposals before escalating

# LangGraph checkpoints for Sequence 2 (pause at await_reply, resume on the next reply)
CHECKPOINT_DB = os.getenv("CHECKPOINT_DB", "checkpoints.sqlite")
