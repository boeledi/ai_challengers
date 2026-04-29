from argparse import Namespace
import json
from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import orchestrate  # noqa: E402


def test_peer_review_prompt_rewards_synergy_not_adversarial_ranking():
    prompt = orchestrate.build_peer_review_prompt(
        "Should we launch this offer?",
        {"A": "Launch narrowly.", "B": "Validate willingness to pay."},
    )

    assert "Best synergy" in prompt
    assert "Agreement quality" in prompt
    assert "Decision crux" in prompt
    assert "Most dangerous response" not in prompt
    assert "Suspicious agreement" not in prompt


def test_chairman_prompt_starts_with_decision_memo_sections():
    prompt = orchestrate.build_chairman_prompt(
        "council",
        "Should we launch this offer?",
        [{"persona": "Builder", "response": "Launch a paid pilot.", "model": "claude-opus"}],
        [{"model": "gpt-5-5", "response": "Combine pilot with a price test."}],
        tension_map="The core tension is speed versus evidence.",
        co_constructions=[{"persona": "Integrator", "response": "Paid pilot plus clear kill criteria."}],
    )

    expected_sections = [
        "## Recommendation",
        "## Key Insights",
        "## Options",
        "## Arguments For/Against",
        "## Decision Cruxes",
        "## Missing Information",
        "## Confidence",
        "## Next Step",
    ]
    for section in expected_sections:
        assert section in prompt

    assert prompt.index("## Recommendation") < prompt.index("## Key Insights")
    assert "Where the Board Agrees" not in prompt


def test_chairman_prompt_includes_adapted_version_when_user_requested_rewrite():
    prompt = orchestrate.build_chairman_prompt(
        "council",
        "Il faudrait vÃ©rifier toutes les donnÃ©es. A la fin, fournis-moi une version adaptÃ©e si nÃ©cessaire.",
        [{"persona": "Validator", "response": "Some claims are unsupported.", "model": "gpt-4-1"}],
        [],
        tension_map="Fact-check first, then rewrite conservatively.",
    )

    assert "## Adapted Version" in prompt
    assert "Do not merely recommend that the user rewrite it" in prompt


def test_context_pack_sanitizes_html_and_skips_pdf_until_supported():
    html = """
    <html><head><style>.x{color:red}</style><script>alert('x')</script></head>
    <body><h1>Useful title</h1><p onclick="steal()">Useful body</p></body></html>
    """

    pack = orchestrate.build_context_pack(
        "Question",
        file_items=[("notes.html", html), ("brief.pdf", b"%PDF-1.4 fake")],
    )

    assert "Useful title" in pack.context_text
    assert "Useful body" in pack.context_text
    assert "alert" not in pack.context_text
    assert "color:red" not in pack.context_text
    assert "onclick" not in pack.context_text
    assert pack.skipped_files == [{"name": "brief.pdf", "reason": "PDF parsing is not supported yet"}]


def test_research_auto_triggers_only_for_time_sensitive_questions():
    config = {"research": {"auto_triggers": ["latest", "2026", "market", "law", "pricing"]}}

    assert orchestrate.should_run_research(
        "What are the latest EU AI Act obligations in 2026?",
        "auto",
        config,
    )
    assert not orchestrate.should_run_research(
        "Should we rename this internal helper?",
        "auto",
        config,
    )
    assert orchestrate.should_run_research("anything", "on", config)
    assert not orchestrate.should_run_research("latest anything", "off", config)


def test_research_auto_handles_yaml_numeric_triggers():
    config = {"research": {"auto_triggers": ["latest", 2026, "market"]}}

    assert orchestrate.should_run_research(
        "What changed in the AI market in 2026?",
        "auto",
        config,
    )


def test_auto_factcheck_triggers_for_explicit_verification_request():
    config = {
        "research": {
            "auto_triggers": ["latest"],
            "factcheck_triggers": ["vérifier", "données", "sources", "fact-check"],
        }
    }

    mode = orchestrate.resolve_research_mode(
        "Il faudrait vérifier toutes les données et les sources avant publication.",
        "auto",
        config,
    )

    assert mode == "factcheck"
    assert orchestrate.should_run_research(
        "Il faudrait vérifier toutes les données et les sources avant publication.",
        "auto",
        config,
    )


def test_factcheck_extracts_reference_links_and_claims():
    text = """
    The benchmark improved from 12% to 66% in 2026. ([Stanford HAI][1])

    [1]: https://example.com/ai-index "AI Index"
    """

    links = orchestrate.extract_reference_links(text)
    claims = orchestrate.extract_checkable_claims(text, links, max_claims=10)

    assert links["1"]["url"] == "https://example.com/ai-index"
    assert links["1"]["title"] == "AI Index"
    assert len(claims) == 1
    assert claims[0].refs == ["1"]
    assert claims[0].urls == ["https://example.com/ai-index"]


def test_factcheck_pack_fetches_sources_and_uses_configured_light_model(monkeypatch, tmp_path):
    config = {
        "models": {
            "gpt-4-1": {"provider": "openai_compat", "model_id": "gpt-4.1", "api_key_env": "OPENAI_API_KEY"}
        },
        "research": {
            "factcheck_model": "gpt-4-1",
            "max_factcheck_claims": 5,
            "max_factcheck_sources": 5,
        },
    }
    pack = orchestrate.ContextPack(
        question="Please fact-check this.",
        context_text=(
            "The benchmark improved from 12% to 66% in 2026. ([Stanford HAI][1])\n\n"
            "[1]: https://example.com/ai-index \"AI Index\""
        ),
    )
    calls = []

    def fake_fetch(url, timeout=20):
        return orchestrate.FactCheckSource(
            ref="1",
            label="AI Index",
            url=url,
            final_url=url,
            status_code=200,
            title="AI Index",
            content="The benchmark improved from 12% to 66% in 2026.",
        )

    def fake_call(config, model_key, role, prompt, **kwargs):
        calls.append((model_key, role, prompt))
        return {
            "response": (
                '{"findings":[{"claim_id":"C1","verdict":"supported",'
                '"evidence":"The source states 12% to 66%.",'
                '"issue":"","correction":"","confidence":"high"}]}'
            ),
            "error": None,
        }

    monkeypatch.setattr(orchestrate, "fetch_factcheck_source", fake_fetch)
    monkeypatch.setattr(orchestrate.llm_call, "call_model", fake_call)
    monkeypatch.setattr(orchestrate.llm_call, "find_project_root", lambda: tmp_path)

    factcheck = orchestrate.build_factcheck_pack(
        context_pack=pack,
        research_mode="factcheck",
        config=config,
        available_models=["gpt-4-1"],
    )

    assert calls and calls[0][0] == "gpt-4-1"
    assert calls[0][1] == "fact_checker"
    assert factcheck.findings[0].verdict == "supported"
    assert "supported" in factcheck.to_markdown()


def test_factcheck_verifies_claims_in_batches(monkeypatch, tmp_path):
    config = {
        "models": {
            "gpt-4-1": {"provider": "openai_compat", "model_id": "gpt-4.1", "api_key_env": "OPENAI_API_KEY"}
        },
        "research": {
            "factcheck_model": "gpt-4-1",
            "max_factcheck_claims": 5,
            "max_factcheck_sources": 5,
            "factcheck_claims_per_call": 1,
        },
    }
    pack = orchestrate.ContextPack(
        question="Please fact-check this.",
        context_text=(
            "Metric A improved to 66% in 2026. ([Source A][1])\n\n"
            "Metric B fell to 12% in 2026. ([Source B][2])\n\n"
            "[1]: https://example.com/a \"Source A\"\n"
            "[2]: https://example.com/b \"Source B\""
        ),
    )
    calls = []

    def fake_fetch(url, timeout=20):
        return orchestrate.FactCheckSource(
            url=url,
            final_url=url,
            status_code=200,
            title=url.rsplit("/", 1)[-1],
            content="Metric A improved to 66% in 2026. Metric B increased to 20% in 2026.",
        )

    def fake_call(config, model_key, role, prompt, **kwargs):
        calls.append(prompt)
        payload = json.loads(prompt.split("FACTCHECK_PAYLOAD:", 1)[1])
        incoming_claim_id = payload["claims"][0]["claim_id"]
        if incoming_claim_id == "C1":
            claim_id = "C1"
            verdict = "supported"
            issue = ""
            correction = ""
        else:
            claim_id = "C2"
            verdict = "contradicted"
            issue = "Source says Metric B increased to 20%, not fell to 12%."
            correction = "Metric B increased to 20% in 2026."
        return {
            "response": (
                f'{{"findings":[{{"claim_id":"{claim_id}","verdict":"{verdict}",'
                f'"evidence":"Source excerpt checked.","issue":"{issue}",'
                f'"correction":"{correction}","confidence":"high"}}]}}'
            ),
            "error": None,
        }

    monkeypatch.setattr(orchestrate, "fetch_factcheck_source", fake_fetch)
    monkeypatch.setattr(orchestrate.llm_call, "call_model", fake_call)
    monkeypatch.setattr(orchestrate.llm_call, "find_project_root", lambda: tmp_path)

    factcheck = orchestrate.build_factcheck_pack(
        context_pack=pack,
        research_mode="factcheck",
        config=config,
        available_models=["gpt-4-1"],
    )

    assert len(calls) == 2
    assert [finding.verdict for finding in factcheck.findings] == ["supported", "contradicted"]


def test_factcheck_does_not_report_retrieved_source_as_validated_when_model_omits_claim(monkeypatch, tmp_path):
    config = {
        "models": {
            "gpt-4-1": {"provider": "openai_compat", "model_id": "gpt-4.1", "api_key_env": "OPENAI_API_KEY"}
        },
        "research": {
            "factcheck_model": "gpt-4-1",
            "max_factcheck_claims": 5,
            "max_factcheck_sources": 5,
        },
    }
    pack = orchestrate.ContextPack(
        question="Please fact-check this.",
        context_text=(
            "The benchmark improved from 12% to 66% in 2026. ([Stanford HAI][1])\n\n"
            "[1]: https://example.com/ai-index \"AI Index\""
        ),
    )

    def fake_fetch(url, timeout=20):
        return orchestrate.FactCheckSource(
            url=url,
            final_url=url,
            status_code=200,
            title="AI Index",
            content="The benchmark improved from 12% to 66% in 2026.",
        )

    monkeypatch.setattr(orchestrate, "fetch_factcheck_source", fake_fetch)
    monkeypatch.setattr(orchestrate.llm_call, "call_model", lambda **kwargs: {"response": '{"findings":[]}'})
    monkeypatch.setattr(orchestrate.llm_call, "find_project_root", lambda: tmp_path)

    factcheck = orchestrate.build_factcheck_pack(
        context_pack=pack,
        research_mode="factcheck",
        config=config,
        available_models=["gpt-4-1"],
    )

    assert factcheck.findings[0].verdict == "needs_semantic_check"
    assert "not validated" in factcheck.findings[0].issue


def test_model_selection_honors_explicit_models_and_real_config_keys():
    config = {
        "defaults": {
            "deliberate": {
                "preferred_models": ["claude-opus", "gpt-5-5", "gpt-4-1", "claude-sonnet"],
                "chairman": "claude-opus",
            }
        }
    }
    available = ["claude-opus", "gpt-5-5", "gpt-4-1", "claude-sonnet"]

    selected, chairman, warnings = orchestrate.select_deliberation_models(
        config=config,
        available=available,
        requested_models="gpt-5-5,claude-opus",
        max_advisors=5,
        requested_chairman=None,
    )

    assert selected == ["gpt-5-5", "claude-opus"]
    assert chairman == "claude-opus"
    assert warnings == []


def test_project_config_default_model_references_exist():
    config = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
    model_keys = set(config["models"])

    preferred = config["defaults"]["deliberate"]["preferred_models"]
    assert preferred
    assert all(model in model_keys for model in preferred)
    assert config["defaults"]["deliberate"]["chairman"] in model_keys
    assert config["defaults"]["analyze"]["roles"]["reviewer"] in model_keys
    assert "tension_mapper" in config["token_budgets"]
    assert "co_construction" in config["token_budgets"]
    assert "quality_rubric" in config


def test_run_deliberate_uses_constructive_phase_order_and_custom_models(monkeypatch, tmp_path):
    config = {
        "models": {
            "claude-opus": {"provider": "anthropic", "model_id": "claude", "api_key_env": "A"},
            "gpt-5-5": {"provider": "openai_compat", "model_id": "gpt-5.5", "api_key_env": "O"},
        },
        "defaults": {
            "deliberate": {
                "preferred_models": ["claude-opus", "gpt-5-5"],
                "chairman": "claude-opus",
                "default_mode": "council",
            }
        },
        "response_profiles": {
            "depth": {"basic": {"rounds": 1, "max_advisors": 5, "peer_review": True, "base_word_range": [150, 300]}},
            "length": {"standard": {"word_range_multiplier": 1.0, "token_budget_multiplier": 1.0}},
        },
        "research": {"auto_triggers": ["latest"]},
    }
    calls = []

    monkeypatch.setattr(orchestrate.llm_call, "load_env", lambda: None)
    monkeypatch.setattr(orchestrate.llm_call, "load_config", lambda: config)
    monkeypatch.setattr(
        orchestrate.llm_call,
        "check_models",
        lambda cfg: {
            "available": [
                {"model": "claude-opus", "provider": "anthropic"},
                {"model": "gpt-5-5", "provider": "openai_compat"},
            ],
            "unavailable": [],
        },
    )
    monkeypatch.setattr(orchestrate.llm_call, "find_project_root", lambda: tmp_path)

    def fake_parallel(config, model_keys, role, prompt, system_prompt=None, system_prompts=None, **kwargs):
        calls.append(("parallel", role, list(model_keys)))
        return [
            {
                "model": model,
                "role": role,
                "response": f"{role} response from {model}",
                "tokens_used": {"input": 1, "output": 1},
                "error": None,
            }
            for model in model_keys
        ]

    def fake_call(config, model_key, role, prompt, **kwargs):
        calls.append(("single", role, model_key))
        response = "Tension map" if role == "tension_mapper" else "## Recommendation\nProceed constructively."
        return {
            "model": model_key,
            "role": role,
            "response": response,
            "tokens_used": {"input": 1, "output": 1},
            "error": None,
        }

    monkeypatch.setattr(orchestrate.llm_call, "call_models_parallel", fake_parallel)
    monkeypatch.setattr(orchestrate.llm_call, "call_model", fake_call)

    args = Namespace(
        question="Should we launch this offer?",
        mode="council",
        depth="basic",
        length="standard",
        rounds=1,
        no_interact=True,
        blind=False,
        chairman=None,
        models="gpt-5-5,claude-opus",
        research="off",
        output="memo",
    )

    result = orchestrate.run_deliberate(args)

    assert calls == [
        ("parallel", "advisor", ["gpt-5-5", "claude-opus"]),
        ("single", "tension_mapper", "claude-opus"),
        ("parallel", "co_construction", ["gpt-5-5", "claude-opus"]),
        ("parallel", "peer_reviewer", ["gpt-5-5", "claude-opus"]),
        ("single", "chairman", "claude-opus"),
    ]
    assert result["metadata"]["models"] == "gpt-5-5, claude-opus"
    assert result["tension_map"] == "Tension map"
    assert result["co_constructions"]


def test_run_deliberate_injects_factcheck_audit_before_advisors(monkeypatch, tmp_path):
    config = {
        "models": {
            "gpt-4-1": {"provider": "openai_compat", "model_id": "gpt-4.1", "api_key_env": "O"},
        },
        "defaults": {
            "deliberate": {
                "preferred_models": ["gpt-4-1"],
                "chairman": "gpt-4-1",
                "default_mode": "council",
            }
        },
        "response_profiles": {
            "depth": {"basic": {"rounds": 1, "max_advisors": 1, "peer_review": True, "base_word_range": [150, 300]}},
            "length": {"standard": {"word_range_multiplier": 1.0, "token_budget_multiplier": 1.0}},
        },
        "research": {"factcheck_model": "gpt-4-1"},
    }
    advisor_prompts = []
    factcheck = orchestrate.FactCheckPack(
        mode="factcheck",
        claims=[],
        sources=[],
        findings=[
            orchestrate.FactCheckFinding(
                claim_id="C1",
                claim="The benchmark improved.",
                urls=["https://example.com"],
                verdict="supported",
                evidence="Source confirms it.",
                issue="",
                correction="",
                confidence="high",
            )
        ],
        audit_markdown="## Fact-Check Audit\n\nC1 supported",
    )

    monkeypatch.setattr(orchestrate.llm_call, "load_env", lambda: None)
    monkeypatch.setattr(orchestrate.llm_call, "load_config", lambda: config)
    monkeypatch.setattr(
        orchestrate.llm_call,
        "check_models",
        lambda cfg: {"available": [{"model": "gpt-4-1", "provider": "openai_compat"}], "unavailable": []},
    )
    monkeypatch.setattr(orchestrate.llm_call, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(orchestrate, "build_factcheck_pack", lambda **kwargs: factcheck)

    def fake_parallel(config, model_keys, role, prompt, system_prompt=None, system_prompts=None, **kwargs):
        if role == "advisor":
            advisor_prompts.append(prompt)
        return [
            {
                "model": model_keys[0],
                "role": role,
                "response": f"{role} response",
                "tokens_used": {},
                "error": None,
            }
        ]

    def fake_call(config, model_key, role, prompt, **kwargs):
        return {
            "model": model_key,
            "role": role,
            "response": "## Recommendation\nProceed.",
            "tokens_used": {},
            "error": None,
        }

    monkeypatch.setattr(orchestrate.llm_call, "call_models_parallel", fake_parallel)
    monkeypatch.setattr(orchestrate.llm_call, "call_model", fake_call)

    result = orchestrate.run_deliberate(
        Namespace(
            question="Please verify every claim.",
            mode="council",
            depth="basic",
            length="standard",
            rounds=1,
            no_interact=True,
            blind=False,
            chairman=None,
            models="gpt-4-1",
            research="factcheck",
            output="memo",
        )
    )

    assert "FACT-CHECK AUDIT" in advisor_prompts[0]
    assert "C1 supported" in advisor_prompts[0]
    assert result["factcheck"]["mode"] == "factcheck"
    assert result["factcheck_path"]
