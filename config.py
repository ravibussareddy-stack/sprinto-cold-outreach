"""Central configuration. Every threshold the system uses lives here so each
decision is visible and defensible in one place."""
import os
from dotenv import load_dotenv

load_dotenv()

# --- LLM: Google Gemini ----------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Check the current model list at ai.google.dev before a real run.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# --- Storage: Neon (serverless Postgres) -----------------------------------
# Leave blank to run without a database (no page cache, no run history).
NEON_DATABASE_URL = os.getenv("NEON_DATABASE_URL", "")

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

# --- ICP thresholds --------------------------------------------------------
QUALIFY_SCORE = 50
REVIEW_SCORE = 35
MIN_EMPLOYEES, MAX_EMPLOYEES = 10, 1000
