"""Async wrapper around the synchronous orchestrate.py pipeline.

Bridges the sync pipeline with FastAPI's async architecture using
asyncio.to_thread() and asyncio.Queue for SSE event streaming.
"""

import asyncio
import contextlib
import sys
import threading
import time
from argparse import Namespace
from datetime import datetime
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

    def __init__(self, session_store: SessionStore, max_concurrent: int = 3,
                 heartbeat_interval: float = 10.0):
        self.session_store = session_store
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queues: dict[str, asyncio.Queue] = {}
        self._handlers: dict[str, interaction.WebInteractionHandler] = {}
        self._cancel_tokens: dict[str, threading.Event] = {}
        self.heartbeat_interval = heartbeat_interval

    def get_queue(self, session_id: str) -> asyncio.Queue | None:
        return self._queues.get(session_id)

    def prepare_queue(self, session_id: str) -> asyncio.Queue:
        """Create the SSE queue before the browser subscribes to events.

        FastAPI background tasks start after the redirect response is sent, so
        the progress page can open its EventSource before run_* has executed.
        Preparing the queue synchronously avoids a false "Session not found".
        """
        queue = self._queues.get(session_id)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[session_id] = queue
        return queue

    def get_handler(self, session_id: str) -> interaction.WebInteractionHandler | None:
        return self._handlers.get(session_id)

    def _is_session_canceled(self, session_id: str) -> bool:
        session = self.session_store.get_session(session_id)
        return bool(session and session.get("status") == "canceled")

    @staticmethod
    def _progress_payload(step: str, detail: str, status: str = "running") -> dict:
        return {
            "step": step,
            "detail": detail,
            "status": status,
            "at": datetime.now().strftime("%H:%M:%S"),
        }

    async def _heartbeat_loop(
        self,
        session_id: str,
        queue: asyncio.Queue,
        started_at: float,
        cancel_token: threading.Event,
    ) -> None:
        while not cancel_token.is_set():
            await asyncio.sleep(self.heartbeat_interval)
            if cancel_token.is_set() or self._is_session_canceled(session_id):
                return
            session = self.session_store.get_session(session_id) or {}
            if session.get("status") != "running":
                return
            await queue.put(make_event(
                "heartbeat",
                step="working",
                detail=session.get("progress_step") or "Still working...",
                status="running",
                at=datetime.now().strftime("%H:%M:%S"),
                elapsed_ms=int((time.time() - started_at) * 1000),
            ))

    async def _emit_progress(
        self,
        session_id: str,
        queue: asyncio.Queue,
        step: str,
        detail: str,
        status: str = "running",
    ) -> None:
        if self._is_session_canceled(session_id):
            return
        data = self._progress_payload(step, detail, status)
        self.session_store.update_status(session_id, "running", detail)
        self.session_store.add_event(session_id, "progress", data)
        await queue.put(make_event("progress", **data))

    async def _stop_canceled_before_start(self, session_id: str, queue: asyncio.Queue) -> None:
        """Close a prepared stream when a queued session was canceled before work began."""
        if queue.empty():
            await queue.put(make_event(
                "canceled",
                step="canceled",
                detail="Canceled by user.",
                status="canceled",
                at=datetime.now().strftime("%H:%M:%S"),
            ))
            await queue.put(None)
        self._queues.pop(session_id, None)
        self._handlers.pop(session_id, None)
        self._cancel_tokens.pop(session_id, None)

    def _make_thread_progress_callback(
        self,
        session_id: str,
        queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
    ):
        def _callback(step: str, detail: str, status: str = "running") -> None:
            try:
                if self._is_session_canceled(session_id):
                    return
                data = self._progress_payload(step, detail, status)
                self.session_store.update_status(session_id, "running", detail)
                self.session_store.add_event(session_id, "progress", data)
                loop.call_soon_threadsafe(queue.put_nowait, make_event("progress", **data))
            except Exception:
                pass

        return _callback

    async def cancel_session(self, session_id: str) -> bool:
        """Cancel a running or pending session.

        Cancellation is cooperative: an in-flight HTTP request cannot be killed
        safely from another thread, but the pipeline will stop at the next phase
        boundary and late results are ignored.
        """
        session = self.session_store.get_session(session_id)
        if not session or session.get("status") not in {"pending", "running"}:
            return False

        token = self._cancel_tokens.get(session_id)
        if token:
            token.set()

        handler = self._handlers.get(session_id)
        if handler:
            handler.provide_answer("skip")

        self.session_store.store_canceled(session_id)
        self.session_store.add_event(session_id, "canceled", {"message": "Canceled by user"})

        queue = self._queues.get(session_id)
        if queue:
            await queue.put(make_event(
                "canceled",
                step="canceled",
                detail="Canceled by user.",
                status="canceled",
                at=datetime.now().strftime("%H:%M:%S"),
            ))
            await queue.put(None)

        return True

    async def run_deliberate(self, session_id: str, question: str, mode: str = "council",
                              depth: str = None, length: str = None, rounds: int = 1,
                              no_interact: bool = False, chairman: str = None,
                              file_names: list[str] = None,
                              file_items: list[tuple[str, str | bytes]] = None,
                              research: str = "auto", output: str = "memo",
                              models: str = None) -> None:
        """Run a deliberation pipeline in a background thread with SSE events."""
        queue = self.prepare_queue(session_id)
        loop = asyncio.get_running_loop()
        cancel_token = threading.Event()
        self._cancel_tokens[session_id] = cancel_token
        heartbeat_task = None

        # Create interaction handler for web mode
        handler = None
        if not no_interact:
            handler = interaction.WebInteractionHandler(timeout=300)
            self._handlers[session_id] = handler

            # Connect handler to SSE queue so questions reach the browser.
            # ask_user() runs in the pipeline thread, so use call_soon_threadsafe
            # to push the event onto the asyncio Queue from that thread.
            def _on_questions(questions: list[str], context: str):
                event = make_event("needs_input", questions=questions, context=context)
                loop.call_soon_threadsafe(queue.put_nowait, event)
            handler.set_questions_callback(_on_questions)

        async with self._semaphore:
            if cancel_token.is_set() or self._is_session_canceled(session_id):
                await self._stop_canceled_before_start(session_id, queue)
                return

            self.session_store.update_status(session_id, "running", "starting")
            await self._emit_progress(session_id, queue, "starting", "Pipeline starting...")

            try:
                start_time = time.time()
                heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(session_id, queue, start_time, cancel_token)
                )

                if file_names:
                    await self._emit_progress(
                        session_id,
                        queue,
                        "files",
                        f"Context files: {', '.join(file_names)}",
                    )

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
                    models=models,
                    research=research,
                    output=output,
                    file_items=file_items,
                )

                # Run the sync pipeline in a thread
                await self._emit_progress(
                    session_id,
                    queue,
                    "dispatch",
                    "Preparing advisor dispatch...",
                )
                progress_callback = self._make_thread_progress_callback(session_id, queue, loop)

                result = await asyncio.to_thread(
                    orchestrate.run_deliberate,
                    args,
                    handler,
                    progress_callback=progress_callback,
                    cancel_token=cancel_token,
                )

                duration_ms = int((time.time() - start_time) * 1000)

                # Store results in session database
                if cancel_token.is_set() or self._is_session_canceled(session_id):
                    self.session_store.store_canceled(session_id)
                    self.session_store.add_event(session_id, "canceled", {
                        "duration_ms": duration_ms,
                    })
                    await queue.put(make_event(
                        "canceled",
                        step="canceled",
                        detail="Canceled by user.",
                        status="canceled",
                    ))
                elif result and isinstance(result, dict):
                    self.session_store.store_result(
                        session_id=session_id,
                        result_html=result.get("html", ""),
                        result_json={
                            "verdict": result.get("verdict", ""),
                            "metadata": result.get("metadata", {}),
                            "tension_map": result.get("tension_map", ""),
                            "co_constructions": result.get("co_constructions", []),
                            "html_path": result.get("html_path", ""),
                            "md_path": result.get("md_path", ""),
                            "log_path": result.get("log_path", ""),
                            "factcheck": result.get("factcheck"),
                            "factcheck_path": result.get("factcheck_path", ""),
                        },
                        duration_ms=duration_ms,
                    )
                else:
                    self.session_store.update_status(session_id, "complete")

                if not cancel_token.is_set() and not self._is_session_canceled(session_id):
                    self.session_store.add_event(session_id, "complete", {
                        "duration_ms": duration_ms,
                    })
                    completion_detail = f"Completed in {duration_ms/1000:.1f}s"
                    await queue.put(make_event(
                        "progress",
                        step="complete",
                        detail=completion_detail,
                        status="complete",
                    ))
                    await queue.put(make_event(
                        "complete",
                        step="complete",
                        detail=completion_detail,
                        status="complete",
                        duration_ms=duration_ms,
                    ))

            except orchestrate.PipelineCancelled as e:
                self.session_store.store_canceled(session_id)
                self.session_store.add_event(session_id, "canceled", {"message": str(e)})
                await queue.put(make_event(
                    "canceled",
                    step="canceled",
                    detail=str(e),
                    status="canceled",
                    at=datetime.now().strftime("%H:%M:%S"),
                ))
            except Exception as e:
                error_msg = str(e)
                if cancel_token.is_set() or self._is_session_canceled(session_id):
                    self.session_store.store_canceled(session_id)
                    await queue.put(make_event(
                        "canceled",
                        step="canceled",
                        detail="Canceled by user.",
                        status="canceled",
                    ))
                else:
                    self.session_store.store_error(session_id, error_msg)
                    await queue.put(make_event("error", message=error_msg))

            finally:
                cancel_token.set()
                if heartbeat_task:
                    heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat_task
                # Signal stream completion
                await queue.put(None)
                # Cleanup
                self._queues.pop(session_id, None)
                self._handlers.pop(session_id, None)
                self._cancel_tokens.pop(session_id, None)

    async def run_analyze(self, session_id: str, source: str, with_qa: bool = False,
                           qa_count: int = 10, lang: str = None, extract: bool = False,
                           length: str = None) -> None:
        """Run an analysis pipeline in a background thread."""
        queue = self.prepare_queue(session_id)
        cancel_token = threading.Event()
        self._cancel_tokens[session_id] = cancel_token
        heartbeat_task = None

        async with self._semaphore:
            if cancel_token.is_set() or self._is_session_canceled(session_id):
                await self._stop_canceled_before_start(session_id, queue)
                return

            self.session_store.update_status(session_id, "running", "starting")
            await self._emit_progress(session_id, queue, "starting", "Pipeline starting...")

            try:
                start_time = time.time()
                heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(session_id, queue, start_time, cancel_token)
                )

                args = Namespace(
                    source=source,
                    with_qa=with_qa,
                    qa_count=qa_count,
                    lang=lang,
                    compare=False,
                    extract=extract,
                )

                await self._emit_progress(session_id, queue, "ingesting", "Reading source...")
                progress_callback = self._make_thread_progress_callback(
                    session_id,
                    queue,
                    asyncio.get_running_loop(),
                )
                result = await asyncio.to_thread(
                    orchestrate.run_analyze,
                    args,
                    progress_callback=progress_callback,
                    cancel_token=cancel_token,
                )

                duration_ms = int((time.time() - start_time) * 1000)

                if cancel_token.is_set() or self._is_session_canceled(session_id):
                    self.session_store.store_canceled(session_id)
                    self.session_store.add_event(session_id, "canceled", {"duration_ms": duration_ms})
                    await queue.put(make_event(
                        "canceled",
                        step="canceled",
                        detail="Canceled by user.",
                        status="canceled",
                    ))
                elif result and isinstance(result, dict):
                    self.session_store.store_result(
                        session_id=session_id,
                        result_html=result.get("html", ""),
                        result_json={
                            "synthesis": result.get("synthesis", ""),
                            "html_path": result.get("html_path", ""),
                            "md_path": result.get("md_path", ""),
                        },
                        duration_ms=duration_ms,
                    )
                else:
                    self.session_store.update_status(session_id, "complete")

                if not cancel_token.is_set() and not self._is_session_canceled(session_id):
                    self.session_store.add_event(session_id, "complete", {"duration_ms": duration_ms})
                    completion_detail = f"Completed in {duration_ms/1000:.1f}s"
                    await queue.put(make_event(
                        "progress",
                        step="complete",
                        detail=completion_detail,
                        status="complete",
                    ))
                    await queue.put(make_event(
                        "complete",
                        step="complete",
                        detail=completion_detail,
                        status="complete",
                        duration_ms=duration_ms,
                    ))

            except orchestrate.PipelineCancelled as e:
                self.session_store.store_canceled(session_id)
                self.session_store.add_event(session_id, "canceled", {"message": str(e)})
                await queue.put(make_event(
                    "canceled",
                    step="canceled",
                    detail=str(e),
                    status="canceled",
                    at=datetime.now().strftime("%H:%M:%S"),
                ))
            except Exception as e:
                if cancel_token.is_set() or self._is_session_canceled(session_id):
                    self.session_store.store_canceled(session_id)
                    await queue.put(make_event(
                        "canceled",
                        step="canceled",
                        detail="Canceled by user.",
                        status="canceled",
                    ))
                else:
                    self.session_store.store_error(session_id, str(e))
                    await queue.put(make_event("error", message=str(e)))

            finally:
                cancel_token.set()
                if heartbeat_task:
                    heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat_task
                await queue.put(None)
                self._queues.pop(session_id, None)
                self._cancel_tokens.pop(session_id, None)

    async def provide_answer(self, session_id: str, answer: str) -> bool:
        """Provide user's answer for mid-pipeline interaction.

        Returns True if the answer was delivered to the pipeline.
        """
        handler = self._handlers.get(session_id)
        if handler:
            handler.provide_answer(answer)
            return True
        return False
