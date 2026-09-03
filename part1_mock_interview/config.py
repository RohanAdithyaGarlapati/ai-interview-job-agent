"""Configuration and credential management for the mock interview agent.

Place your credentials in a .env file or set them as environment variables.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# === LiveKit Configuration ===
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

# === Tavus Configuration ===
TAVUS_API_KEY = os.getenv("TAVUS_API_KEY", "")
TAVUS_REPLICA_ID = os.getenv("TAVUS_REPLICA_ID", "")  # Your Tavus persona/replica ID

# === LLM Configuration (OpenAI or Google Cloud) ===
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# === Interview Configuration ===
# These are *soft* budgets: when one elapses the interviewer is told to wrap the
# topic up and move on of its own accord, so the candidate is never cut off
# mid-sentence. main.py forces the transition only at HARD_LIMIT_MULTIPLIER x
# these values, if the nudge went unheeded.
SELF_INTRO_DURATION_SECONDS = 150       # ~2.5 min of intro + follow-ups
PAST_EXPERIENCE_DURATION_SECONDS = 240  # ~4 min to dig into a project

# === Validation ===
def validate_credentials():
    """Check that all required credentials are present."""
    missing = []

    if not LIVEKIT_API_KEY:
        missing.append("LIVEKIT_API_KEY")
    if not LIVEKIT_API_SECRET:
        missing.append("LIVEKIT_API_SECRET")
    # Tavus is deliberately not required. The avatar is the presentation layer;
    # main.py falls back to a voice-only interview if these are unset or the
    # account is out of conversational credits, and refusing to boot over a
    # missing avatar would take the whole interview down with it.
    if not (TAVUS_API_KEY and TAVUS_REPLICA_ID):
        print("Note: Tavus not configured - the interview will run voice-only.")
    # Speech (STT/LLM/TTS) runs through LiveKit Cloud Inference, billed to the
    # LiveKit project, so no model-provider key is required. GOOGLE_API_KEY is
    # only needed if main.py is switched back to the Gemini Live variant.

    if missing:
        print("Missing credentials:")
        for cred in missing:
            print(f"   - {cred}")
        print("\nSet these in a .env file or as environment variables.")
        return False
    return True
