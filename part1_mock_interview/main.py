"""LiveKit mock interview agent with a real-time Tavus avatar.

Two stages, per the brief: self-introduction, then past experience.

Transitions are driven two ways so the interview always progresses:

  - Normally, the interviewer decides the stage is done and calls the
    `advance_stage` tool. This is the "well-defined switching logic": one
    explicit call, so a transition is a discrete event rather than something
    inferred from the model drifting onto a new topic.

  - If that never fires - a candidate who rambles, or a model that will not let
    go of a thread - a wall-clock fallback steps in. It escalates rather than
    cutting in: at the stage budget the interviewer is *told* to wrap up and
    move on itself, so the handover still lands on a natural beat; only at
    HARD_LIMIT_MULTIPLIER x the budget is the transition forced outright.

Within a stage the interviewer is asked to keep digging - follow-ups grounded in
what the candidate actually said - because a stage that is one question long is
not an interview. The budgets are generous for that reason.

Speech is a stitched STT -> LLM -> TTS pipeline on LiveKit Cloud Inference, so
the only credentials required are the LiveKit ones. See TURN_HANDLING for the
responsiveness tuning. The Tavus avatar publishes a lip-synced video track when
credits allow; if it is unavailable the interview continues voice-only.
"""
from __future__ import annotations

import asyncio
import logging

from livekit.agents import (
    Agent,
    AgentSession,
    APIConnectOptions,
    JobContext,
    JobProcess,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.agents.voice.turn import TurnHandlingOptions
from livekit.plugins import silero, tavus

from config import (
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    PAST_EXPERIENCE_DURATION_SECONDS,
    SELF_INTRO_DURATION_SECONDS,
    TAVUS_API_KEY,
    TAVUS_REPLICA_ID,
    validate_credentials,
)

logger = logging.getLogger("mock-interview")


BASE_INSTRUCTIONS = """\
You are a warm, sharp technical interviewer running a mock interview. This is a
real conversation, not a questionnaire.

HOW YOU TALK
- Ask ONE question, then stop and listen to the whole answer.
- The moment they finish, come straight back - no dead air. Open with a short
  reaction ("oh nice", "got it", "mm, interesting") and let the question follow
  right behind it.
- Keep turns short. This is speech. Two or three sentences is usually plenty.
  Never read out bullet points, headings, or stage directions.
- Never ask two questions in one turn.
- If they interrupt you, stop talking immediately and listen.
- If an answer is thin ("yeah, a few projects"), do not accept it and move on -
  that is exactly when to ask for specifics.

HOW YOU QUESTION
- Your next question should come out of what they just said, in their own
  words: "you said the migration was painful - what actually broke?"
- Dig. Three or four exchanges on one thread is good interviewing, not stalling.
- Prefer specifics. If they say "we improved performance", ask by how much,
  measured how, and what the bottleneck turned out to be.
- Ask what *they* did, not only what the team did.
"""

STAGE_INSTRUCTIONS = {
    "self_introduction": (
        "STAGE 1 of 2 - SELF-INTRODUCTION. Ask them to tell you about "
        "themselves, then have a real conversation about it: pick up on the "
        "specifics they mention - a company, a role, a technology, a change of "
        "direction - and ask about those. Stay here for several exchanges. When "
        "you genuinely understand their background and the thread has run its "
        "course, call the advance_stage tool. Do not move to their projects "
        "without calling it."
    ),
    "past_experience": (
        "STAGE 2 of 2 - PAST EXPERIENCE. Ask about a challenging project they "
        "have worked on, then dig in over several exchanges: their specific "
        "contribution, what made it hard, the trade-offs they chose, what they "
        "would do differently, what they took from it. Follow the threads their "
        "answers open up. When the topic is genuinely exhausted, call the "
        "advance_stage tool to wrap up."
    ),
    "complete": (
        "The interview is over. Thank them warmly, tell them they will hear back "
        "with feedback, and say goodbye. Do not ask further questions."
    ),
}

STAGE_ORDER = ["self_introduction", "past_experience", "complete"]

# Soft budgets: reaching one prompts the interviewer to wrap the stage up of its
# own accord, so the candidate is never cut off mid-sentence.
STAGE_BUDGET = {
    "self_introduction": SELF_INTRO_DURATION_SECONDS,
    "past_experience": PAST_EXPERIENCE_DURATION_SECONDS,
}

# How far past its budget a stage may run before the transition is forced.
HARD_LIMIT_MULTIPLIER = 1.75

# Floor on how long a stage must have been live before another transition is
# honoured. Guards the timer-vs-tool race in _go_next; must stay well under the
# smallest budget so genuine early transitions still land.
MIN_SECONDS_IN_STAGE = 15.0


# How fast the interviewer comes back once the candidate stops talking.
#
# endpointing.min_delay is the dead air paid on every single reply. 'dynamic'
# lets it stretch toward max_delay when the candidate sounds mid-thought
# (trailing "and...", "so..."), so a snappy floor does not mean being cut off
# while thinking.
#
# preemptive_generation is the larger win: the reply is generated, and spoken,
# from the partial transcript *before* the turn is formally committed, so most
# of the model's thinking happens while the candidate is still finishing and
# stops being latency they can feel.
TURN_HANDLING: TurnHandlingOptions = {
    "endpointing": {"mode": "dynamic", "min_delay": 0.25, "max_delay": 2.0},
    "preemptive_generation": {"enabled": True, "preemptive_tts": True},
}


class InterviewAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=f"{BASE_INSTRUCTIONS}\n\n{STAGE_INSTRUCTIONS['self_introduction']}"
        )
        self.stage = "self_introduction"
        self._timer: asyncio.Task | None = None
        self._entered_stage_at = 0.0

    async def on_enter(self) -> None:
        self._entered_stage_at = asyncio.get_running_loop().time()
        self._arm_timer()
        self.session.generate_reply(
            instructions=(
                "Greet the candidate warmly, introduce yourself as their interviewer "
                "in one sentence, then ask them to tell you about themselves."
            )
        )

    @function_tool
    async def advance_stage(self, ctx: RunContext) -> str:
        """Move the interview on to the next stage. Call this once the candidate
        has finished with the current stage's topic."""
        return await self._go_next(reason="interviewer judged the stage complete")

    async def _go_next(self, reason: str) -> str:
        idx = STAGE_ORDER.index(self.stage)
        if idx >= len(STAGE_ORDER) - 1:
            return "The interview is already complete."

        # The fallback timer and the model's own advance_stage call can race: the
        # timer fires, and a moment later the tool call the model had *already
        # decided on* for the previous stage lands and advances again, skipping a
        # stage outright. Ignore a transition arriving right behind another - it
        # is that stale duplicate, not a real second handover.
        since = asyncio.get_running_loop().time() - self._entered_stage_at
        if since < MIN_SECONDS_IN_STAGE:
            logger.info(
                "ignoring '%s' %.1fs into %s (stale duplicate)", reason, since, self.stage
            )
            return f"Still on stage: {self.stage}. Continue with the current topic."

        self.stage = STAGE_ORDER[idx + 1]
        self._entered_stage_at = asyncio.get_running_loop().time()
        logger.info("stage -> %s (%s)", self.stage, reason)

        await self.update_instructions(
            f"{BASE_INSTRUCTIONS}\n\n{STAGE_INSTRUCTIONS[self.stage]}"
        )
        self._arm_timer()
        return f"Moved to stage: {self.stage}. Continue from the new instructions."

    def _arm_timer(self) -> None:
        """Time-based fallback, in two escalating steps.

        A single hard cut at the budget makes the interview feel like a kitchen
        timer, so: at the budget the interviewer is nudged to wrap up and
        transition itself, and only at the hard limit is the change forced.
        """
        if self._timer:
            self._timer.cancel()
            self._timer = None

        budget = STAGE_BUDGET.get(self.stage)
        if budget is None:
            return

        stage_when_armed = self.stage

        async def _fire() -> None:
            try:
                await asyncio.sleep(budget)
                if self.stage != stage_when_armed:
                    return  # advanced naturally; nothing to nudge
                logger.info("soft nudge: %ss elapsed on %s", budget, stage_when_armed)
                await self.update_instructions(
                    f"{BASE_INSTRUCTIONS}\n\n{STAGE_INSTRUCTIONS[stage_when_armed]}\n\n"
                    "You have spent a while here. Ask at most one more question, "
                    "then call advance_stage to move on."
                )

                await asyncio.sleep(budget * HARD_LIMIT_MULTIPLIER - budget)
                if self.stage != stage_when_armed:
                    return  # took the hint
                logger.info("hard limit on %s - forcing transition", stage_when_armed)
                await self._go_next(reason="time-based fallback")
                self.session.generate_reply(
                    instructions=(
                        "Briefly acknowledge what they just said, then move the "
                        "conversation on to your next question."
                    )
                )
            except asyncio.CancelledError:
                return

        self._timer = asyncio.create_task(_fire())


def prewarm(proc: JobProcess) -> None:
    """Load the VAD model up front so the first turn is not slowed by it."""
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    logger.info("connected to room %s", ctx.room.name)

    # A stitched pipeline rather than one speech-to-speech socket. Gemini Live
    # was the obvious choice for latency, but its free tier stalled in practice:
    # it would accept a turn and never emit a generation ("generate_reply timed
    # out waiting for generation_created"), leaving 100+ seconds of dead air
    # mid-interview. Separate components fail independently, which is the better
    # trade for something that has to hold up live.
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
    # credits, rate-limited or down, fall back to voice-only rather than taking
    # the session with it - a working audio interview beats no interview.
    try:
        avatar = tavus.AvatarSession(
            face_id=TAVUS_REPLICA_ID,
            api_key=TAVUS_API_KEY,
            avatar_participant_name="Interviewer",
            # Fail fast. The default 3 retries at 2s apart spend ~14s before
            # giving up, and that is 14s of silence before the interview opens.
            # A 402 or an outage will not clear in six seconds.
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
            "tavus avatar unavailable (%s: %s) - continuing voice-only",
            type(e).__name__, e,
        )

    await session.start(agent=InterviewAgent(), room=ctx.room)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not validate_credentials():
        raise SystemExit(1)
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
