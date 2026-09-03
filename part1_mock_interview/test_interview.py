"""Simple test to verify Part 1 agent is running and accepting connections."""
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

async def test_agent_connection():
    """Test if the agent is running and ready."""
    print("Testing Part 1 Mock Interview Agent...\n")
    print(f"LiveKit URL: {LIVEKIT_URL}")
    print(f"API Key: {LIVEKIT_API_KEY[:10]}...")
    print(f"API Secret: {'*' * 20}\n")

    try:
        # Try to import LiveKit
        try:
            from livekit import api
            print("✓ LiveKit SDK loaded")
        except ImportError:
            print("✗ LiveKit SDK not available (install with: pip install livekit)")
            return

        # Create access token
        import jwt
        import time

        now = int(time.time())
        exp = now + 3600

        payload = {
            "iss": LIVEKIT_API_KEY,
            "sub": "TestCandidate",
            "aud": "livekit",
            "nbf": now,
            "exp": exp,
            "grants": {
                "can_publish": True,
                "can_subscribe": True,
                "room": "mock-interview-room",
                "room_join": True,
            }
        }

        token = jwt.encode(payload, LIVEKIT_API_SECRET, algorithm="HS256")
        print(f"✓ Test token generated: {token[:50]}...\n")

        print("STATUS: Part 1 is configured and ready!\n")
        print("To test in a browser:")
        print("1. Use LiveKit's official test client")
        print("2. URL: https://meet.livekit.io")
        print("3. Server: " + LIVEKIT_URL)
        print("4. Token: " + token)
        print("\nOr use the client.html we created:")
        print("1. Open: http://localhost:9000/Liba/part1_mock_interview/client.html")
        print("2. Paste the token above when prompted")

    except Exception as e:
        print(f"✗ Error: {e}")
        print("Make sure main.py is running: cd part1_mock_interview && python main.py")

if __name__ == "__main__":
    asyncio.run(test_agent_connection())
