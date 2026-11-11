"""Central configuration. Every threshold the system uses lives here so each
decision is visible and defensible in one place."""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Model -----------------------------------------------------------------
MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("CLAUDE_EFFORT", "low")  # extraction/drafting are simple tasks

# --- Credentials -----------------------------------------------------------
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")          # account that sends
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")  # Google App Password
# Case rule: the "to:" address is hard-coded to YOUR OWN inbox.
SEND_TO = os.getenv("SEND_TO", GMAIL_ADDRESS)

# --- Scraping --------------------------------------------------------------
USER_AGENT = "Mozilla/5.0 (compatible; SprintoOutreachResearch/1.0; student project)"
REQUEST_TIMEOUT = 15
DELAY_BETWEEN_REQUESTS = 1.5      # seconds - be polite, avoid rate limits
THIN_PAGE_WORDS = 150             # below this, assume JS-rendered -> try Jina Reader
MAX_CLEAN_WORDS = 1500            # ~2,000 tokens cap per page sent to the model
USE_JINA_FALLBACK = os.getenv("USE_JINA_FALLBACK", "1") == "1"
CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")

# --- Agent loop ------------------------------------------------------------
# Hard cap set BEFORE the build (lecture: "a loop without a limit is a cost
# without a ceiling"). Worst case = 4 fetches beyond the homepage.
MAX_EXTRA_FETCHES = 4

# --- ICP thresholds --------------------------------------------------------
QUALIFY_SCORE = 50        # >= this -> QUALIFIED
REVIEW_SCORE = 35         # between REVIEW and QUALIFY -> QUALIFIED_FLAGGED
MIN_EMPLOYEES, MAX_EMPLOYEES = 10, 1000
