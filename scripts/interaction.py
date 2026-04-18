"""
AI Provocateurs — Interaction Handler

Provides a unified interface for mid-pipeline user interaction across all entry points:
- CLI (stdin/stdout)
- Claude Code skill mode (returns structured data for AskUserQuestion)
- Web interface (asyncio.Event-based, used by Extension 3)

The pipeline calls handler.ask_user() when advisors request additional information
via <needs_info> tags. The handler presents the questions and returns the user's answers.
"""

import json
import re
import sys
import threading


def extract_needs_info(responses: list[dict]) -> list[str]:
    """Extract and deduplicate <needs_info> questions from advisor responses.

    Scans all advisor response texts for <needs_info>...</needs_info> tags.
    Deduplicates by normalizing whitespace and checking for substring matches.

    Args:
        responses: List of advisor response dicts with 'response' key.

    Returns:
        Deduplicated list of question strings. Empty if no questions found.
    """
    pattern = re.compile(r'<needs_info(?:\s+priority="[^"]*")?>(.*?)</needs_info>', re.DOTALL)
    raw_questions = []

    for resp in responses:
        text = resp.get("response", "")
        matches = pattern.findall(text)
        raw_questions.extend(q.strip() for q in matches if q.strip())

    # Deduplicate: remove questions that are substrings of other questions
    # or that are very similar (normalized comparison)
    if not raw_questions:
        return []

    unique = []
    normalized = []
    for q in raw_questions:
        norm = re.sub(r'\s+', ' ', q.lower().strip())
        is_duplicate = False
        for existing_norm in normalized:
            # Check substring or high overlap
            if norm in existing_norm or existing_norm in norm:
                is_duplicate = True
                break
        if not is_duplicate:
            unique.append(q)
            normalized.append(norm)

    # Cap at 5 questions to prevent abuse
    return unique[:5]


def strip_needs_info_tags(text: str) -> str:
    """Remove <needs_info> tags from a response text, keeping surrounding content."""
    return re.sub(
        r'\s*<needs_info(?:\s+priority="[^"]*")?>(.*?)</needs_info>\s*',
        '',
        text,
        flags=re.DOTALL,
    ).strip()


class InteractionHandler:
    """Abstract base for user interaction during pipeline execution."""

    def ask_user(self, questions: list[str], context: str = "") -> str | None:
        """Present questions to user and return their response.

        Args:
            questions: List of questions from advisors.
            context: Brief context about why these questions are being asked.

        Returns:
            User's answer as a string, or None if skipped/timed out.
        """
        raise NotImplementedError

    def notify_progress(self, step: str, detail: str = "") -> None:
        """Notify user of pipeline progress. Optional to implement."""
        pass


class CLIInteractionHandler(InteractionHandler):
    """Interactive CLI handler using stdin/stdout."""

    def __init__(self, timeout: int = 120):
        self.timeout = timeout

    def ask_user(self, questions: list[str], context: str = "") -> str | None:
        print(f"\n{'='*60}")
        print("  ADVISORS REQUEST ADDITIONAL INFORMATION")
        print(f"{'='*60}")
        if context:
            print(f"\n  {context}\n")
        print("  The following questions were raised:\n")
        for i, q in enumerate(questions, 1):
            print(f"  {i}. {q}")
        print(f"\n  (Type your response, or press Enter to skip)")
        print(f"  (Timeout: {self.timeout}s)")
        print(f"{'='*60}\n")

        # Use a thread to implement timeout on input()
        result = [None]
        def _read_input():
            try:
                result[0] = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                result[0] = None

        thread = threading.Thread(target=_read_input, daemon=True)
        thread.start()
        thread.join(timeout=self.timeout)

        if not thread.is_alive() and result[0]:
            return result[0]
        if thread.is_alive():
            print("\n  (Timed out — proceeding without additional input)")
        return None

    def notify_progress(self, step: str, detail: str = "") -> None:
        if detail:
            print(f"  {step}: {detail}", file=sys.stderr)
        else:
            print(f"  {step}", file=sys.stderr)


class SkillInteractionHandler(InteractionHandler):
    """Handler for Claude Code skill mode.

    Instead of directly interacting with the user, this handler returns
    structured data that the SKILL.md orchestrator uses to call AskUserQuestion.
    The skill orchestrator must:
    1. Call ask_user() to get the formatted question
    2. Present it via AskUserQuestion
    3. Call provide_answer() with the user's response
    """

    def __init__(self):
        self._pending_questions: list[str] | None = None
        self._answer: str | None = None

    def ask_user(self, questions: list[str], context: str = "") -> str | None:
        """Store questions and return formatted prompt for AskUserQuestion."""
        self._pending_questions = questions
        return None  # Skill orchestrator handles the actual interaction

    def get_formatted_question(self) -> str | None:
        """Get a formatted question string for AskUserQuestion."""
        if not self._pending_questions:
            return None
        lines = ["The deliberation advisors would like additional information:\n"]
        for i, q in enumerate(self._pending_questions, 1):
            lines.append(f"{i}. {q}")
        lines.append("\nPlease provide any relevant information, or say 'skip' to continue without answering.")
        return "\n".join(lines)

    def provide_answer(self, answer: str) -> None:
        """Provide the user's answer after AskUserQuestion returns."""
        if answer and answer.lower().strip() != "skip":
            self._answer = answer

    @property
    def answer(self) -> str | None:
        return self._answer


class WebInteractionHandler(InteractionHandler):
    """Handler for web interface mode (Extension 3).

    Uses threading events to synchronize between the pipeline thread
    and the async web handler. The pipeline blocks on ask_user() until
    the web endpoint calls provide_answer().

    The _questions_callback is set by PipelineRunner to push SSE events
    to the browser when questions are detected. Without it, questions
    are stored but never displayed.
    """

    def __init__(self, timeout: int = 300):
        self.timeout = timeout
        self._questions_event = threading.Event()
        self._answer_event = threading.Event()
        self._questions: list[str] | None = None
        self._answer: str | None = None
        self._progress_callback = None
        self._questions_callback = None

    def set_progress_callback(self, callback) -> None:
        """Set callback for progress notifications (used by SSE)."""
        self._progress_callback = callback

    def set_questions_callback(self, callback) -> None:
        """Set callback to notify the web layer when questions are detected.

        The callback receives (questions: list[str], context: str) and should
        push a 'needs_input' SSE event to the browser. Called from the pipeline
        thread — the callback must be thread-safe.
        """
        self._questions_callback = callback

    def ask_user(self, questions: list[str], context: str = "") -> str | None:
        """Store questions, notify the web layer via SSE, and wait for answer."""
        self._questions = questions
        self._questions_event.set()  # Signal that questions are ready

        # Push needs_input SSE event to the browser
        if self._questions_callback:
            self._questions_callback(questions, context)

        # Wait for answer from web endpoint
        answered = self._answer_event.wait(timeout=self.timeout)
        if answered and self._answer:
            return self._answer
        return None

    def get_pending_questions(self) -> list[str] | None:
        """Called by web layer to check if questions are pending."""
        if self._questions_event.is_set():
            return self._questions
        return None

    def wait_for_questions(self, timeout: float = None) -> list[str] | None:
        """Block until questions are available."""
        self._questions_event.wait(timeout=timeout)
        return self._questions

    def provide_answer(self, answer: str) -> None:
        """Called by web endpoint when user submits an answer."""
        if answer and answer.lower().strip() != "skip":
            self._answer = answer
        self._answer_event.set()  # Unblock the pipeline

    def notify_progress(self, step: str, detail: str = "") -> None:
        if self._progress_callback:
            self._progress_callback(step, detail)
