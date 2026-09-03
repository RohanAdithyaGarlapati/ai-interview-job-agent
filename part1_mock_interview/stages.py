"""Interview stages: self-introduction and past-experience.

Each stage has:
- A system prompt for the interviewer AI
- Transition logic (when to move to the next stage)
- Time-based fallback (force transition after N seconds)
"""
import asyncio
from enum import Enum
from typing import Callable, Optional
from dataclasses import dataclass


class InterviewStage(Enum):
    """Interview workflow stages."""
    SELF_INTRODUCTION = "self_introduction"
    PAST_EXPERIENCE = "past_experience"
    COMPLETE = "complete"


@dataclass
class StageTransition:
    """Represents a transition between stages."""
    current_stage: InterviewStage
    next_stage: InterviewStage
    reason: str  # "natural" (candidate signaled done), "timeout" (fallback timer)


class SelfIntroductionStage:
    """Initial stage: candidate introduces themselves and background."""

    SYSTEM_PROMPT = """You are a professional interviewer conducting a mock interview.
You are currently in the self-introduction stage.

Your role:
1. Greet the candidate warmly and professionally
2. Ask them to introduce themselves: name, background, current role, key skills
3. Listen for cues that they've finished their introduction (e.g., "That's about me", "That covers it")
4. Be conversational and encouraging; gently interrupt if they go off-topic
5. When you sense they've completed their introduction, acknowledge it and prepare to move to the next stage

Keep responses concise (2-3 sentences). Speak naturally, not robotically.
You have up to 60 seconds for this stage; after that, you'll automatically move on."""

    def __init__(self, timeout_seconds: int = 60):
        self.timeout_seconds = timeout_seconds
        self.start_time: Optional[float] = None

    async def initialize(self) -> str:
        """Return the opening prompt for this stage."""
        self.start_time = asyncio.get_event_loop().time()
        return "Hi! Welcome to the mock interview. Could you start by telling me a bit about yourself and your background?"

    async def should_transition(self, candidate_response: str) -> bool:
        """Determine if the candidate has signaled they're done with their introduction."""
        # Look for signals that they've finished
        done_signals = [
            "that's about me",
            "that covers my background",
            "that's pretty much it",
            "yeah that's me",
            "so that's my intro",
            "that's all",
            "anything else",
        ]
        lower_response = candidate_response.lower()
        return any(signal in lower_response for signal in done_signals)

    async def check_timeout(self) -> bool:
        """Check if the time limit for this stage has been exceeded."""
        if self.start_time is None:
            return False
        elapsed = asyncio.get_event_loop().time() - self.start_time
        return elapsed > self.timeout_seconds


class PastExperienceStage:
    """Second stage: delve into concrete past projects and lessons learned."""

    SYSTEM_PROMPT = """You are a professional interviewer conducting a mock interview.
You are currently in the past-experience stage.

Your role:
1. Ask about a specific past project or achievement (e.g., "Tell me about a challenging project you worked on")
2. Dig deeper: what was the context, what did they do, what was the outcome, what did they learn?
3. Ask follow-up questions to understand their technical skills, decision-making, and growth
4. Listen for natural concluding signals (e.g., "Yeah, that's the main one", "Nothing else major comes to mind")
5. Be genuinely interested; this is where real depth emerges

Keep responses concise (2-3 sentences). Use active listening: reference details they mentioned.
You have up to 120 seconds for this stage; after that, the interview will wrap up."""

    def __init__(self, timeout_seconds: int = 120):
        self.timeout_seconds = timeout_seconds
        self.start_time: Optional[float] = None

    async def initialize(self) -> str:
        """Return the opening prompt for this stage."""
        self.start_time = asyncio.get_event_loop().time()
        return "Great, thanks for that overview. Now I'd like to dive into your concrete experience. Can you walk me through a project or challenge you worked on that you're proud of?"

    async def should_transition(self, candidate_response: str) -> bool:
        """Determine if the candidate has signaled they're done with this stage."""
        done_signals = [
            "that's about it",
            "that covers it",
            "nothing else major",
            "that's the main one",
            "yeah that's pretty much it",
            "so that's my experience",
        ]
        lower_response = candidate_response.lower()
        return any(signal in lower_response for signal in done_signals)

    async def check_timeout(self) -> bool:
        """Check if the time limit for this stage has been exceeded."""
        if self.start_time is None:
            return False
        elapsed = asyncio.get_event_loop().time() - self.start_time
        return elapsed > self.timeout_seconds


class InterviewOrchestrator:
    """Manages the flow through interview stages."""

    def __init__(self):
        self.current_stage = InterviewStage.SELF_INTRODUCTION
        self.self_intro = SelfIntroductionStage()
        self.past_exp = PastExperienceStage()

    async def start(self) -> tuple[InterviewStage, str]:
        """Begin the interview."""
        if self.current_stage == InterviewStage.SELF_INTRODUCTION:
            prompt = await self.self_intro.initialize()
            return self.current_stage, prompt
        return self.current_stage, ""

    async def process_response(
        self, candidate_response: str
    ) -> tuple[InterviewStage, str, Optional[StageTransition]]:
        """
        Process a candidate's response and determine next action.

        Returns:
            (current_stage, next_prompt, transition_info)
        """
        transition = None

        if self.current_stage == InterviewStage.SELF_INTRODUCTION:
            # Check for natural or timeout-based transition
            natural_done = await self.self_intro.should_transition(candidate_response)
            timeout_hit = await self.self_intro.check_timeout()

            if natural_done or timeout_hit:
                reason = "natural" if natural_done else "timeout"
                transition = StageTransition(
                    current_stage=InterviewStage.SELF_INTRODUCTION,
                    next_stage=InterviewStage.PAST_EXPERIENCE,
                    reason=reason,
                )
                self.current_stage = InterviewStage.PAST_EXPERIENCE
                prompt = await self.past_exp.initialize()
                return self.current_stage, prompt, transition
            else:
                # Stay in this stage, ask a follow-up
                return (
                    self.current_stage,
                    "Tell me more about that.",
                    None,
                )

        elif self.current_stage == InterviewStage.PAST_EXPERIENCE:
            # Check for natural or timeout-based transition
            natural_done = await self.past_exp.should_transition(candidate_response)
            timeout_hit = await self.past_exp.check_timeout()

            if natural_done or timeout_hit:
                reason = "natural" if natural_done else "timeout"
                transition = StageTransition(
                    current_stage=InterviewStage.PAST_EXPERIENCE,
                    next_stage=InterviewStage.COMPLETE,
                    reason=reason,
                )
                self.current_stage = InterviewStage.COMPLETE
                closing = "Thanks for sharing that -- great insights. That wraps up our interview. Best of luck!"
                return self.current_stage, closing, transition
            else:
                # Stay in this stage, dig deeper
                return (
                    self.current_stage,
                    "Interesting. What did you learn from that experience?",
                    None,
                )

        return self.current_stage, "", None
