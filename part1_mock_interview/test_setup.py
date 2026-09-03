#!/usr/bin/env python
"""Quick validation that credentials are set up correctly."""
import asyncio
import sys
from config import validate_credentials, OPENAI_API_KEY
from tavus_integration import test_tavus_connection


async def test_openai():
    """Test that OpenAI API is reachable."""
    if not OPENAI_API_KEY:
        print("❌ OPENAI_API_KEY not set")
        return False

    try:
        # Just test that the key is valid by querying the models endpoint
        import openai
        openai.api_key = OPENAI_API_KEY
        models = openai.Model.list()
        print("✅ OpenAI connection OK")
        return True
    except Exception as e:
        print(f"❌ OpenAI error: {e}")
        return False


async def main():
    print("🔍 Testing mock interview setup...\n")

    # Test config
    print("1. Checking credentials...")
    if not validate_credentials():
        print("   Missing required credentials. See .env.example for setup.\n")
        return False

    print()

    # Test Tavus
    print("2. Testing Tavus connection...")
    tavus_ok = await test_tavus_connection()

    print()

    # Test OpenAI
    print("3. Testing OpenAI connection...")
    openai_ok = await test_openai()

    print()

    if tavus_ok and openai_ok:
        print("✅ All systems ready! Run: python main.py")
        return True
    else:
        print("❌ Some checks failed. Fix the errors above and try again.")
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
