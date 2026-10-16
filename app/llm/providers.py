"""Language-model providers, with streaming.

Two real providers:

* ``GroqProvider``   - Groq cloud (free tier), OpenAI-compatible API.
                       Default model: openai/gpt-oss-120b.
* ``OllamaProvider`` - a model running on this laptop through Ollama.
                       Default model: qwen2.5-coder:7b (fits a 6 GB GPU).

Both turn a list of chat messages into a stream of events:

    ("text", "...")      a piece of the reply, as it is generated
    ("status", "...")    something the user should know (e.g. rate-limit wait)
    ("usage", {...})     token counts at the end, if the provider reports them

A ``ScriptedProvider`` returns pre-written replies. It is only used by the
automatic tests; it is never offered in the app.

Only the Python standard library is used for HTTP, so there is nothing extra
to install. The API key is sent only to Groq and never logged.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

Event = tuple[str, Any]
Message = dict[str, str]

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_RATE_LIMIT_RETRIES = 3


class LLMError(RuntimeError):
    """A problem talking to the model, with a message a user can act on."""


class RequestTooLarge(LLMError):
    """The prompt does not fit the provider's limit; the caller can retry with less context."""


# Groq free tier: about 8,000 tokens per minute per model, and Groq counts the
# prompt PLUS the requested reply length against it. Keep every request under.
GROQ_FREE_TPM = 8000
MIN_REPLY_TOKENS = 1500


def estimate_tokens(messages: list[dict[str, str]]) -> int:
    return int(sum(len(m["content"]) for m in messages) / 3.5) + 20 * len(messages)


Opener = Callable[..., Any]


def _open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def _read_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        return str(data.get("error", {}).get("message") or data.get("error") or body)[:300]
    except Exception:
        return f"HTTP {exc.code}"


@dataclass
class GroqProvider:
    api_key: str = field(repr=False)
    model: str = "openai/gpt-oss-120b"
    temperature: float = 0.2
    max_tokens: int = 6000
    timeout: float = 180.0
    opener: Opener = _open
    sleep: Callable[[float], None] = time.sleep
    name: str = "groq"

    tpm_limit: int = GROQ_FREE_TPM

    def reply_budget(self, messages: list[Message]) -> int:
        return min(self.max_tokens, self.tpm_limit - 100 - estimate_tokens(messages))

    def _payload(self, messages: list[Message], stream: bool) -> dict[str, Any]:
        budget = self.reply_budget(messages)
        if budget < MIN_REPLY_TOKENS:
            raise RequestTooLarge(
                f"This request is about {estimate_tokens(messages)} tokens, too big for Groq's free tier "
                f"({self.tpm_limit} tokens per minute including the reply)."
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_completion_tokens": budget,
            "stream": stream,
        }
        if "gpt-oss" in self.model:
            # Think before answering (more when there is room), but don't stream the thinking.
            payload["reasoning_effort"] = "medium" if budget >= 4000 else "low"
            payload["include_reasoning"] = False
        return payload

    def _request(self, payload: dict[str, Any]) -> urllib.request.Request:
        return urllib.request.Request(
            GROQ_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "AI-Driven-Embedded-Programmer/1.0",
            },
            method="POST",
        )

    def stream(self, messages: list[Message]) -> Iterator[Event]:
        if not self.api_key:
            raise LLMError("No Groq API key. Open Settings and paste your free key from console.groq.com/keys.")
        payload = self._payload(messages, stream=True)
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                response = self.opener(self._request(payload), self.timeout)
            except urllib.error.HTTPError as exc:
                message = _read_error(exc)
                if exc.code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                    wait = _retry_after(exc, default=8.0 * (attempt + 1))
                    yield ("status", f"Groq free-tier limit reached, waiting {wait:.0f} s…")
                    self.sleep(wait)
                    continue
                if exc.code == 401:
                    raise LLMError("Groq rejected the API key. Check it in Settings.") from exc
                if exc.code == 413 or "too large" in message.lower():
                    raise RequestTooLarge(
                        "The request is too big for Groq's free tier (about 8,000 tokens per minute). "
                        "Start a new chat or use fewer examples in Settings."
                    ) from exc
                raise LLMError(f"Groq error {exc.code}: {message}") from exc
            except urllib.error.URLError as exc:
                raise LLMError(f"Could not reach Groq ({exc.reason}). Check your internet connection.") from exc
            with response:
                yield from _parse_openai_stream(response)
            return
        raise LLMError("Groq is still rate-limiting after several retries. Wait a minute and try again.")

    def check(self) -> tuple[bool, str]:
        try:
            text = "".join(c for kind, c in self.stream([{"role": "user", "content": "Reply with OK."}]) if kind == "text")
        except LLMError as exc:
            return False, str(exc)
        return True, f"Connected to Groq ({self.model}). Reply: {text.strip()[:40]}"


def _retry_after(exc: urllib.error.HTTPError, default: float) -> float:
    value = exc.headers.get("retry-after") if exc.headers else None
    try:
        return max(1.0, min(float(value), 60.0)) if value else default
    except ValueError:
        return default


def _parse_openai_stream(response: Any) -> Iterator[Event]:
    usage = None
    for raw in response:
        line = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else str(raw).strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices") or []:
            text = (choice.get("delta") or {}).get("content")
            if text:
                yield ("text", text)
        usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage") or usage
    if usage:
        yield ("usage", {"prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")})


@dataclass
class OllamaProvider:
    model: str = "qwen2.5-coder:7b"
    host: str = "http://localhost:11434"
    temperature: float = 0.2
    num_ctx: int = 8192
    timeout: float = 600.0
    opener: Opener = _open
    name: str = "ollama"

    def installed_models(self) -> list[str]:
        try:
            with self.opener(urllib.request.Request(self.host.rstrip("/") + "/api/tags"), 5) as r:
                data = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise LLMError(
                "Ollama is not running. Start the Ollama app (or run 'ollama serve') and try again."
            ) from exc
        return [m.get("name", "") for m in data.get("models", [])]

    def stream(self, messages: list[Message]) -> Iterator[Event]:
        models = self.installed_models()
        if not any(m == self.model or m.split(":")[0] == self.model for m in models):
            raise LLMError(
                f"The local model '{self.model}' is not downloaded yet. Run Setup.bat again, "
                f"or open a terminal and run: ollama pull {self.model}"
            )
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        request = urllib.request.Request(
            self.host.rstrip("/") + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = self.opener(request, self.timeout)
        except urllib.error.HTTPError as exc:
            raise LLMError(f"Ollama error {exc.code}: {_read_error(exc)}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"Could not reach Ollama at {self.host}: {exc.reason}") from exc
        with response:
            for raw in response:
                line = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else str(raw).strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    raise LLMError(f"Ollama: {chunk['error']}")
                text = (chunk.get("message") or {}).get("content")
                if text:
                    yield ("text", text)
                if chunk.get("done"):
                    yield ("usage", {"prompt_tokens": chunk.get("prompt_eval_count"),
                                     "completion_tokens": chunk.get("eval_count")})

    def check(self) -> tuple[bool, str]:
        try:
            models = self.installed_models()
        except LLMError as exc:
            return False, str(exc)
        if not any(m == self.model or m.split(":")[0] == self.model for m in models):
            return False, f"Ollama is running, but '{self.model}' is not downloaded. Installed: {', '.join(models) or 'none'}"
        return True, f"Ollama is running and '{self.model}' is ready."


@dataclass
class ScriptedProvider:
    """Pre-written replies, for automatic tests only. Not a language model."""

    replies: list[str] | Callable[[list[Message]], str]
    model: str = "scripted"
    name: str = "scripted"
    calls: list[list[Message]] = field(default_factory=list)

    def stream(self, messages: list[Message]) -> Iterator[Event]:
        self.calls.append(messages)
        text = self.replies(messages) if callable(self.replies) else self.replies.pop(0)
        for i in range(0, len(text), 40):
            yield ("text", text[i:i + 40])

    def check(self) -> tuple[bool, str]:
        return True, "scripted"


def make_provider(settings: dict[str, Any]):
    if settings.get("provider") == "ollama":
        return OllamaProvider(
            model=settings.get("ollama_model") or "qwen2.5-coder:7b",
            host=settings.get("ollama_host") or "http://localhost:11434",
            temperature=float(settings.get("temperature", 0.2)),
        )
    return GroqProvider(
        api_key=settings.get("groq_api_key", ""),
        model=settings.get("groq_model") or "openai/gpt-oss-120b",
        temperature=float(settings.get("temperature", 0.2)),
    )
