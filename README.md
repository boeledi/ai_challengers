# AI Challengers

AI Challengers is a multi-model deliberation system. Its goal is not to make one agent defeat another, but to give the user a stronger basis for understanding and decision-making.

The project sends a question, idea, text, or decision to multiple models and personas. Each agent contributes a different perspective, then the system turns agreements, disagreements, and blind spots into an actionable decision memo.

```text
Question
  -> ContextPack
  -> independent contributions
  -> productive tension map
  -> co-construction
  -> constructive review
  -> decision memo
```

## Goal

AI Challengers is designed to help users:

- get complementary perspectives;
- identify arguments and counterarguments;
- better understand available options;
- weigh pros and cons;
- discover new ideas;
- make assumptions and uncertainties explicit;
- prepare better before making a decision.

The default mode is constructive. Adversarial modes remain available, but only for cases where the explicit goal is to stress-test, attack, or defend a position.

## Interfaces

The project currently exposes three ways to use the system:

| Interface | Primary Use | Input | Output |
|-----------|-------------|-------|--------|
| FastAPI web app | Local interactive use | Web forms | Decision memo, HTML report, session history |
| Standalone CLI | Automation and direct runs | `scripts/orchestrate.py` | Console memo and files in `output/` |
| Claude Code skills | Use from Claude Code | `/deliberate`, `/analyze` | Structured responses and reports |

## Web Version

The web version is the recommended interface for local testing and day-to-day use.

### Start the Web App

```bash
python run_web.py
```

By default, the app runs at:

```text
http://127.0.0.1:8080/
```

Host, port, SQLite path, and max concurrent pipelines are configured in [config/models.yaml](config/models.yaml), under the `web` section.

```yaml
web:
  host: "127.0.0.1"
  port: 8080
  db_path: "data/sessions.db"
  max_concurrent_pipelines: 3
```

### Web Features

- constructive deliberation form;
- mode, depth, length, and round selection;
- `Research` selector: `auto`, `context`, `factcheck`, `deep`, `on`, `off`;
- `Primary Output` selector: `memo`, `full`, `both`;
- upload of text, code, HTML, or data context files;
- real-time progress through Server-Sent Events;
- mid-pipeline clarification questions when advisors need more information;
- decision memo shown outside the report iframe;
- full HTML report in an iframe;
- report downloads for memo/synthesis, Markdown, HTML, logs, and fact-check audits when available;
- session history;
- API key configuration page.

### File Uploads

Uploaded files are not appended raw to the question. They are processed through a `ContextPack`:

- HTML cleanup;
- removal of scripts, styles, dangerous attributes, and unsafe URLs;
- text normalization;
- bounding of large files;
- metadata for truncated or skipped files.

PDFs are intentionally excluded from the web upload UI until real PDF parsing is implemented. If a PDF still reaches the pipeline, it is explicitly marked as skipped.

### Main Routes

| Route | Purpose |
|-------|---------|
| `/` | New deliberation and analysis forms |
| `/deliberate/{session_id}` | Deliberation progress and result |
| `/analyze/{session_id}` | Analysis progress and result |
| `/sessions` | Session history |
| `/sessions/{session_id}` | Past session details |
| `/sessions/{session_id}/download/{artifact}` | Download `memo`, `md`, `html`, `log`, or `factcheck` artifacts |
| `/config` | Model status and API key entry |
| `/api/models/check` | JSON model availability check |
| `/api/health` | Simple health check |

Web sessions are stored in SQLite through [web/session_store.py](web/session_store.py).

## `/deliberate` Pipeline

The current constructive pipeline follows these phases:

1. **ContextPack**: clean the question and context files.
2. **Optional research**: `auto`, `context`, `factcheck`, `deep`, `on`, or `off`.
3. **Framing**: neutral reframing of the question.
4. **Model allocation**: honor `--models`, show real diversity, chairman, and quorum.
5. **Independent contributions**: parallel advisor responses.
6. **Deliberation rounds**: optional, based on `--rounds` or `--depth`.
7. **Productive tension map**: solid agreements, useful disagreements, cruxes, blind spots.
8. **Co-construction**: each advisor improves their response using the strongest contributions from others.
9. **Constructive review**: synergy, gaps, agreement quality, cruxes.
10. **Final memo**: decision-oriented synthesis.
11. **Reports**: HTML report, Markdown transcript, session log, and optional fact-check audit.

### Research Modes

| Mode | Behavior |
|------|----------|
| `off` | No external research |
| `context` | Lightweight web context only |
| `on` | Legacy alias for `context` |
| `factcheck` | Extract checkable claims, fetch cited URLs, verify claims against source excerpts, and generate a fact-check audit |
| `deep` | Fact-check audit plus lightweight contextual research |
| `auto` | Uses `factcheck` for explicit verification requests and `context` for time-sensitive external facts |

Fact-check mode is intended for drafts, articles, citations, figures, links, and publication workflows. It does not give every advisor free Internet access. Instead, the pipeline builds a structured audit first, then injects it into the deliberation.

### Final Memo Structure

Constructive modes prioritize a memo with these sections:

- `Recommendation`
- `Key Insights`
- `Options`
- `Arguments For/Against`
- `Decision Cruxes`
- `Missing Information`
- `Confidence`
- `Next Step`

### Modes

| Mode | Purpose | Constructive by Default |
|------|---------|-------------------------|
| `council` | General complementary perspectives | Yes |
| `compass` | Strategic reading through directional personas | Yes |
| `raw` | Free responses from multiple models | Yes |
| `steelman` | Strong defense of several options | Yes |
| `forecast` | Prediction and confidence levels | Yes |
| `collaborative` | Build an actionable plan | Yes |
| `redteam` | Deliberately attack a plan or idea | No |
| `premortem` | Failure scenarios before launch | No |
| `advocate` | Structured pro/con debate | No |

## Standalone CLI

The standalone CLI lives in [scripts/orchestrate.py](scripts/orchestrate.py).

### Deliberation

```bash
python scripts/orchestrate.py deliberate "Should we launch a paid pilot next month?"
```

Available options:

| Option | Values | Description |
|--------|--------|-------------|
| `--mode`, `-m` | `council`, `compass`, `raw`, `redteam`, `premortem`, `steelman`, `advocate`, `forecast`, `collaborative` | Deliberation mode |
| `--rounds`, `-r` | integer | Number of rounds |
| `--depth`, `-d` | `quick`, `basic`, `stress`, `deep`, `ultra` | Depth profile |
| `--length`, `-l` | `concise`, `standard`, `detailed`, `comprehensive` | Response length |
| `--no-interact` | boolean | Disable clarification questions |
| `--blind`, `-b` | boolean | Hide model identities during selected steps |
| `--chairman`, `-c` | model key | Synthesis model |
| `--models` | comma-separated list | Models to use, in order |
| `--research` | `auto`, `context`, `factcheck`, `deep`, `on`, `off` | Optional external research and fact-checking |
| `--output` | `memo`, `full`, `both` | Primary console output |

Examples:

```bash
python scripts/orchestrate.py deliberate \
  --mode council \
  --depth basic \
  --research auto \
  --output memo \
  "Should we launch a paid pilot for our B2B SaaS offer next month?"
```

```bash
python scripts/orchestrate.py deliberate \
  --mode redteam \
  --models claude-opus,gpt-5-5 \
  "Find the serious flaws in this incident response plan."
```

### Analysis

```bash
python scripts/orchestrate.py analyze "https://example.com/article"
```

Available options:

| Option | Values | Description |
|--------|--------|-------------|
| `--with-qa`, `-q` | boolean | Generate Q&A pairs |
| `--qa-count` | integer | Number of Q&A pairs |
| `--lang`, `-l` | text | Desired output language |
| `--compare` | boolean | Comparison mode |
| `--extract` | boolean | Structured extraction |

## Claude Code Skills

The Claude Code skills are available under `.claude/skills/`:

| Skill | Purpose |
|-------|---------|
| `.claude/skills/deliberate/SKILL.md` | Multi-perspective deliberation |
| `.claude/skills/analyze/SKILL.md` | Multi-pass document or URL analysis |

The `deliberate` skill spec should stay aligned with [scripts/orchestrate.py](scripts/orchestrate.py): same constructive logic, same review criteria, same memo structure.

## Configuration

The main configuration file is [config/models.yaml](config/models.yaml).

### Models

Model keys are the internal identifiers used by `--models`, defaults, and pricing.

Examples of direct model keys:

- `claude-opus`
- `claude-sonnet`
- `gpt-5-5`
- `gpt-4-1`
- `gemini`
- `grok`
- `mistral-large`
- `deepseek`
- `deepseek-r1`

OpenAI-compatible providers can be added without code changes by adding a `provider: openai_compat` entry.

### Important Defaults

```yaml
defaults:
  deliberate:
    preferred_models: [claude-opus, gpt-5-5, gpt-4-1, gemini, grok, claude-sonnet]
    chairman: claude-opus
    default_rounds: 1
    default_mode: council
  analyze:
    roles:
      reader: claude-opus
      reviewer: gpt-5-5
      researcher: gemini
      summarizer: claude-sonnet
```

### Optional Research

```yaml
research:
  default: auto
  provider: duckduckgo
  max_results: 5
  factcheck_model: gpt-4-1
  max_factcheck_claims: 50
  max_factcheck_sources: 50
```

`auto` selects `factcheck` when the prompt explicitly asks to verify data, links, sources, citations, references, or an article before publication. It selects `context` for questions that appear to depend on external or recent facts: market, pricing, law, regulation, benchmarks, vendors, models, APIs, security, or dates.

Fact-checking intentionally uses a cheaper configured verifier (`research.factcheck_model`) by default. Fetching URLs, HTTP status checks, source extraction, and Markdown link parsing are deterministic; stronger models remain useful for the final synthesis, not for every source lookup.

### Quality Rubric

The `quality_rubric` section encodes the constructive default:

- actionable recommendation first;
- arguments and counterarguments tied to options;
- disagreements turned into cruxes, validity conditions, or mitigations;
- consensus treated as a possible confidence signal;
- missing information and uncertainty made explicit;
- concrete next action.

## Installation

### Requirements

- Python 3.10+;
- at least one model API key;
- ideally several providers for stronger diversity.

### Dependencies

```bash
pip install -r requirements.txt
```

### Environment Variables

Copy the example and fill in the keys you have:

```bash
cp config/.env.example .env
```

Recognized variables:

| Provider | Variable |
|----------|----------|
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Google Gemini | `GOOGLE_API_KEY` |
| xAI | `XAI_API_KEY` |
| Mistral | `MISTRAL_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| z.ai | `ZAI_API_KEY` |

Check the setup:

```bash
python scripts/llm_call.py --check
```

## Output Files

Generated files are written to `output/`.

| File | Purpose |
|------|---------|
| `output/deliberate-report-{timestamp}.html` | Deliberation HTML report |
| `output/deliberate-transcript-{timestamp}.md` | Full deliberation transcript |
| `output/factcheck-{timestamp}.md` | Fact-check audit when `factcheck` or `deep` is active |
| `output/analyze-report-{timestamp}.html` | Analysis HTML report |
| `output/analyze-report-{timestamp}.md` | Analysis Markdown report |
| `output/logs/session-{timestamp}.log` | Session log |

The web version also stores sessions in `data/sessions.db`.

## Security

The project applies several prompt-injection defenses:

- user inputs are wrapped in `<user_input>`;
- model outputs passed into later stages are wrapped in `<model_output>`;
- system prompts state that these blocks are data, not instructions;
- HTML outputs are escaped;
- `ContextPack` sanitizes HTML, scripts, styles, JS event attributes, and unsafe URLs.

These defenses reduce risk, but do not eliminate it. For sensitive decisions, review outputs and verify critical facts.

## Tests and Evaluations

Run the test suite:

```bash
python -m pytest -q
```

The tests cover:

- absence of legacy destructive review criteria in constructive mode;
- presence of synergy, cruxes, construction, and mitigation;
- constructive pipeline order with fake LLMs;
- `--models` handling;
- conditional triggering of `--research auto`, including fact-check detection;
- HTML cleanup and PDF handling;
- consistency of configured model keys.

[evals/constructive_cases.yaml](evals/constructive_cases.yaml) contains a small qualitative evaluation set for constructiveness, novelty, clarity, actionability, and uncertainty management.

## Known Limits

- Real PDF parsing is not implemented for `/deliberate` yet.
- Lightweight external research uses DuckDuckGo best effort and is not guaranteed to be available.
- Fact-checking verifies retrieved source excerpts; JavaScript-heavy, blocked, or paywalled pages may still need manual review.
- Quorum quality depends on the API keys present in `.env`.
- Adversarial modes can be intentionally harsh by design.
- Costs depend on models, depth, length, and number of rounds.

## Troubleshooting

| Problem | Action |
|---------|--------|
| No models available | Check `.env`, then run `python scripts/llm_call.py --check` |
| Costs are too high | Use `--depth quick`, `--length concise`, or `--models` |
| Timeouts | Increase `timeouts.read` or `timeouts.chairman` |
| Rate limits | Reduce `rate_limits.*.max_concurrent` |
| Uploaded PDF is skipped | Convert the PDF to text or Markdown before upload |
| Web app is unreachable | Check that `python run_web.py` is running and port `8080` is free |
| No report generated | Check `output/` and session logs |

## Project Structure

```text
ai_challengers/
  .claude/skills/              Claude Code skills
  config/models.yaml           Models, defaults, research, web, budgets
  config/.env.example          API key template
  scripts/llm_call.py          Multi-provider LLM client
  scripts/orchestrate.py       CLI orchestrator
  web/                         FastAPI application
  run_web.py                   Local web launcher
  tests/                       Pytest suite
  evals/                       Qualitative evaluation cases
  output/                      Generated reports
  data/sessions.db             Local web SQLite database
```

## Intent

The project is aligned with its original ambition: combine independent perspectives to produce broader understanding, useful contradiction, a clear synthesis, and better human decisions.
