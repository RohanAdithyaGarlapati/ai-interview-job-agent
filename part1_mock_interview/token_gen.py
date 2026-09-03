"""Generate a LiveKit access token for joining the mock interview room.

Usage:  python token_gen.py [room_name]
"""
import sys
import time
from urllib.parse import quote

from livekit import api

from config import LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_URL

# A fresh room name per run, deliberately. LiveKit dispatches an agent when a
# room is *created*, so re-joining a room that already exists (e.g. left over
# from a previous session, or still holding a stale browser tab) silently gets
# you a room with no interviewer in it. A unique name makes every join a room
# creation, which makes dispatch guaranteed.
ROOM = sys.argv[1] if len(sys.argv) > 1 else f"interview-{int(time.time())}"


def generate_token(room: str, identity: str) -> str:
    return (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name("Candidate")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_ttl(__import__("datetime").timedelta(hours=2))
        .to_jwt()
    )


if __name__ == "__main__":
    identity = f"candidate-{int(time.time())}"
    token = generate_token(ROOM, identity)

    print(f"Room:     {ROOM}")
    print(f"Identity: {identity}")
    print(f"\nToken:\n{token}")
    print(
        "\nOne-click join URL:\n"
        f"https://meet.livekit.io/custom?liveKitUrl={quote(LIVEKIT_URL, safe='')}"
        f"&token={token}"
    )
