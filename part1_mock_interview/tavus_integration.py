"""Tavus real-time avatar integration for low-latency, lip-synced video responses.

Tavus provides:
- Real-time 2D/3D digital human rendering
- Automatic lip-sync from TTS audio
- Low-latency response (~500ms typical)
- WebRTC streaming to LiveKit
"""
import asyncio
import aiohttp
from typing import Optional
from config import TAVUS_API_KEY, TAVUS_REPLICA_ID


class TavusAvatarClient:
    """Manages real-time communication with Tavus for avatar video generation."""

    def __init__(self, api_key: str, replica_id: str):
        self.api_key = api_key
        self.replica_id = replica_id
        self.base_url = "https://api.tavus.io/v2"
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def generate_video(self, text: str, timeout_ms: int = 5000) -> Optional[str]:
        """
        Generate a short video response from the avatar speaking the given text.

        Args:
            text: The script for the avatar to speak (TTS will be applied automatically)
            timeout_ms: Max time to wait for video generation (Tavus targets ~500ms)

        Returns:
            URL to the generated video, or None if generation fails
        """
        if not self.session:
            return None

        payload = {
            "replica_id": self.replica_id,
            "script": {
                "type": "text",
                "input": text,
            },
            "video_resolution": "1080p",
            "output_format": "mp4",
        }

        try:
            async with self.session.post(
                f"{self.base_url}/generate-video",
                json=payload,
                headers=await self._headers(),
                timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("video_url")
                else:
                    print(f"Tavus error: {resp.status}")
                    return None
        except asyncio.TimeoutError:
            print("Tavus request timed out")
            return None
        except Exception as e:
            print(f"Tavus error: {e}")
            return None

    async def get_replica_status(self) -> dict:
        """Check the status of this replica (active, credentials valid, etc)."""
        if not self.session:
            return {"status": "not initialized"}

        try:
            async with self.session.get(
                f"{self.base_url}/replicas/{self.replica_id}",
                headers=await self._headers(),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    return {"status": "error", "code": resp.status}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class AvatarSession:
    """Wrapper for a single interview session with the Tavus avatar."""

    def __init__(self, api_key: str, replica_id: str):
        self.client = TavusAvatarClient(api_key, replica_id)
        self.initialized = False

    async def initialize(self) -> bool:
        """Verify credentials and avatar availability."""
        await self.client.__aenter__()
        status = await self.client.get_replica_status()
        self.initialized = status.get("status") == "ready"
        if not self.initialized:
            print(f"⚠️  Avatar not ready: {status}")
        return self.initialized

    async def speak(self, text: str) -> Optional[str]:
        """Generate video of the avatar speaking."""
        if not self.initialized:
            return None
        return await self.client.generate_video(text)

    async def close(self):
        """Clean up."""
        await self.client.__aexit__(None, None, None)


async def test_tavus_connection():
    """Quick test to validate Tavus credentials."""
    if not TAVUS_API_KEY or not TAVUS_REPLICA_ID:
        print("❌ Tavus credentials not set. Set TAVUS_API_KEY and TAVUS_REPLICA_ID.")
        return False

    session = AvatarSession(TAVUS_API_KEY, TAVUS_REPLICA_ID)
    if await session.initialize():
        print("✅ Tavus connection OK")
        await session.close()
        return True
    else:
        print("❌ Tavus connection failed")
        return False
