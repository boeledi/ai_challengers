#!/usr/bin/env python3
"""
AI Provocateurs — Unified Multi-Provider LLM Caller

Core engine that abstracts calls to all supported LLM providers behind a single
CLI interface. Supports single calls, parallel dispatch, per-role token budgets,
retry with exponential backoff, rate limiting, and truncation detection.

Providers:
  - anthropic: Anthropic Messages API (Claude)
  - google: Google GenAI API (Gemini)
  - openai_compat: OpenAI Chat Completions API (GPT, Grok, Mistral, DeepSeek,
                   OpenRouter, z.ai, and any compatible endpoint)

Usage:
  # Single call
  python scripts/llm_call.py --model claude-opus --role advisor --prompt "Question"

  # Parallel calls
  python scripts/llm_call.py --parallel --model claude-opus --model gpt --role advisor --prompt "Q"

  # Health check
  python scripts/llm_call.py --check

  # With thinking level override
  python scripts/llm_call.py --model claude-opus --thinking-level high --role chairman --prompt "..."

Dependencies: requests, pyyaml, python-dotenv
"""

import argparse
import json
import logging
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    import requests
except ImportError:
    print(json.dumps({"error": "Missing dependency: requests. Run: pip install requests"}))
    sys.exit(1)

try:
    import yaml
except ImportError:
    print(json.dumps({"error": "Missing dependency: pyyaml. Run: pip install pyyaml"}))
    sys.exit(1)

try:
    from dotenv import load_dotenv
except ImportError:
    print(json.dumps({"error": "Missing dependency: python-dotenv. Run: pip install python-dotenv"}))
    sys.exit(1)


# =============================================================================
# Constants
# =============================================================================

# Retry configuration per HTTP status code
RETRY_CONFIG = {
    429: {"max_retries": 5, "base_delay": 2,  "max_delay": 120, "backoff": 2.0},
    529: {"max_retries": 3, "base_delay": 30, "max_delay": 120, "backoff": 2.0},
    500: {"max_retries": 2, "base_delay": 5,  "max_delay": 30,  "backoff": 2.0},
    502: {"max_retries": 1, "base_delay": 10, "max_delay": 30,  "backoff": 2.0},
    503: {"max_retries": 1, "base_delay": 10, "max_delay": 30,  "backoff": 2.0},
    408: {"max_retries": 1, "base_delay": 5,  "max_delay": 15,  "backoff": 1.0},
}

# HTTP status codes that are fatal (no retry)
FATAL_STATUS_CODES = {400, 401, 403, 404}

# Logger setup
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s.%(msecs)03d] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger("llm_call")


# =============================================================================
# Configuration Loading
# =============================================================================

def find_project_root() -> Path:
    """Walk up from the script location to find the project root (contains config/)."""
    current = Path(__file__).resolve().parent.parent
    if (current / "config" / "models.yaml").exists():
        return current
    # Fallback: current working directory
    cwd = Path.cwd()
    if (cwd / "config" / "models.yaml").exists():
        return cwd
    return current


def load_config() -> dict:
    """Load models.yaml configuration."""
    root = find_project_root()
    config_path = root / "config" / "models.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_env() -> None:
    """Load .env file from project root, falling back to ~/.env."""
    root = find_project_root()
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.info("Loaded .env from %s", env_path)
    else:
        home_env = Path.home() / ".env"
        if home_env.exists():
            load_dotenv(home_env)
            logger.info("Loaded .env from %s", home_env)
        else:
            logger.warning("No .env file found at %s or %s", env_path, home_env)


def get_token_budget(config: dict, role: str) -> int:
    """Get token budget for a given role from config."""
    budgets = config.get("token_budgets", {})
    return budgets.get(role, budgets.get("default", 4096))


def get_effective_max_tokens(config: dict, model_key: str, role: str) -> int:
    """Compute effective max_tokens = min(token_budget[role], model.max_tokens)."""
    model_cfg = config["models"][model_key]
    model_ceiling = model_cfg.get("max_tokens", 16384)
    budget = get_token_budget(config, role)
    return min(budget, model_ceiling)


def get_timeouts(config: dict, role: str = "advisor") -> tuple:
    """Return (connect_timeout, read_timeout) for a given role."""
    timeouts = config.get("timeouts", {})
    connect = timeouts.get("connect", 10)
    if role == "chairman":
        read = timeouts.get("chairman", 180)
    else:
        read = timeouts.get("read", 120)
    return (connect, read)


def get_rate_limit_config(config: dict, provider: str) -> dict:
    """Get rate limit settings for a provider."""
    rate_limits = config.get("rate_limits", {})
    if provider in rate_limits:
        return rate_limits[provider]
    # Fall back to openai_compat defaults for unknown providers
    return rate_limits.get("openai_compat", {"max_concurrent": 3, "min_delay_between_ms": 500})


# =============================================================================
# Rate Limiting
# =============================================================================

class ProviderRateLimiter:
    """Per-provider semaphore-based rate limiter for parallel calls."""

    def __init__(self, config: dict):
        self._semaphores = {}
        self._delays = {}
        self._last_call = {}
        self._locks = {}
        rate_limits = config.get("rate_limits", {})
        for provider, limits in rate_limits.items():
            max_concurrent = limits.get("max_concurrent", 3)
            delay_ms = limits.get("min_delay_between_ms", 500)
            self._semaphores[provider] = threading.Semaphore(max_concurrent)
            self._delays[provider] = delay_ms / 1000.0
            self._last_call[provider] = 0.0
            self._locks[provider] = threading.Lock()

    def _get_provider_key(self, provider: str) -> str:
        """Map model provider to rate limit config key."""
        if provider in self._semaphores:
            return provider
        return "openai_compat"

    def acquire(self, provider: str) -> None:
        """Acquire rate limit slot for a provider, blocking if necessary."""
        key = self._get_provider_key(provider)
        if key not in self._semaphores:
            return
        self._semaphores[key].acquire()
        # Enforce minimum delay between calls to same provider
        with self._locks[key]:
            now = time.time()
            elapsed = now - self._last_call[key]
            wait = self._delays[key] - elapsed
            if wait > 0:
                time.sleep(wait)
            self._last_call[key] = time.time()

    def release(self, provider: str) -> None:
        """Release rate limit slot for a provider."""
        key = self._get_provider_key(provider)
        if key in self._semaphores:
            self._semaphores[key].release()


# =============================================================================
# Provider Adapters
# =============================================================================

def call_anthropic(
    model_cfg: dict,
    prompt: str,
    system_prompt: str | None,
    max_tokens: int,
    thinking_level: str | None,
    timeouts: tuple,
) -> dict:
    """Call Anthropic Messages API.

    Handles extended thinking via the `thinking` parameter when a thinking_level
    is specified. Maps thinking levels to budget_tokens values.

    Args:
        model_cfg: Model configuration from models.yaml.
        prompt: User prompt text.
        system_prompt: Optional system prompt.
        max_tokens: Maximum output tokens.
        thinking_level: Optional thinking level (low/medium/high).
        timeouts: Tuple of (connect_timeout, read_timeout).

    Returns:
        Raw API response dict or raises requests.HTTPError.
    """
    api_key = os.environ.get(model_cfg["api_key_env"], "")
    if not api_key:
        raise ValueError(f"API key not set: {model_cfg['api_key_env']}")

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    messages = [{"role": "user", "content": prompt}]

    body = {
        "model": model_cfg["model_id"],
        "messages": messages,
        "max_tokens": max_tokens,
    }

    if system_prompt:
        body["system"] = system_prompt

    # Extended thinking support
    if thinking_level:
        thinking_budget_map = {
            "low": 2048,
            "medium": 8192,
            "high": 16384,
        }
        budget = thinking_budget_map.get(thinking_level, 8192)
        body["thinking"] = {
            "type": "enabled",
            "budget_tokens": budget,
        }
        # When thinking is enabled, max_tokens must include thinking budget
        body["max_tokens"] = max_tokens + budget
        headers["anthropic-version"] = "2025-04-14"

    resp = requests.post(
        model_cfg["endpoint"],
        headers=headers,
        json=body,
        timeout=timeouts,
    )
    resp.raise_for_status()
    data = resp.json()

    # Extract response text from content blocks
    response_text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            response_text += block["text"]

    # Token usage
    usage = data.get("usage", {})
    tokens_used = {
        "input": usage.get("input_tokens", 0),
        "output": usage.get("output_tokens", 0),
    }

    # Truncation detection
    stop_reason = data.get("stop_reason", "")
    truncated = stop_reason == "max_tokens"

    return {
        "response": response_text,
        "tokens_used": tokens_used,
        "finish_reason": stop_reason,
        "truncated": truncated,
    }


def call_openai_compat(
    model_cfg: dict,
    prompt: str,
    system_prompt: str | None,
    max_tokens: int,
    thinking_level: str | None,
    timeouts: tuple,
) -> dict:
    """Call OpenAI-compatible Chat Completions API.

    Works with OpenAI, xAI (Grok), Mistral, DeepSeek, OpenRouter, z.ai,
    and any provider implementing the OpenAI chat completions format.

    Args:
        model_cfg: Model configuration from models.yaml.
        prompt: User prompt text.
        system_prompt: Optional system prompt.
        max_tokens: Maximum output tokens.
        thinking_level: Optional thinking level (mapped to reasoning_effort for
                        o-series models, or temperature variation for others).
        timeouts: Tuple of (connect_timeout, read_timeout).

    Returns:
        Raw API response dict or raises requests.HTTPError.
    """
    api_key = os.environ.get(model_cfg["api_key_env"], "")
    if not api_key:
        raise ValueError(f"API key not set: {model_cfg['api_key_env']}")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # OpenRouter requires HTTP-Referer
    if "openrouter.ai" in model_cfg.get("endpoint", ""):
        headers["HTTP-Referer"] = "https://github.com/ai-provocateurs"
        headers["X-Title"] = "AI Provocateurs"

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    body = {
        "model": model_cfg["model_id"],
        "messages": messages,
        "max_tokens": max_tokens,
    }

    # Handle thinking levels for OpenAI o-series models
    model_id = model_cfg.get("model_id", "")
    if thinking_level and model_id.startswith("o"):
        # o-series models support reasoning_effort
        body["reasoning_effort"] = thinking_level
    elif thinking_level:
        # For other models, use temperature as a proxy for thinking diversity
        temp_map = {"low": 0.3, "medium": 0.7, "high": 1.0}
        body["temperature"] = temp_map.get(thinking_level, 0.7)

    resp = requests.post(
        model_cfg["endpoint"],
        headers=headers,
        json=body,
        timeout=timeouts,
    )
    resp.raise_for_status()
    data = resp.json()

    # Extract response
    choices = data.get("choices", [])
    if not choices:
        return {
            "response": "",
            "tokens_used": {"input": 0, "output": 0},
            "finish_reason": "error",
            "truncated": False,
        }

    choice = choices[0]
    response_text = choice.get("message", {}).get("content", "")

    # Token usage
    usage = data.get("usage", {})
    tokens_used = {
        "input": usage.get("prompt_tokens", 0),
        "output": usage.get("completion_tokens", 0),
    }

    # Truncation detection
    finish_reason = choice.get("finish_reason", "")
    truncated = finish_reason == "length"

    return {
        "response": response_text,
        "tokens_used": tokens_used,
        "finish_reason": finish_reason,
        "truncated": truncated,
    }


def call_google(
    model_cfg: dict,
    prompt: str,
    system_prompt: str | None,
    max_tokens: int,
    thinking_level: str | None,
    timeouts: tuple,
) -> dict:
    """Call Google Generative AI API (Gemini).

    Uses the generateContent endpoint with the REST API directly.

    Args:
        model_cfg: Model configuration from models.yaml.
        prompt: User prompt text.
        system_prompt: Optional system prompt.
        max_tokens: Maximum output tokens.
        thinking_level: Optional thinking level (mapped to temperature variation).
        timeouts: Tuple of (connect_timeout, read_timeout).

    Returns:
        Raw API response dict or raises requests.HTTPError.
    """
    api_key = os.environ.get(model_cfg["api_key_env"], "")
    if not api_key:
        raise ValueError(f"API key not set: {model_cfg['api_key_env']}")

    model_id = model_cfg["model_id"]
    endpoint = model_cfg["endpoint"].rstrip("/")
    url = f"{endpoint}/{model_id}:generateContent?key={api_key}"

    contents = []
    if system_prompt:
        contents.append({
            "role": "user",
            "parts": [{"text": f"[System Instructions]\n{system_prompt}\n\n[User Query]\n{prompt}"}],
        })
    else:
        contents.append({
            "role": "user",
            "parts": [{"text": prompt}],
        })

    body = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
        },
    }

    # Temperature variation as thinking level proxy
    if thinking_level:
        temp_map = {"low": 0.3, "medium": 0.7, "high": 1.0}
        body["generationConfig"]["temperature"] = temp_map.get(thinking_level, 0.7)

    headers = {"Content-Type": "application/json"}

    resp = requests.post(url, headers=headers, json=body, timeout=timeouts)
    resp.raise_for_status()
    data = resp.json()

    # Extract response text
    candidates = data.get("candidates", [])
    response_text = ""
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            if "text" in part:
                response_text += part["text"]

    # Token usage
    usage_metadata = data.get("usageMetadata", {})
    tokens_used = {
        "input": usage_metadata.get("promptTokenCount", 0),
        "output": usage_metadata.get("candidatesTokenCount", 0),
    }

    # Truncation detection
    finish_reason = ""
    if candidates:
        finish_reason = candidates[0].get("finishReason", "")
    truncated = finish_reason == "MAX_TOKENS"

    return {
        "response": response_text,
        "tokens_used": tokens_used,
        "finish_reason": finish_reason,
        "truncated": truncated,
    }


# Provider adapter dispatch table
PROVIDER_ADAPTERS = {
    "anthropic": call_anthropic,
    "openai_compat": call_openai_compat,
    "google": call_google,
}


# =============================================================================
# Core Call Logic
# =============================================================================

def call_model(
    config: dict,
    model_key: str,
    role: str,
    prompt: str,
    system_prompt: str | None = None,
    thinking_level: str | None = None,
    rate_limiter: ProviderRateLimiter | None = None,
) -> dict:
    """Make a single LLM call with retry logic and rate limiting.

    This is the main entry point for calling any model. It resolves the provider,
    applies token budgets, handles retries with exponential backoff, and returns
    a standardized JSON-serializable result.

    Args:
        config: Full configuration dict from models.yaml.
        model_key: Key in config.models (e.g., "claude-opus", "gpt").
        role: Token budget role (e.g., "advisor", "chairman").
        prompt: User prompt text.
        system_prompt: Optional system instructions.
        thinking_level: Optional thinking level override (low/medium/high).
        rate_limiter: Optional ProviderRateLimiter for parallel calls.

    Returns:
        Dict with keys: model, role, response, tokens_used, duration_ms,
        thinking_level, finish_reason, truncated, error.
    """
    model_cfg = config["models"].get(model_key)
    if not model_cfg:
        return _error_result(model_key, role, thinking_level, f"Unknown model: {model_key}")

    provider = model_cfg.get("provider", "openai_compat")
    adapter = PROVIDER_ADAPTERS.get(provider)
    if not adapter:
        return _error_result(model_key, role, thinking_level, f"Unknown provider: {provider}")

    # Resolve thinking level
    if not thinking_level:
        thinking_level = model_cfg.get("default_thinking")

    max_tokens = get_effective_max_tokens(config, model_key, role)
    timeouts = get_timeouts(config, role)

    start_time = time.time()
    last_error = None

    # Attempt with retries
    for attempt in range(max_retries_for_all() + 1):
        try:
            if rate_limiter:
                rate_limiter.acquire(provider)
            try:
                result = adapter(
                    model_cfg=model_cfg,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    thinking_level=thinking_level,
                    timeouts=timeouts,
                )
            finally:
                if rate_limiter:
                    rate_limiter.release(provider)

            duration_ms = int((time.time() - start_time) * 1000)

            if result.get("truncated"):
                logger.warning(
                    "Response truncated for %s (role=%s). Consider increasing token budget.",
                    model_key, role,
                )

            return {
                "model": model_key,
                "role": role,
                "response": result["response"],
                "tokens_used": result["tokens_used"],
                "duration_ms": duration_ms,
                "thinking_level": thinking_level,
                "finish_reason": result["finish_reason"],
                "truncated": result["truncated"],
                "error": None,
            }

        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else 0
            error_body = ""
            try:
                error_body = e.response.text[:500] if e.response is not None else ""
            except Exception:
                pass

            last_error = f"HTTP {status_code}: {error_body}"
            logger.warning("API error for %s: %s (attempt %d)", model_key, last_error, attempt + 1)

            # Fatal errors — no retry
            if status_code in FATAL_STATUS_CODES:
                break

            # Check retry config for this status code
            retry_cfg = RETRY_CONFIG.get(status_code)
            if not retry_cfg or attempt >= retry_cfg["max_retries"]:
                break

            # Exponential backoff with jitter
            delay = min(
                retry_cfg["base_delay"] * (retry_cfg["backoff"] ** attempt),
                retry_cfg["max_delay"],
            )
            jitter = random.uniform(0, 1.0)
            total_delay = delay + jitter
            logger.info("Retrying %s in %.1fs...", model_key, total_delay)
            time.sleep(total_delay)

        except requests.exceptions.Timeout:
            last_error = f"Request timed out after {timeouts[1]}s"
            logger.warning("Timeout for %s: %s (attempt %d)", model_key, last_error, attempt + 1)
            retry_cfg = RETRY_CONFIG.get(408)
            if not retry_cfg or attempt >= retry_cfg["max_retries"]:
                break
            time.sleep(retry_cfg["base_delay"])

        except requests.exceptions.ConnectionError as e:
            last_error = f"Connection error: {e}"
            logger.warning("Connection error for %s: %s", model_key, last_error)
            break

        except ValueError as e:
            last_error = str(e)
            logger.warning("Config error for %s: %s", model_key, last_error)
            break

        except Exception as e:
            last_error = f"Unexpected error: {e}"
            logger.error("Unexpected error for %s: %s", model_key, last_error, exc_info=True)
            break

    duration_ms = int((time.time() - start_time) * 1000)
    return _error_result(model_key, role, thinking_level, last_error, duration_ms)


def max_retries_for_all() -> int:
    """Return the maximum number of retries across all status codes."""
    return max(cfg["max_retries"] for cfg in RETRY_CONFIG.values())


def _error_result(
    model_key: str,
    role: str,
    thinking_level: str | None,
    error: str | None,
    duration_ms: int = 0,
) -> dict:
    """Build a standardized error result."""
    return {
        "model": model_key,
        "role": role,
        "response": None,
        "tokens_used": {"input": 0, "output": 0},
        "duration_ms": duration_ms,
        "thinking_level": thinking_level,
        "finish_reason": "error",
        "truncated": False,
        "error": error,
    }


def call_models_parallel(
    config: dict,
    model_keys: list[str],
    role: str,
    prompt: str,
    system_prompt: str | None = None,
    system_prompts: list[str] | None = None,
    thinking_level: str | None = None,
    thinking_levels: list[str] | None = None,
) -> list[dict]:
    """Call multiple models in parallel with rate limiting.

    When system_prompts is provided, each model gets its own system prompt
    (matched by index). Otherwise, all models share the same system_prompt.
    Same logic applies to thinking_levels vs thinking_level.

    Args:
        config: Full configuration dict.
        model_keys: List of model keys to call.
        role: Token budget role for all calls.
        prompt: User prompt (shared across all calls).
        system_prompt: Shared system prompt (used when system_prompts is None).
        system_prompts: Per-model system prompts (overrides system_prompt).
        thinking_level: Shared thinking level override.
        thinking_levels: Per-model thinking level overrides.

    Returns:
        List of result dicts in the same order as model_keys.
    """
    rate_limiter = ProviderRateLimiter(config)
    results = [None] * len(model_keys)

    def _call(index: int, model_key: str):
        sp = system_prompts[index] if system_prompts else system_prompt
        tl = thinking_levels[index] if thinking_levels else thinking_level
        return index, call_model(
            config=config,
            model_key=model_key,
            role=role,
            prompt=prompt,
            system_prompt=sp,
            thinking_level=tl,
            rate_limiter=rate_limiter,
        )

    # Use max 10 workers — most sessions have 5 or fewer models
    max_workers = min(len(model_keys), 10)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_call, i, key): i
            for i, key in enumerate(model_keys)
        }
        for future in as_completed(futures):
            idx, result = future.result()
            results[idx] = result

    return results


# =============================================================================
# Health Check
# =============================================================================

def check_models(config: dict) -> dict:
    """Check which models have valid API keys set in the environment.

    Returns a dict with 'available' and 'unavailable' lists, each containing
    dicts with model key, provider, and status info.
    """
    available = []
    unavailable = []

    for model_key, model_cfg in config.get("models", {}).items():
        # Skip commented-out models
        if not isinstance(model_cfg, dict):
            continue
        api_key_env = model_cfg.get("api_key_env", "")
        api_key = os.environ.get(api_key_env, "")
        provider = model_cfg.get("provider", "unknown")
        model_id = model_cfg.get("model_id", "unknown")

        if api_key and api_key not in ("sk-ant-...", "sk-...", "AIza...", "xai-...", "sk-or-...", "..."):
            available.append({
                "model": model_key,
                "provider": provider,
                "model_id": model_id,
                "status": "ready",
            })
        else:
            unavailable.append({
                "model": model_key,
                "provider": provider,
                "model_id": model_id,
                "status": "no_api_key",
                "env_var": api_key_env,
            })

    return {"available": available, "unavailable": unavailable}


# =============================================================================
# CLI
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="AI Provocateurs — Unified Multi-Provider LLM Caller",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single call
  python scripts/llm_call.py --model claude-opus --role advisor --prompt "What's the risk?"

  # Parallel calls
  python scripts/llm_call.py --parallel --model claude-opus --model gpt --role advisor --prompt "Q"

  # Health check
  python scripts/llm_call.py --check

  # With thinking level
  python scripts/llm_call.py --model claude-opus --thinking-level high --role chairman --prompt "..."
        """,
    )

    parser.add_argument(
        "--model", "-m",
        action="append",
        dest="models",
        help="Model key from models.yaml. Repeat for parallel calls.",
    )
    parser.add_argument(
        "--role", "-r",
        default="default",
        help="Token budget role (advisor, chairman, reader, etc.). Default: 'default'.",
    )
    parser.add_argument(
        "--prompt", "-p",
        help="The prompt text to send.",
    )
    parser.add_argument(
        "--prompt-file", "-pf",
        help="Read prompt from a file instead of --prompt.",
    )
    parser.add_argument(
        "--system", "-s",
        help="System prompt text.",
    )
    parser.add_argument(
        "--system-file", "-sf",
        help="Read system prompt from a file.",
    )
    parser.add_argument(
        "--thinking-level", "-t",
        choices=["low", "medium", "high"],
        help="Override the model's default thinking level.",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Call all --model entries in parallel (requires 2+ models).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check which models are available (API keys set). No call made.",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress log output to stderr.",
    )

    return parser


def main():
    """Main entry point for the CLI."""
    parser = build_parser()
    args = parser.parse_args()

    if args.quiet:
        logger.setLevel(logging.WARNING)

    # Load environment and config
    load_env()
    config = load_config()

    # Health check mode
    if args.check:
        result = check_models(config)
        print(json.dumps(result, indent=2))
        return

    # Validate inputs
    if not args.models:
        parser.error("--model is required (unless using --check)")

    # Resolve prompt
    prompt = args.prompt
    if args.prompt_file:
        prompt_path = Path(args.prompt_file)
        if not prompt_path.exists():
            print(json.dumps({"error": f"Prompt file not found: {args.prompt_file}"}))
            sys.exit(1)
        prompt = prompt_path.read_text(encoding="utf-8")

    if not prompt:
        parser.error("--prompt or --prompt-file is required")

    # Resolve system prompt
    system_prompt = args.system
    if args.system_file:
        sys_path = Path(args.system_file)
        if not sys_path.exists():
            print(json.dumps({"error": f"System file not found: {args.system_file}"}))
            sys.exit(1)
        system_prompt = sys_path.read_text(encoding="utf-8")

    # Single model call
    if len(args.models) == 1 and not args.parallel:
        result = call_model(
            config=config,
            model_key=args.models[0],
            role=args.role,
            prompt=prompt,
            system_prompt=system_prompt,
            thinking_level=args.thinking_level,
        )
        print(json.dumps(result, indent=2))
        return

    # Parallel calls
    results = call_models_parallel(
        config=config,
        model_keys=args.models,
        role=args.role,
        prompt=prompt,
        system_prompt=system_prompt,
        thinking_level=args.thinking_level,
    )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
