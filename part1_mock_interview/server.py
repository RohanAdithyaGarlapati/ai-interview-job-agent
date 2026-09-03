"""Public entry point for the mock interview: a join page plus a token endpoint.

Also supervises the LiveKit agent worker. Render's free tier only offers web
services - background workers are a paid product - so the worker is started as a
child process of this app rather than deployed separately. It is restarted if it
dies, because a web service that is up while its interviewer is not is worse than
being down: the candidate joins an empty room with no way to tell why.

Routes:
    GET  /            join page
    POST /api/token   mint a token for a fresh room
    GET  /healthz     liveness + whether the agent worker is running
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import pathlib
import sys
import time

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from livekit import api

from config import LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_URL, validate_credentials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("interview-server")

HERE = pathlib.Path(__file__).parent
app = FastAPI(title="AI Mock Interview")

_worker_proc: asyncio.subprocess.Process | None = None


# --------------------------------------------------------------------------- #
# Agent worker supervision
# --------------------------------------------------------------------------- #

async def _supervise_worker() -> None:
    """Keep `main.py start` alive for as long as the web service is up."""
    global _worker_proc
    backoff = 2
    while True:
        logger.info("starting agent worker")
        _worker_proc = await asyncio.create_subprocess_exec(
            sys.executable, str(HERE / "main.py"), "start",
            stdout=sys.stdout, stderr=sys.stderr,
        )
        code = await _worker_proc.wait()
        _worker_proc = None
        # Exponential backoff, capped: if the worker is crash-looping on bad
        # credentials, hammering LiveKit's registration endpoint helps nobody.
        logger.error("agent worker exited (code %s); restarting in %ss", code, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


@app.on_event("startup")
async def _startup() -> None:
    if not validate_credentials():
        logger.error("missing credentials - agent worker not started")
        return
    asyncio.create_task(_supervise_worker())


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Liveness for the web tier, plus the worker's true state in the body.

    Deliberately 200 even when the worker is down. The platform health check
    reacts to non-2xx by restarting the container, which would fight the
    supervisor above - it is already restarting the worker with backoff, and
    killing the container mid-backoff just loses that state and drops any
    interview in progress. Read `agent_worker_running` to know whether a
    candidate would actually meet an interviewer.
    """
    running = _worker_proc is not None and _worker_proc.returncode is None
    return JSONResponse({"ok": True, "agent_worker_running": running})


@app.post("/api/token")
async def mint_token() -> JSONResponse:
    """Mint a token for a brand-new room.

    A fresh room name per candidate is deliberate: LiveKit dispatches an agent
    when a room is *created*, so reusing one fixed room means anybody joining
    while a previous room still lingers gets no interviewer at all.
    """
    room = f"interview-{int(time.time() * 1000)}"
    identity = f"candidate-{int(time.time() * 1000)}"
    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name("Candidate")
        .with_grants(
            api.VideoGrants(
                room_join=True, room=room,
                can_publish=True, can_subscribe=True, can_publish_data=True,
            )
        )
        .with_ttl(datetime.timedelta(hours=2))
        .to_jwt()
    )
    return JSONResponse({"url": LIVEKIT_URL, "token": token, "room": room})


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((HERE / "static" / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
