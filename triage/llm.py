"""A small client for OpenAI-compatible chat APIs (Gemini, Groq, Ollama all offer one).

Why not a provider SDK: one ~150-line client over httpx covers every free provider, and every
behaviour that matters for the agent is visible here: retries, fallback, token usage, and
how tool calls are parsed.

Failure policy:
- 429 (rate limit), 5xx, timeouts and connection errors are *transient*: retry with backoff
  (honouring Retry-After), then fall back to the second provider if one is configured.
- Anything else (bad key, unknown model, malformed request) is a *configuration* error:
  fail at once with the provider's message. Retrying would only hide it.
"""

import json
import os
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRY_WAIT_S = 30
_GOOGLE_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')


def retry_wait_hint(resp: httpx.Response) -> float | None:
    """How long the provider asks us to wait: a Retry-After header, or Google's retryDelay,
    which it sends in the response body instead (e.g. "61157s" for a used-up daily quota)."""
    header = resp.headers.get("retry-after")
    if header and header.replace(".", "", 1).isdigit():
        return float(header)
    match = _GOOGLE_RETRY_DELAY.search(resp.text)
    return float(match.group(1)) if match else None


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False)  # never printed
    # Gemini's "thinking" models count hidden reasoning against max_tokens; "low" keeps
    # replies fast and leaves room for the answer. Sent only when set.
    reasoning_effort: str | None = None
    timeout_s: float = 90


# Defaults for free tiers. Model names change over time: override with TRIAGE_MODEL /
# TRIAGE_FALLBACK_MODEL, and check what a key can use with `python -m triage models`.
# Measured 2026-11-21 on a free Google AI Studio key: gemini-3.5-flash-lite answered tool
# calls in 1.4-1.8 s; gemini-3.5-flash took 54 s and allows only 20 requests per day.
# Each model has its own daily quota, so a second model of the same provider is a useful
# fallback when no other provider's key is available.
PRESETS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_env": "GEMINI_API_KEY",
        "model": "gemini-3.5-flash-lite",
        "fallback_model": "gemini-3.1-flash-lite",
        "reasoning_effort": None,
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "model": "llama-3.3-70b-versatile",
        "reasoning_effort": None,
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "key_env": None,
        "model": "qwen2.5:3b",
        "reasoning_effort": None,
    },
}


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader: KEY=VALUE lines; existing environment variables win."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def provider_from_env(name: str, model: str | None = None) -> Provider | None:
    """A provider from its preset and API key; None if the key is not set."""
    preset = PRESETS.get(name)
    if preset is None:
        raise ValueError(f"unknown provider '{name}'. Known: {', '.join(PRESETS)}")
    key = os.environ.get(preset["key_env"]) if preset["key_env"] else None
    if preset["key_env"] and not key:
        return None
    return Provider(
        name=name,
        base_url=preset["base_url"],
        model=model or preset["model"],
        api_key=key,
        reasoning_effort=os.environ.get("TRIAGE_REASONING_EFFORT") or preset["reasoning_effort"],
    )


def providers_from_env() -> tuple[Provider | None, Provider | None]:
    """Primary and fallback, from TRIAGE_* settings (see python -m triage --help).

    Fallback default: Groq if its key is set, otherwise a second model of the primary
    provider (a separate daily quota).
    """
    primary_name = os.environ.get("TRIAGE_PROVIDER", "gemini")
    primary = provider_from_env(primary_name, os.environ.get("TRIAGE_MODEL"))
    default_fallback = "groq" if os.environ.get("GROQ_API_KEY") else primary_name
    fallback_name = os.environ.get("TRIAGE_FALLBACK_PROVIDER", default_fallback)
    fallback_model = os.environ.get("TRIAGE_FALLBACK_MODEL")
    if fallback_model is None and fallback_name == primary_name:
        fallback_model = PRESETS[primary_name].get("fallback_model")
    fallback = (
        provider_from_env(fallback_name, fallback_model)
        if fallback_model or (fallback_name != primary_name)
        else None
    )
    if primary and fallback and (fallback.name, fallback.model) == (primary.name, primary.model):
        fallback = None
    return primary, fallback


class LLMError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: str  # raw JSON text, exactly as the model produced it
    # Provider-specific fields, sent back unchanged with the next request. Gemini 3 puts a
    # "thought signature" here and rejects the follow-up request (HTTP 400) without it.
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class ChatResult:
    content: str
    tool_calls: list[ToolCallRequest]
    finish_reason: str
    usage: Usage
    provider: str
    model: str
    latency_ms: int
    attempts: int = 1
    message_extra: dict = field(default_factory=dict)  # same idea, at message level

    def as_message(self) -> dict:
        """The assistant turn, in the shape the API expects to receive back."""
        message: dict = {**self.message_extra, "role": "assistant", "content": self.content or None}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    **c.extra,
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments},
                }
                for c in self.tool_calls
            ]
        return message


class ChatModel(Protocol):
    def chat(
        self, messages: list[dict], tools: list[dict], tool_choice: str | dict = "auto"
    ) -> ChatResult: ...


def parse_response(body: dict, provider: Provider, latency_ms: int, attempts: int) -> ChatResult:
    try:
        choice = body["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"{provider.name}: unexpected response shape: {str(body)[:300]}") from None
    calls = []
    for raw in message.get("tool_calls") or []:
        fn = raw.get("function") or {}
        args = fn.get("arguments")
        calls.append(
            ToolCallRequest(
                # Some providers omit ids; the API needs one to pair the result with the call.
                id=raw.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                name=fn.get("name", ""),
                arguments=args if isinstance(args, str) else json.dumps(args or {}),
                extra={k: v for k, v in raw.items() if k not in ("id", "type", "function")},
            )
        )
    usage = body.get("usage") or {}
    return ChatResult(
        content=message.get("content") or "",
        tool_calls=calls,
        finish_reason=choice.get("finish_reason") or "",
        usage=Usage(usage.get("prompt_tokens", 0) or 0, usage.get("completion_tokens", 0) or 0),
        provider=provider.name,
        model=provider.model,
        latency_ms=latency_ms,
        attempts=attempts,
        message_extra={
            k: v for k, v in message.items() if k not in ("role", "content", "tool_calls")
        },
    )


class OpenAICompatibleClient:
    def __init__(
        self,
        primary: Provider,
        fallback: Provider | None = None,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        retries: int = 3,
        http: httpx.Client | None = None,
        sleep=time.sleep,
    ) -> None:
        self.providers = [p for p in (primary, fallback) if p is not None]
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.retries = retries
        self.http = http or httpx.Client()
        self.sleep = sleep

    def chat(
        self, messages: list[dict], tools: list[dict], tool_choice: str | dict = "auto"
    ) -> ChatResult:
        last_error: LLMError | None = None
        for provider in self.providers:
            try:
                return self._chat_one(provider, messages, tools, tool_choice)
            except LLMError as exc:
                if exc.status is not None and exc.status not in RETRYABLE_STATUS:
                    raise  # configuration error: a fallback would only hide it
                last_error = exc
        raise last_error or LLMError("no provider configured")

    def _chat_one(self, provider: Provider, messages, tools, tool_choice) -> ChatResult:
        payload: dict = {
            "model": provider.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        if provider.reasoning_effort:
            payload["reasoning_effort"] = provider.reasoning_effort
        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"

        for attempt in range(1, self.retries + 1):
            start = time.perf_counter()
            wait = None
            try:
                resp = self.http.post(
                    f"{provider.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=provider.timeout_s,
                )
            except httpx.HTTPError as exc:
                error = LLMError(f"{provider.name}: {type(exc).__name__}")
            else:
                latency = round((time.perf_counter() - start) * 1000)
                if resp.status_code == 200:
                    return parse_response(resp.json(), provider, latency, attempt)
                error = LLMError(
                    f"{provider.name} HTTP {resp.status_code}: {resp.text[:400]}", resp.status_code
                )
                if resp.status_code not in RETRYABLE_STATUS:
                    raise error
                wait = retry_wait_hint(resp)
                if wait is not None and wait > MAX_RETRY_WAIT_S:
                    # e.g. a daily quota that resets in hours: waiting is pointless here,
                    # so give up on this provider now and let the fallback answer.
                    raise LLMError(
                        f"{provider.name}/{provider.model} unavailable for {wait:.0f}s "
                        f"(HTTP {resp.status_code}, likely a used-up quota)",
                        resp.status_code,
                    )
            if attempt < self.retries:
                wait = wait if wait is not None else 1.5 * 2 ** (attempt - 1) + random.random()
                self.sleep(min(wait, MAX_RETRY_WAIT_S))
        raise error
