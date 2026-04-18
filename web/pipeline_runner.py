"""Async wrapper around the synchronous orchestrate.py pipeline.

Bridges the sync pipeline with FastAPI's async architecture using
asyncio.to_thread() and asyncio.Queue for SSE event streaming.
"""

import asyncio
import sys
import time
from argparse import Namespace
from pathlib import Path

# Add scripts to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import llm_call
import orchestrate
import interaction

from web.session_store import SessionStore
from web.sse import make_event


class PipelineRunner:
    """Runs deliberation/analysis pipelines in background threads with SSE progress."""

    def __init__(self, session_store: SessionStore, max_concurrent: int = 3):
        self.session_store = session_store
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queues: dict[str, asyncio.Queue] = {}
        self._handlers: dict[str, interaction.WebInteractionHandler] = {}

    def get_queue(self, session_id: str) -> asyncio.Queue | None:
        return self._queues.get(session_id)

    def get_handler(self, session_id: str) -> interaction.WebInteractionHandler | None:
        return self._handlers.get(session_id)

    async def run_deliberate(self, session_id: str, question: str, mode: str = "council",
                              depth: str = None, length: str = None, rounds: int = 1,
                              no_interact: bool = False, chairman: str = None,
                              file_names: list[str] = None) -> None:
        """Run a deliberation pipeline in a background thread with SSE events."""
        queue = asyncio.Queue()
        self._queues[session_id] = queue

        # Create interaction handler for web mode
        handler = None
        if not no_interact:
            handler = interaction.WebInteractionHandler(timeout=300)
            self._handlers[session_id] = handler

            # Connect handler to SSE queue so questions reach the browser.
            # ask_user() runs in the pipeline thread, so use call_soon_threadsafe
            # to push the event onto the asyncio Queue from that thread.
            loop = asyncio.get_running_loop()
            def _on_questions(questions: list[str], context: str):
                event = make_event("needs_input", questions=questions, context=context)
                loop.call_soon_threadsafe(queue.put_nowait, event)
            handler.set_questions_callback(_on_questions)

        async with self._semaphore:
            self.session_store.update_status(session_id, "running", "starting")
            await queue.put(make_event("progress", step="starting", detail="Pipeline starting..."))

            try:
                start_time = time.time()

                if file_names:
                    await queue.put(make_event("progress", step="files",
                                               detail=f"Context files: {', '.join(file_names)}"))

                # Build args namespace for orchestrate
                args = Namespace(
                    question=question,
                    mode=mode,
                    depth=depth,
                    length=length,
                    rounds=rounds,
                    no_interact=no_interact,
                    blind=False,
                    chairman=chairman,
                    models=None,
                )

                # Run the sync pipeline in a thread
                await queue.put(make_event("progress", step="dispatch", detail="Dispatching advisors..."))

                result = await asyncio.to_thread(
                    orchestrate.run_deliberate, args, handler
                )

                duration_ms = int((time.time() - start_time) * 1000)

                # Store results in session database
                if result and isinstance(result, dict):
                    self.session_store.store_result(
                        session_id=session_id,
                        result_html=result.get("html", ""),
                        result_json={
                            "verdict": result.get("verdict", ""),
                            "metadata": result.get("metadata", {}),
                            "html_path": result.get("html_path", ""),
                            "md_path": result.get("md_path", ""),
                        },
                        duration_ms=duration_ms,
                    )
                else:
                    self.session_store.update_status(session_id, "complete")

                self.session_store.add_event(session_id, "complete", {
                    "duration_ms": duration_ms,
                })

                await queue.put(make_event("progress", step="complete",
                                           detail=f"Completed in {duration_ms/1000:.1f}s"))

            except Exception as e:
                error_msg = str(e)
                self.session_store.store_error(session_id, error_msg)
                await queue.put(make_event("error", message=error_msg))

            finally:
                # Signal stream completion
                await queue.put(None)
                # Cleanup
                self._queues.pop(session_id, None)
                self._handlers.pop(session_id, None)

    async def run_analyze(self, session_id: str, source: str, with_qa: bool = False,
                           qa_count: int = 10, lang: str = None, extract: bool = False,
                           length: str = None) -> None:
        """Run an analysis pipeline in a background thread."""
        queue = asyncio.Queue()
        self._queues[session_id] = queue

        async with self._semaphore:
            self.session_store.update_status(session_id, "running", "starting")
            await queue.put(make_event("progress", step="starting", detail="Pipeline starting..."))

            try:
                start_time = time.time()

                args = Namespace(
                    source=source,
                    with_qa=with_qa,
                    qa_count=qa_count,
                    lang=lang,
                    compare=False,
                    extract=extract,
                )

                await queue.put(make_event("progress", step="ingesting", detail="Reading source..."))
                result = await asyncio.to_thread(orchestrate.run_analyze, args)

                duration_ms = int((time.time() - start_time) * 1000)

                if result and isinstance(result, dict):
                    self.session_store.store_result(
                        session_id=session_id,
                        result_html=result.get("html", ""),
                        result_json={
                            "synthesis": result.get("synthesis", ""),
                            "html_path": result.get("html_path", ""),
                        },
                        duration_ms=duration_ms,
                    )
                else:
                    self.session_store.update_status(session_id, "complete")

                await queue.put(make_event("progress", step="complete",
                                           detail=f"Completed in {duration_ms/1000:.1f}s"))

            except Exception as e:
                self.session_store.store_error(session_id, str(e))
                await queue.put(make_event("error", message=str(e)))

            finally:
                await queue.put(None)
                self._queues.pop(session_id, None)

    async def provide_answer(self, session_id: str, answer: str) -> bool:
        """Provide user's answer for mid-pipeline interaction.

        Returns True if the answer was delivered to the pipeline.
        """
        handler = self._handlers.get(session_id)
        if handler:
            handler.provide_answer(answer)
            return True
        return False
