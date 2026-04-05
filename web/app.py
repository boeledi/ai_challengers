"""AI Provocateurs — FastAPI Web Application."""

import asyncio
import os
import sys
from pathlib import Path

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Add scripts to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import llm_call

from web.models import DeliberateRequest, AnalyzeRequest, InteractionAnswer, ConfigKeyUpdate
from web.session_store import SessionStore
from web.pipeline_runner import PipelineRunner
from web.sse import event_stream


def create_app() -> FastAPI:
    """Application factory."""

    # Load config
    llm_call.load_env()
    config = llm_call.load_config()
    web_config = config.get("web", {})
    db_path = web_config.get("db_path", "data/sessions.db")

    app = FastAPI(title="AI Provocateurs", version="1.0.0")

    # Static files and templates
    static_dir = Path(__file__).parent / "static"
    templates_dir = Path(__file__).parent / "templates"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    templates = Jinja2Templates(directory=str(templates_dir))

    # Services
    store = SessionStore(db_path=db_path)
    runner = PipelineRunner(session_store=store)

    # =========================================================================
    # Pages
    # =========================================================================

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Landing page with deliberation form."""
        # Check available models
        config = llm_call.load_config()
        check = llm_call.check_models(config)
        available = [m["model"] for m in check["available"]]
        return templates.TemplateResponse("index.html", {
            "request": request,
            "available_models": available,
            "modes": ["council", "compass", "raw", "redteam", "premortem",
                      "steelman", "advocate", "forecast", "collaborative"],
            "depths": ["quick", "basic", "stress", "deep", "ultra"],
            "lengths": ["concise", "standard", "detailed", "comprehensive"],
        })

    @app.get("/sessions", response_class=HTMLResponse)
    async def sessions_page(request: Request):
        """Session history page."""
        sessions = store.list_sessions(limit=50)
        return templates.TemplateResponse("sessions.html", {
            "request": request,
            "sessions": sessions,
        })

    @app.get("/sessions/{session_id}", response_class=HTMLResponse)
    async def session_detail(request: Request, session_id: str):
        """View a past session."""
        session = store.get_session(session_id)
        if not session:
            return HTMLResponse("<h1>Session not found</h1>", status_code=404)
        events = store.get_events(session_id)
        return templates.TemplateResponse("session.html", {
            "request": request,
            "session": session,
            "events": events,
        })

    @app.get("/config", response_class=HTMLResponse)
    async def config_page(request: Request):
        """Configuration page."""
        config = llm_call.load_config()
        check = llm_call.check_models(config)
        models_status = {}
        for m in check["available"]:
            models_status[m["model"]] = {"status": "ready", "provider": m["provider"]}
        for m in check["unavailable"]:
            models_status[m["model"]] = {
                "status": "no_key", "provider": m["provider"],
                "env_var": m.get("env_var", ""),
            }
        return templates.TemplateResponse("config.html", {
            "request": request,
            "models_status": models_status,
        })

    @app.get("/deliberate/{session_id}", response_class=HTMLResponse)
    async def deliberate_progress(request: Request, session_id: str):
        """Active deliberation progress page."""
        session = store.get_session(session_id)
        if not session:
            return HTMLResponse("<h1>Session not found</h1>", status_code=404)
        return templates.TemplateResponse("deliberate.html", {
            "request": request,
            "session": session,
            "session_id": session_id,
        })

    @app.get("/analyze/{session_id}", response_class=HTMLResponse)
    async def analyze_progress(request: Request, session_id: str):
        """Active analysis progress page."""
        session = store.get_session(session_id)
        if not session:
            return HTMLResponse("<h1>Session not found</h1>", status_code=404)
        return templates.TemplateResponse("analyze.html", {
            "request": request,
            "session": session,
            "session_id": session_id,
        })

    # =========================================================================
    # Actions
    # =========================================================================

    @app.post("/deliberate")
    async def start_deliberate(request: Request, background_tasks: BackgroundTasks):
        """Start a new deliberation."""
        form = await request.form()
        question = form.get("question", "").strip()
        mode = form.get("mode", "council")
        depth = form.get("depth") or None
        length = form.get("length") or None
        rounds = int(form.get("rounds", 1))
        no_interact = form.get("no_interact") == "on"

        if not question:
            return RedirectResponse("/", status_code=303)

        # Read uploaded context files
        file_contents = []
        uploaded_files = form.getlist("files")
        for upload in uploaded_files:
            if hasattr(upload, "filename") and upload.filename:
                try:
                    raw = await upload.read()
                    text = raw.decode("utf-8", errors="replace")
                    if text.strip():
                        file_contents.append((upload.filename, text))
                except Exception:
                    pass  # Skip unreadable files

        # Append file contents to the question as context
        enriched_question = question
        if file_contents:
            context_parts = ["\n\n--- ATTACHED FILES ---\n"]
            for filename, content in file_contents:
                # Truncate very large files to prevent token explosion
                if len(content) > 50000:
                    content = content[:50000] + f"\n\n[... truncated, {len(content)} chars total ...]"
                context_parts.append(f"\n### File: {filename}\n\n{content}\n")
            enriched_question = question + "".join(context_parts)

        file_names = [f for f, _ in file_contents] if file_contents else []

        session_id = store.create_session(
            pipeline_type="deliberate",
            question=question,  # Store original question for display
            mode=mode,
            depth=depth or "basic",
            length=length or "standard",
        )

        background_tasks.add_task(
            runner.run_deliberate,
            session_id=session_id,
            question=enriched_question,  # Pass enriched question with file contents
            mode=mode,
            depth=depth,
            length=length,
            rounds=rounds,
            no_interact=no_interact,
            file_names=file_names,
        )

        return RedirectResponse(f"/deliberate/{session_id}", status_code=303)

    @app.post("/analyze")
    async def start_analyze(request: Request, background_tasks: BackgroundTasks):
        """Start a new analysis."""
        form = await request.form()
        source = form.get("source", "").strip()
        with_qa = form.get("with_qa") == "on"
        qa_count = int(form.get("qa_count", 10))
        lang = form.get("lang") or None
        extract = form.get("extract") == "on"
        length = form.get("length") or None

        if not source:
            return RedirectResponse("/", status_code=303)

        session_id = store.create_session(
            pipeline_type="analyze",
            question=source,
            mode="analyze",
            length=length or "standard",
        )

        background_tasks.add_task(
            runner.run_analyze,
            session_id=session_id,
            source=source,
            with_qa=with_qa,
            qa_count=qa_count,
            lang=lang,
            extract=extract,
            length=length,
        )

        return RedirectResponse(f"/analyze/{session_id}", status_code=303)

    @app.post("/deliberate/{session_id}/answer")
    async def submit_answer(session_id: str, request: Request):
        """Submit user answer for mid-pipeline interaction."""
        form = await request.form()
        answer = form.get("answer", "").strip()
        if answer:
            delivered = await runner.provide_answer(session_id, answer)
            if delivered:
                return HTMLResponse(
                    '<div class="event-item success">Answer submitted. Pipeline resuming...</div>'
                )
        return HTMLResponse(
            '<div class="event-item">No active interaction for this session.</div>'
        )

    # =========================================================================
    # SSE Endpoints
    # =========================================================================

    @app.get("/deliberate/{session_id}/events")
    async def deliberate_events(session_id: str):
        """SSE endpoint for deliberation progress."""
        queue = runner.get_queue(session_id)
        if not queue:
            # Session may have already completed
            session = store.get_session(session_id)
            if session and session["status"] == "complete":
                async def completed():
                    yield f"event: complete\ndata: {{\"status\": \"done\"}}\n\n"
                return StreamingResponse(completed(), media_type="text/event-stream")
            return StreamingResponse(
                iter([f"event: error\ndata: {{\"message\": \"Session not found\"}}\n\n"]),
                media_type="text/event-stream",
            )
        return StreamingResponse(event_stream(queue), media_type="text/event-stream")

    @app.get("/analyze/{session_id}/events")
    async def analyze_events(session_id: str):
        """SSE endpoint for analysis progress."""
        return await deliberate_events(session_id)  # Same logic

    # =========================================================================
    # API Endpoints
    # =========================================================================

    @app.get("/api/models/check")
    async def check_models():
        """Check available models."""
        config = llm_call.load_config()
        return llm_call.check_models(config)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.post("/config/keys")
    async def update_key(request: Request):
        """Update an API key in .env file (write-only, never displays values)."""
        form = await request.form()
        key_name = form.get("key_name", "").strip()
        key_value = form.get("key_value", "").strip()

        if not key_name or not key_value:
            return RedirectResponse("/config", status_code=303)

        # Update .env file
        root = llm_call.find_project_root()
        env_path = root / ".env"
        lines = []
        found = False
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith(f"{key_name}="):
                    lines.append(f"{key_name}={key_value}")
                    found = True
                else:
                    lines.append(line)
        if not found:
            lines.append(f"{key_name}={key_value}")

        env_path.write_text("\n".join(lines) + "\n")

        # Reload environment
        os.environ[key_name] = key_value

        return RedirectResponse("/config", status_code=303)

    return app
