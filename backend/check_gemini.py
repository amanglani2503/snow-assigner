import os
import sys

from google import genai

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "project-1ba841b0-6a6f-4b0d-afc")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")

# Best guesses at model IDs. Pass your own as arguments to test others.
CANDIDATES = sys.argv[1:] or [
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
]

client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
print(f"project={PROJECT} location={LOCATION}\n")

working = []
for model in CANDIDATES:
    try:
        r = client.models.generate_content(model=model, contents="Reply with the single word: ready")
        print(f"OK    {model}: {(r.text or '').strip()[:40]}")
        working.append(model)
    except Exception as e:
        print(f"FAIL  {model}: {str(e)[:160]}")

print("\nWorking models:", working or "none")