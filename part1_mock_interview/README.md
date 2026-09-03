# AI Mock Interview with Real-Time Digital Avatar

Real-time mock interview using **LiveKit AI Multi-Agent** + **Tavus** for a low-latency digital human interviewer.

## What this does

- **Two-stage interview**: self-introduction (up to 60s) → past-experience (up to 120s)
- **Real-time avatar**: Tavus renders the interviewer with automatic lip-sync, ~500ms latency
- **Smooth transitions**: natural (candidate signals done) + time-based fallback (ensures progress)
- **Gentle interruptions**: handled by the LLM interviewer agent
- **WebRTC streaming**: low-latency two-way interaction via LiveKit

## Architecture

```
Candidate (browser/app) ──WebRTC─→ LiveKit Server
                                        ↓
                            LiveKit AI Agent (LLM)
                                   ↓      ↓
                                TTS    Interview
                               (text)  Orchestrator
                                ↓
                            Tavus API
                               ↓
                          Avatar Video
                               ↓
                          WebRTC ──→ Candidate
```

## Setup

### 1. Get credentials

You'll need accounts on three services (all have free trials):

#### LiveKit Cloud
- Sign up at https://livekit.io
- Create a project
- Copy: **API Key** and **API Secret**

#### Tavus (Real-time Avatar)
- Sign up at https://tavus.io
- Create a Persona (your digital interviewer avatar)
- Copy: **API Key** and **Replica ID** (persona ID)

#### OpenAI (LLM for the interviewer)
- Get API key from https://platform.openai.com
- Set up billing

### 2. Set credentials

Create a `.env` file in this directory:

```bash
# .env
LIVEKIT_URL=ws://your-livekit-cloud-url:7880
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret

TAVUS_API_KEY=your_tavus_key
TAVUS_REPLICA_ID=your_persona_id

OPENAI_API_KEY=your_openai_key
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the agent

```bash
python main.py
```

This starts a LiveKit worker. Candidates can join via:
- LiveKit JavaScript SDK (web)
- LiveKit mobile SDK (iOS/Android)
- Any WebRTC client

### 5. Test the connection

Before running a full interview, test that credentials work:

```bash
python -c "
import asyncio
from config import OPENAI_API_KEY, validate_credentials
from tavus_integration import test_tavus_connection

async def test():
    if validate_credentials():
        await test_tavus_connection()

asyncio.run(test())
"
```

## How it works

### Self-Introduction Stage (60s max)

1. Agent: "Could you start by telling me about yourself?"
2. Candidate speaks their intro
3. Agent listens for "that's about me" or waits 60s
4. When done (naturally or by timeout), transitions to Stage 2

### Past-Experience Stage (120s max)

1. Agent: "Tell me about a challenging project you worked on"
2. Candidate describes a project
3. Agent asks follow-ups: "What did you learn?", "Tell me more"
4. When candidate signals done or 120s elapsed, wraps up

### Real-time Avatar

For each agent response:
1. LLM generates text (e.g., "Great, tell me more about that")
2. Text → Tavus API
3. Tavus generates HD video of avatar speaking (with lip-sync)
4. Video → LiveKit WebRTC → Candidate's screen

**Latency**: ~500ms per response (Tavus generation) + network. Total response time: 1-2 seconds, feels natural.

## Files

- **main.py** — LiveKit agent entry point (handles room lifecycle, coordinates stages)
- **stages.py** — Interview logic (self-intro → past-exp, transition triggers, timeouts)
- **tavus_integration.py** — Real-time avatar API client (video generation, credential mgmt)
- **config.py** — Credentials and tuning params
- **requirements.txt** — Python dependencies
- **.env** — Your credentials (create this, don't commit)

## Customization

Edit `stages.py` to change:
- Interview questions ("Tell me about yourself")
- Stage durations (currently 60s + 120s)
- Transition signals (what "done" looks like)
- Follow-up prompts ("Tell me more", "What did you learn?")

Edit `config.py` to tune:
- `SELF_INTRO_DURATION_SECONDS`
- `PAST_EXPERIENCE_DURATION_SECONDS`
- `TRANSITION_TIMEOUT_SECONDS` (fallback timer)

## Deployment (Production)

For a live product:

1. **LiveKit**: Use LiveKit Cloud (managed) or self-host (enterprise)
2. **Tavus**: Paid tier for production personas
3. **LLM**: Use OpenAI, Google Cloud, or your own model
4. **Hosting**: Run agent on Lambda, Cloud Functions, or EC2
5. **Frontend**: Build a web/mobile app with LiveKit SDK to let candidates join rooms

Example LiveKit room join (JavaScript):

```javascript
const room = new Room();
await room.connect("ws://livekit-server", token);
// Candidate now sees the avatar and hears the interviewer
```

## Limitations & Known Issues

1. **Tavus API latency**: ~500ms to generate video per response. For faster TTM, consider:
   - Pre-caching common responses
   - Using a faster TTS + pre-recorded avatar segments
   - Increasing timeout tolerances

2. **Interruption handling**: Currently gentle (agent asks clarifying questions). For assertive interruption (talking over the candidate), requires speech-to-text segmentation + real-time intervention.

3. **Natural transitions**: Fallback timers guarantee progress, but "natural" detection (candidate says "that's it") is regex-based. A real system would use intent classification or confidence thresholds.

4. **No recording**: This scaffolding doesn't record the interview. Add LiveKit recording service for production.

## Next Steps

1. **Wire up credentials** and test with `python main.py`
2. **Build a frontend** (React + LiveKit SDK) so candidates can join rooms
3. **Add analytics** — track stage times, transcript, scores
4. **Tune personas** — customize Tavus replica appearance/voice
5. **A/B test** — different interview styles, questions, avatar appearances

---

**Questions?** Check the Tavus docs (https://docs.tavus.io) and LiveKit docs (https://docs.livekit.io).
