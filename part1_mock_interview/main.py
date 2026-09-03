"""LiveKit mock interview agent with a real-time Tavus avatar.

The interviewer runs a full, open-ended interview rather than a scripted set of
stages on a clock: it works through an agenda of topics, asks follow-ups grounded
in what the candidate actually said, and moves on when a thread is exhausted.
There are deliberately no wall-clock stage timers - an interview ends when the
conversation is done, and cutting a candidate off mid-answer to satisfy a timer
is exactly what makes a mock interview feel fake.

Speech is a stitched STT -> LLM -> TTS pipeline running on LiveKit Cloud
Inference, so the only credentials required are the LiveKit ones. See
TURN_HANDLING below for the responsiveness tuning. The Tavus avatar consumes the
session audio and publishes a lip-synced video track when credits allow; if it
is unavailable the interview continues voice-only.
"""
from __future__ import annotations

import logging

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
)
from livekit.agents import APIConnectOptions
from livekit.agents.voice.turn import TurnHandlingOptions
from livekit.plugins import silero, tavus

from config import (
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    TAVUS_API_KEY,
    TAVUS_REPLICA_ID,
    validate_credentials,
)

logger = logging.getLogger("mock-interview")


INTERVIEWER_INSTRUCTIONS = """\
You are a warm, sharp technical interviewer running a mock interview. This is a
real conversation, not a questionnaire. Your goal is to understand this person
properly, the way a good interviewer would.

HOW YOU TALK
- Ask ONE question, then stop and listen to the entire answer.
- The moment they finish, come straight back - no dead air. Start with a short
  reaction ("oh nice", "got it", "mm, interesting") and let your actual question
  follow right behind it. Never open with a long preamble.
- Keep your turns short. This is speech. Two or three sentences is usually
  plenty. Never read out bullet points, headings, or stage directions.
- Never ask two questions in one turn.
- If they interrupt you, stop talking immediately and listen.
- If they give you a very short or thin answer ("yeah, a few projects"), do not
  accept it and move on - that is the moment to ask for the specifics.

HOW YOU QUESTION
- Your next question should come out of what they just said, using their own
  words: "you said the migration was painful - what actually broke?"
- Dig. When an answer is vague, general, or interesting, follow up rather than
  moving on. Three or four exchanges on one thread is good interviewing.
- Prefer specifics over generalities. If they say "we improved performance", ask
  by how much, measured how, and what the bottleneck turned out to be.
- Ask about their own contribution, not just what the team did.
- Be curious rather than interrogating. You are trying to understand, not catch
  them out.

THE AGENDA
Work through these areas in roughly this order, spending several exchanges on
each. This is a guide, not a script - follow genuinely interesting threads
wherever they go, and skip anything that clearly does not apply.

1.  Introduction - who they are, how they got here.
2.  Their background - the roles and transitions they mentioned, and why.
3.  A project they are proud of - what it was and why it mattered.
4.  Their specific contribution to it, in detail.
5.  The hardest technical problem in it, and how they worked it out.
6.  Design and trade-off decisions - what they chose, what they rejected, why.
7.  Something that went wrong, and what they learned.
8.  How they work with other people - disagreements, reviews, mentoring.
9.  What they are curious about or learning now.
10. Invite their questions, then thank them warmly and close.

Keep going through this. Do not wrap the interview up early - there is a lot to
get through, and a short interview is a bad interview. Open by greeting them and
asking them to tell you about themselves.
"""


# How fast the interviewer comes back once the candidate stops talking.
#
# endpointing.min_delay is the floor on silence before the turn is considered
# over - the dead air paid on every single reply. 'dynamic' lets it stretch
# toward max_delay when the candidate sounds mid-thought (trailing "and...",
# "so..."), so a snappy floor does not mean getting cut off while thinking.
#
# preemptive_generation is the real win: the reply starts being generated, and
# spoken, from the partial transcript *before* the turn is formally committed,
# so most of the model's thinking time is spent while the candidate is still
# finishing. That time stops being latency the candidate can feel.
TURN_HANDLING: TurnHandlingOptions = {
    "endpointing": {"mode": "dynamic", "min_delay": 0.25, "max_delay": 2.0},
    "preemptive_generation": {"enabled": True, "preemptive_tts": True},
}


class InterviewAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INTERVIEWER_INSTRUCTIONS)

    async def on_enter(self) -> None:
        self.session.generate_reply(
            instructions=(
                "Greet the candidate warmly, introduce yourself as their interviewer "
                "in one sentence, then ask them to tell you about themselves."
            )
        )


def prewarm(proc: JobProcess) -> None:
    """Load the VAD model up front so the first turn is not slowed by it."""
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    logger.info("connected to room %s", ctx.room.name)

    # A stitched STT -> LLM -> TTS pipeline rather than one speech-to-speech
    # socket. Gemini Live was the obvious choice for latency, but in practice its
    # free tier stalled: it would accept a turn and never emit a generation
    # ("generate_reply timed out waiting for generation_created"), leaving 100+
    # seconds of dead air mid-interview. Separate components fail independently
    # and each one here is individually fast, which is the better trade for
    # something that has to hold up live in front of an interviewer.
    #
    # These are LiveKit Cloud Inference model strings, billed to the LiveKit
    # project, so no per-vendor API keys are needed.
    session = AgentSession(
        stt="deepgram/nova-3",
        llm="google/gemini-2.5-flash",
        tts="cartesia/sonic-2",
        vad=ctx.proc.userdata["vad"],
        turn_handling=TURN_HANDLING,
    )

    # The avatar is the presentation layer, not the interview. If Tavus is out of
    # credits, rate-limited or down, fall back to voice-only rather than taking the
    # whole session with it - a working audio interview beats no interview at all.
    try:
        avatar = tavus.AvatarSession(
            face_id=TAVUS_REPLICA_ID,
            api_key=TAVUS_API_KEY,
            avatar_participant_name="Interviewer",
            # Fail fast. The default 3 retries at 2s apart spend ~14s before
            # giving up, and that is 14s of silence the candidate sits through
            # before the interview even opens. Errors worth retrying here are
            # rare; a 402 or an outage is not going to clear in six seconds.
            conn_options=APIConnectOptions(max_retry=0, timeout=5.0),
        )
        await avatar.start(
            session,
            room=ctx.room,
            livekit_url=LIVEKIT_URL,
            livekit_api_key=LIVEKIT_API_KEY,
            livekit_api_secret=LIVEKIT_API_SECRET,
        )
        logger.info("tavus avatar started (face %s)", TAVUS_REPLICA_ID)
    except Exception as e:
        logger.warning(
            "tavus avatar unavailable (%s: %s) - continuing voice-only", type(e).__name__, e
        )

    await session.start(agent=InterviewAgent(), room=ctx.room)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not validate_credentials():
        raise SystemExit(1)
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
