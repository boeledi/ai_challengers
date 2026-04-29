import asyncio
import time

from fastapi.testclient import TestClient

import web.app
import web.pipeline_runner
from web.pipeline_runner import PipelineRunner
from web.session_store import SessionStore
from web.sse import event_stream, make_event


def test_deliberate_runner_reuses_prepared_sse_queue(monkeypatch, tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    runner = PipelineRunner(session_store=store)
    session_id = store.create_session(
        pipeline_type="deliberate",
        question="Should we launch?",
        mode="council",
    )

    prepared_queue = runner.prepare_queue(session_id)

    def fake_run_deliberate(args, interaction_handler=None, progress_callback=None, cancel_token=None):
        return {
            "html": "<p>report</p>",
            "verdict": "## Recommendation\nProceed.",
            "metadata": {},
            "html_path": "",
            "md_path": "",
        }

    monkeypatch.setattr("web.pipeline_runner.orchestrate.run_deliberate", fake_run_deliberate)

    asyncio.run(
        runner.run_deliberate(
            session_id=session_id,
            question="Should we launch?",
            no_interact=True,
            research="off",
        )
    )

    assert not prepared_queue.empty()


def test_deliberate_runner_persists_detailed_progress_events(monkeypatch, tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    runner = PipelineRunner(session_store=store)
    session_id = store.create_session(
        pipeline_type="deliberate",
        question="Should we launch?",
        mode="council",
    )

    def fake_run_deliberate(args, interaction_handler=None, progress_callback=None, cancel_token=None):
        if progress_callback:
            progress_callback("framing", "Framing the decision context...", "running")
        return {
            "html": "<p>report</p>",
            "verdict": "## Recommendation\nProceed.",
            "metadata": {},
            "html_path": "",
            "md_path": "",
        }

    monkeypatch.setattr("web.pipeline_runner.orchestrate.run_deliberate", fake_run_deliberate)

    asyncio.run(
        runner.run_deliberate(
            session_id=session_id,
            question="Should we launch?",
            no_interact=True,
            research="off",
        )
    )

    events = store.get_events(session_id)
    assert any(
        event["event_type"] == "progress"
        and event["data"]["step"] == "framing"
        and event["data"]["detail"] == "Framing the decision context..."
        for event in events
    )


def test_sse_returns_stored_error_for_failed_session(monkeypatch, tmp_path):
    db_path = tmp_path / "sessions.db"
    config = {
        "web": {"db_path": str(db_path)},
        "models": {},
    }

    monkeypatch.setattr(web.app.llm_call, "load_env", lambda: None)
    monkeypatch.setattr(web.app.llm_call, "load_config", lambda: config)
    monkeypatch.setattr(web.app.llm_call, "check_models", lambda cfg: {"available": [], "unavailable": []})

    store = SessionStore(db_path=str(db_path))
    session_id = store.create_session(
        pipeline_type="deliberate",
        question="Should we launch?",
        mode="council",
    )
    store.store_error(session_id, "boom")

    client = TestClient(web.app.create_app())
    response = client.get(f"/deliberate/{session_id}/events")

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "boom" in response.text
    assert "Session stream not available" not in response.text


def test_deliberate_progress_uses_structured_timeline_and_clarification_layout(monkeypatch, tmp_path):
    db_path = tmp_path / "sessions.db"
    config = {
        "web": {"db_path": str(db_path)},
        "models": {},
    }

    monkeypatch.setattr(web.app.llm_call, "load_env", lambda: None)
    monkeypatch.setattr(web.app.llm_call, "load_config", lambda: config)
    monkeypatch.setattr(web.app.llm_call, "check_models", lambda cfg: {"available": [], "unavailable": []})

    store = SessionStore(db_path=str(db_path))
    session_id = store.create_session(
        pipeline_type="deliberate",
        question="Should we publish?",
        mode="council",
    )
    store.update_status(session_id, "running", "factcheck")
    store.add_event(
        session_id,
        "progress",
        {
            "step": "factcheck",
            "detail": "Fact-check audit completed for 45 claim(s).",
            "status": "ok",
            "at": "12:40:43",
        },
    )

    client = TestClient(web.app.create_app())
    response = client.get(f"/deliberate/{session_id}")

    assert response.status_code == 200
    assert "run-status-panel" in response.text
    assert "activity-spinner" in response.text
    assert "cancel-session-btn" in response.text
    assert "cancelSession" in response.text
    assert "progress-shell" in response.text
    assert "event-badge" in response.text
    assert "event-detail-text" in response.text
    assert "question-list" in response.text
    assert "interaction-actions" in response.text
    assert "escapeHtml" in response.text
    assert "htmx.process(area)" in response.text
    assert 'method="post"' in response.text
    assert 'action="/deliberate/' in response.text
    assert "skipInteraction" in response.text


def test_analyze_progress_exposes_activity_panel_and_cancel(monkeypatch, tmp_path):
    db_path = tmp_path / "sessions.db"
    config = {
        "web": {"db_path": str(db_path)},
        "models": {},
    }

    monkeypatch.setattr(web.app.llm_call, "load_env", lambda: None)
    monkeypatch.setattr(web.app.llm_call, "load_config", lambda: config)
    monkeypatch.setattr(web.app.llm_call, "check_models", lambda cfg: {"available": [], "unavailable": []})

    store = SessionStore(db_path=str(db_path))
    session_id = store.create_session(
        pipeline_type="analyze",
        question="https://example.com/article",
        mode="analyze",
    )
    store.update_status(session_id, "running", "Reading source...")

    client = TestClient(web.app.create_app())
    response = client.get(f"/analyze/{session_id}")

    assert response.status_code == 200
    assert "run-status-panel" in response.text
    assert "activity-spinner" in response.text
    assert "cancel-session-btn" in response.text
    assert "cancelSession" in response.text
    assert 'sse-swap="heartbeat"' in response.text
    assert 'sse-swap="canceled"' in response.text


def test_runner_emits_heartbeat_while_pipeline_is_busy(monkeypatch, tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    runner = PipelineRunner(session_store=store, heartbeat_interval=0.01)
    session_id = store.create_session(
        pipeline_type="deliberate",
        question="Should we launch?",
        mode="council",
    )
    queue = runner.prepare_queue(session_id)

    def fake_run_deliberate(args, interaction_handler=None, progress_callback=None, cancel_token=None):
        time.sleep(0.05)
        return {
            "html": "<p>report</p>",
            "verdict": "## Recommendation\nProceed.",
            "metadata": {},
            "html_path": "",
            "md_path": "",
        }

    monkeypatch.setattr("web.pipeline_runner.orchestrate.run_deliberate", fake_run_deliberate)

    asyncio.run(
        runner.run_deliberate(
            session_id=session_id,
            question="Should we launch?",
            no_interact=True,
            research="off",
        )
    )

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    assert any(event and event.get("type") == "heartbeat" for event in events)


def test_runner_emits_complete_event_when_pipeline_finishes(monkeypatch, tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    runner = PipelineRunner(session_store=store, heartbeat_interval=0.01)
    session_id = store.create_session(
        pipeline_type="deliberate",
        question="Should we launch?",
        mode="council",
    )
    queue = runner.prepare_queue(session_id)

    def fake_run_deliberate(args, interaction_handler=None, progress_callback=None, cancel_token=None):
        return {
            "html": "<p>report</p>",
            "verdict": "## Recommendation\nProceed.",
            "metadata": {},
            "html_path": "",
            "md_path": "",
        }

    monkeypatch.setattr("web.pipeline_runner.orchestrate.run_deliberate", fake_run_deliberate)

    asyncio.run(
        runner.run_deliberate(
            session_id=session_id,
            question="Should we launch?",
            no_interact=True,
            research="off",
        )
    )

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    assert any(event and event.get("type") == "complete" for event in events)


def test_event_stream_does_not_turn_canceled_close_into_complete():
    async def collect_events():
        queue = asyncio.Queue()
        await queue.put(make_event("canceled", detail="Canceled by user."))
        await queue.put(None)
        chunks = []
        async for chunk in event_stream(queue):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect_events())

    assert any("event: canceled" in chunk for chunk in chunks)
    assert not any("event: complete" in chunk for chunk in chunks)


def test_runner_cancel_marks_session_canceled_and_avoids_late_result(monkeypatch, tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    runner = PipelineRunner(session_store=store, heartbeat_interval=0.01)
    session_id = store.create_session(
        pipeline_type="deliberate",
        question="Should we launch?",
        mode="council",
    )

    async def scenario():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()

        def fake_run_deliberate(args, interaction_handler=None, progress_callback=None, cancel_token=None):
            loop.call_soon_threadsafe(started.set)
            while not cancel_token.is_set():
                time.sleep(0.01)
            raise web.pipeline_runner.orchestrate.PipelineCancelled("Canceled")

        monkeypatch.setattr("web.pipeline_runner.orchestrate.run_deliberate", fake_run_deliberate)

        task = asyncio.create_task(
            runner.run_deliberate(
                session_id=session_id,
                question="Should we launch?",
                no_interact=True,
                research="off",
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        canceled = await runner.cancel_session(session_id)
        await asyncio.wait_for(task, timeout=1)
        return canceled

    assert asyncio.run(scenario())

    session = store.get_session(session_id)
    assert session["status"] == "canceled"
    assert not session.get("result_html")
    assert any(event["event_type"] == "canceled" for event in store.get_events(session_id))


def test_runner_does_not_start_pipeline_after_pending_session_was_canceled(monkeypatch, tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    runner = PipelineRunner(session_store=store, heartbeat_interval=0.01)
    session_id = store.create_session(
        pipeline_type="deliberate",
        question="Should we launch?",
        mode="council",
    )
    runner.prepare_queue(session_id)
    called = False

    def fake_run_deliberate(args, interaction_handler=None, progress_callback=None, cancel_token=None):
        nonlocal called
        called = True
        return {"html": "<p>late</p>", "verdict": "late"}

    monkeypatch.setattr("web.pipeline_runner.orchestrate.run_deliberate", fake_run_deliberate)

    async def scenario():
        canceled = await runner.cancel_session(session_id)
        await runner.run_deliberate(
            session_id=session_id,
            question="Should we launch?",
            no_interact=True,
            research="off",
        )
        return canceled

    assert asyncio.run(scenario())
    assert not called
    assert runner.get_queue(session_id) is None
    assert store.get_session(session_id)["status"] == "canceled"


def test_cancel_endpoint_marks_running_session_canceled(monkeypatch, tmp_path):
    db_path = tmp_path / "sessions.db"
    config = {
        "web": {"db_path": str(db_path)},
        "models": {},
    }

    monkeypatch.setattr(web.app.llm_call, "load_env", lambda: None)
    monkeypatch.setattr(web.app.llm_call, "load_config", lambda: config)
    monkeypatch.setattr(web.app.llm_call, "check_models", lambda cfg: {"available": [], "unavailable": []})

    store = SessionStore(db_path=str(db_path))
    session_id = store.create_session(
        pipeline_type="deliberate",
        question="Should we launch?",
        mode="council",
    )
    store.update_status(session_id, "running", "advisor call")

    client = TestClient(web.app.create_app())
    response = client.post(f"/sessions/{session_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "canceled"
    assert store.get_session(session_id)["status"] == "canceled"


def test_interaction_answer_post_records_submission_event(monkeypatch, tmp_path):
    db_path = tmp_path / "sessions.db"
    config = {
        "web": {"db_path": str(db_path)},
        "models": {},
    }

    class FakeRunner:
        def __init__(self, session_store):
            self.session_store = session_store

        async def provide_answer(self, session_id, answer):
            return True

    monkeypatch.setattr(web.app.llm_call, "load_env", lambda: None)
    monkeypatch.setattr(web.app.llm_call, "load_config", lambda: config)
    monkeypatch.setattr(web.app.llm_call, "check_models", lambda cfg: {"available": [], "unavailable": []})
    monkeypatch.setattr(web.app, "PipelineRunner", FakeRunner)

    store = SessionStore(db_path=str(db_path))
    session_id = store.create_session(
        pipeline_type="deliberate",
        question="Should we launch?",
        mode="council",
    )
    store.update_status(session_id, "running", "waiting for answer")

    client = TestClient(web.app.create_app())
    response = client.post(f"/deliberate/{session_id}/answer", data={"answer": "Useful context"})

    assert response.status_code == 200
    assert "Answer submitted" in response.text
    events = store.get_events(session_id)
    assert any(
        event["event_type"] == "progress"
        and event["data"]["step"] == "interaction"
        and "Answer submitted (14 chars)." == event["data"]["detail"]
        for event in events
    )


def test_deliberate_get_answer_fallback_delivers_and_redirects(monkeypatch, tmp_path):
    db_path = tmp_path / "sessions.db"
    config = {
        "web": {"db_path": str(db_path)},
        "models": {},
    }

    class FakeRunner:
        def __init__(self, session_store):
            self.session_store = session_store

        async def provide_answer(self, session_id, answer):
            return True

    monkeypatch.setattr(web.app.llm_call, "load_env", lambda: None)
    monkeypatch.setattr(web.app.llm_call, "load_config", lambda: config)
    monkeypatch.setattr(web.app.llm_call, "check_models", lambda cfg: {"available": [], "unavailable": []})
    monkeypatch.setattr(web.app, "PipelineRunner", FakeRunner)

    store = SessionStore(db_path=str(db_path))
    session_id = store.create_session(
        pipeline_type="deliberate",
        question="Should we launch?",
        mode="council",
    )
    store.update_status(session_id, "running", "waiting for answer")

    client = TestClient(web.app.create_app())
    response = client.get(
        f"/deliberate/{session_id}",
        params={"answer": "Useful context"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/deliberate/{session_id}"
    events = store.get_events(session_id)
    assert any(
        event["event_type"] == "progress"
        and event["data"]["step"] == "interaction"
        and "Answer submitted (14 chars)." == event["data"]["detail"]
        for event in events
    )


def test_session_report_downloads(monkeypatch, tmp_path):
    db_path = tmp_path / "sessions.db"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    html_path = output_dir / "deliberate-report-test.html"
    html_path.write_text("<html><body>Full report</body></html>", encoding="utf-8")
    factcheck_path = output_dir / "factcheck-test.md"
    factcheck_path.write_text("# Fact-Check Audit\n\nSupported claim.", encoding="utf-8")

    config = {
        "web": {"db_path": str(db_path)},
        "models": {},
    }

    monkeypatch.setattr(web.app.llm_call, "load_env", lambda: None)
    monkeypatch.setattr(web.app.llm_call, "load_config", lambda: config)
    monkeypatch.setattr(web.app.llm_call, "check_models", lambda cfg: {"available": [], "unavailable": []})
    monkeypatch.setattr(web.app.llm_call, "find_project_root", lambda: tmp_path)

    store = SessionStore(db_path=str(db_path))
    session_id = store.create_session(
        pipeline_type="deliberate",
        question="Should we launch?",
        mode="council",
    )
    store.store_result(
        session_id=session_id,
        result_html="<p>Stored report</p>",
        result_json={
            "verdict": "## Recommendation\nProceed.",
            "html_path": str(html_path),
            "factcheck_path": str(factcheck_path),
        },
    )

    client = TestClient(web.app.create_app())

    memo_response = client.get(f"/sessions/{session_id}/download/memo")
    assert memo_response.status_code == 200
    assert "Proceed." in memo_response.text
    assert "attachment" in memo_response.headers["content-disposition"]

    html_response = client.get(f"/sessions/{session_id}/download/html")
    assert html_response.status_code == 200
    assert "Full report" in html_response.text
    assert "attachment" in html_response.headers["content-disposition"]

    factcheck_response = client.get(f"/sessions/{session_id}/download/factcheck")
    assert factcheck_response.status_code == 200
    assert "Supported claim." in factcheck_response.text
    assert "attachment" in factcheck_response.headers["content-disposition"]
