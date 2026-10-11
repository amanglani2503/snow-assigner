import os

# Mock for the POC. In production: https://yourcompany.service-now.com
# On Cloud Run (Step 7) this becomes http://127.0.0.1:8080/mock-snow
SNOW_BASE_URL = os.getenv("SNOW_BASE_URL", "http://127.0.0.1:8000/mock-snow")


def snow_auth():
    """Basic auth if SNOW_USER and SNOW_PASSWORD are set (real instance), else None (mock)."""
    user, pwd = os.getenv("SNOW_USER"), os.getenv("SNOW_PASSWORD")
    return (user, pwd) if user and pwd else None


# --- Gemini / ADK (Vertex AI) ---
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "project-1ba841b0-6a6f-4b0d-afc")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
AGENT_TIMEOUT_SECONDS = float(os.getenv("AGENT_TIMEOUT_SECONDS", "45"))
AGENT_MAX_CONCURRENT = int(os.getenv("AGENT_MAX_CONCURRENT", "4"))