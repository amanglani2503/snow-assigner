import os

# Mock for the POC. In production: https://yourcompany.service-now.com
# On Cloud Run (Step 7) this becomes http://127.0.0.1:8080/mock-snow
SNOW_BASE_URL = os.getenv("SNOW_BASE_URL", "http://127.0.0.1:8000/mock-snow")


def snow_auth():
    """Basic auth if SNOW_USER and SNOW_PASSWORD are set (real instance), else None (mock)."""
    user, pwd = os.getenv("SNOW_USER"), os.getenv("SNOW_PASSWORD")
    return (user, pwd) if user and pwd else None

SNOW_LIVE = snow_auth() is not None          # real instance when credentials are set
SNOW_GROUPS = [g.strip() for g in os.getenv("SNOW_GROUPS", "Network").split(",") if g.strip()]
SYNC_SECONDS = int(os.getenv("SYNC_SECONDS", "15"))
MEMBERS_SYNC_SECONDS = int(os.getenv("MEMBERS_SYNC_SECONDS", "300"))
SNOW_EXTRA_QUERY = os.getenv("SNOW_EXTRA_QUERY", "")   # e.g. "opened_atONToday@javascript:gs.beginningOfToday()@javascript:gs.endOfToday()"

# --- Gemini / ADK (Vertex AI) ---
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "project-1ba841b0-6a6f-4b0d-afc")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
AGENT_TIMEOUT_SECONDS = float(os.getenv("AGENT_TIMEOUT_SECONDS", "45"))
AGENT_MAX_CONCURRENT = int(os.getenv("AGENT_MAX_CONCURRENT", "4"))