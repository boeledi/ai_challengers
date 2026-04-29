import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import llm_call  # noqa: E402


class DummyResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "stop_reason": "end_turn",
        }


def test_anthropic_call_does_not_send_temperature_for_thinking_level(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return DummyResponse()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(llm_call.requests, "post", fake_post)

    llm_call.call_anthropic(
        model_cfg={
            "model_id": "claude-opus-4-7",
            "endpoint": "https://api.anthropic.com/v1/messages",
            "api_key_env": "ANTHROPIC_API_KEY",
        },
        prompt="Question",
        system_prompt="System",
        max_tokens=100,
        thinking_level="medium",
        timeouts=(1, 1),
    )

    assert "temperature" not in captured["json"]


class OpenAIDummyResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [
                {
                    "message": {"content": "ok after retry"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        }


def test_call_model_retries_transient_ssl_connection_error(monkeypatch):
    attempts = {"count": 0}

    def fake_post(url, headers, json, timeout):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise llm_call.requests.exceptions.SSLError("sslv3 alert bad record mac")
        return OpenAIDummyResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(llm_call.requests, "post", fake_post)
    monkeypatch.setattr(llm_call.time, "sleep", lambda seconds: None)

    result = llm_call.call_model(
        config={
            "models": {
                "gpt-test": {
                    "provider": "openai_compat",
                    "model_id": "gpt-test",
                    "endpoint": "https://api.openai.com/v1/chat/completions",
                    "api_key_env": "OPENAI_API_KEY",
                    "max_tokens": 128,
                }
            },
            "token_budgets": {"advisor": 64},
            "timeouts": {"connect": 1, "read": 1},
        },
        model_key="gpt-test",
        role="advisor",
        prompt="Question",
    )

    assert result["error"] is None
    assert result["response"] == "ok after retry"
    assert attempts["count"] == 2
