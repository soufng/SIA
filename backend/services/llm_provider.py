"""Pluggable LLM provider for the advanced RAG layer.

This module is intentionally additive: nothing else in the project depends on
it. It exposes a single :class:`LLMProvider` protocol and a factory that
returns the best available provider given the current settings, falling back
to a deterministic mock provider when no external LLM is reachable.

The mock provider is what guarantees the advanced RAG endpoint always returns
a useful answer — even on a developer laptop with no API key configured.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from backend.core.config import settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMResponse:
    """Result of a single LLM completion call."""

    text: str
    provider: str
    model: str
    used_fallback: bool = False


class LLMProviderError(RuntimeError):
    """Raised by providers when a completion cannot be obtained.

    The exception message is *always* safe to surface to API responses and
    UI: it never contains API keys, request headers, or full request bodies.
    Providers use :func:`_redact` to scrub any potentially sensitive
    substring before raising.
    """


class LLMProvider(Protocol):
    """Minimal interface every provider must implement."""

    name: str
    model: str

    def complete(self, system: str, user: str) -> LLMResponse:  # pragma: no cover
        ...


class MockLLMProvider:
    """Deterministic fallback that lets the advanced RAG keep working.

    It returns the *user* prompt prefixed with a clear marker so callers
    (and tests) can recognise when no real LLM was used.
    """

    name = "mock"
    model = "deterministic-template"

    def complete(self, system: str, user: str) -> LLMResponse:
        # The advanced RAG service supplies a pre-rendered template in `user`
        # when it wants the mock provider to surface a polished narrative.
        return LLMResponse(
            text=user,
            provider=self.name,
            model=self.model,
            used_fallback=True,
        )


class OpenAIProvider:
    """Chat-completions backed provider using the public OpenAI HTTP API."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com",
        max_tokens: int = 1200,
        temperature: float = 0.2,
        timeout: int = 45,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout

    def complete(self, system: str, user: str) -> LLMResponse:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            data = _http_post_json(
                f"{self.base_url}/v1/chat/completions",
                payload,
                headers,
                timeout=self.timeout,
                sensitive_substring=self.api_key,
            )
        except LLMProviderError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise LLMProviderError(_redact(str(exc), self.api_key)) from None

        text = (
            (data.get("choices") or [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not text:
            # Surface a useful error rather than a silent empty narrative.
            raise LLMProviderError(
                f"OpenAI returned an empty completion (model={self.model})."
            )
        return LLMResponse(
            text=text.strip(),
            provider=self.name,
            model=self.model,
        )


class AnthropicProvider:
    """Messages-API backed provider using the Anthropic HTTP API."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-haiku-latest",
        base_url: str = "https://api.anthropic.com",
        max_tokens: int = 1200,
        temperature: float = 0.2,
        timeout: int = 45,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout

    def complete(self, system: str, user: str) -> LLMResponse:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        try:
            data = _http_post_json(
                f"{self.base_url}/v1/messages",
                payload,
                headers,
                timeout=self.timeout,
                sensitive_substring=self.api_key,
            )
        except LLMProviderError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise LLMProviderError(_redact(str(exc), self.api_key)) from None

        chunks = data.get("content") or []
        text = "".join(
            block.get("text", "")
            for block in chunks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text:
            raise LLMProviderError(
                f"Anthropic returned an empty completion (model={self.model})."
            )
        return LLMResponse(
            text=text.strip(),
            provider=self.name,
            model=self.model,
        )


class OllamaProvider:
    """Local provider for any Ollama-compatible runtime."""

    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2",
        max_tokens: int = 1200,
        temperature: float = 0.2,
        timeout: int = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout

    def complete(self, system: str, user: str) -> LLMResponse:
        payload = {
            "model": self.model,
            "stream": False,
            "options": {
                "num_predict": self.max_tokens,
                "temperature": self.temperature,
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            data = _http_post_json(
                f"{self.base_url}/api/chat",
                payload,
                {"Content-Type": "application/json"},
                timeout=self.timeout,
            )
        except LLMProviderError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise LLMProviderError(str(exc)) from None

        text = (data.get("message") or {}).get("content", "")
        if not text:
            raise LLMProviderError(
                f"Ollama returned an empty completion (model={self.model})."
            )
        return LLMResponse(
            text=text.strip(),
            provider=self.name,
            model=self.model,
        )


# ---------- Factory & helpers ----------


def get_llm_provider() -> LLMProvider:
    """Return the best LLM provider available given the current settings.

    Selection order when ``ADVANCED_RAG_PROVIDER == "auto"``:
      1. OpenAI if ``ADVANCED_RAG_API_KEY`` or ``OPENAI_API_KEY`` is set
      2. Anthropic if ``ANTHROPIC_API_KEY`` is set
      3. Ollama if a base URL is reachable (or explicitly configured)
      4. The :class:`MockLLMProvider` fallback
    """
    if not settings.ADVANCED_RAG_ENABLED:
        return MockLLMProvider()

    provider_name = (settings.ADVANCED_RAG_PROVIDER or "auto").lower().strip()
    api_key = settings.ADVANCED_RAG_API_KEY or os.environ.get("OPENAI_API_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    model = settings.ADVANCED_RAG_MODEL.strip()
    base_url = settings.ADVANCED_RAG_BASE_URL.strip()
    max_tokens = settings.ADVANCED_RAG_MAX_TOKENS
    temperature = settings.ADVANCED_RAG_TEMPERATURE
    timeout = settings.ADVANCED_RAG_TIMEOUT_SECONDS

    candidates: list[str] = []
    if provider_name == "auto":
        if api_key:
            candidates.append("openai")
        if anthropic_key:
            candidates.append("anthropic")
        if base_url or os.environ.get("OLLAMA_HOST"):
            candidates.append("ollama")
        candidates.append("mock")
    else:
        candidates = [provider_name]
        if provider_name == "ollama":
            candidates.append("mock")

    for candidate in candidates:
        try:
            if candidate == "openai" and api_key:
                return OpenAIProvider(
                    api_key=api_key,
                    model=model or "gpt-4o-mini",
                    base_url=base_url or "https://api.openai.com",
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                )
            if candidate == "anthropic" and (anthropic_key or api_key):
                return AnthropicProvider(
                    api_key=anthropic_key or api_key,
                    model=model or "claude-3-5-haiku-latest",
                    base_url=base_url or "https://api.anthropic.com",
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                )
            if candidate == "ollama":
                ollama_base_url = (
                    base_url
                    or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
                )
                if not _is_ollama_reachable(ollama_base_url):
                    logger.warning(
                        "Ollama is configured but unreachable at %s; "
                        "using deterministic fallback.",
                        ollama_base_url,
                    )
                    continue
                return OllamaProvider(
                    base_url=ollama_base_url,
                    model=model or "llama3.2",
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                )
            if candidate in {"mock", "none"}:
                return MockLLMProvider()
        except Exception as exc:  # pragma: no cover - defensive only
            logger.warning("LLM provider %s init failed: %s", candidate, exc)

    return MockLLMProvider()


def _is_ollama_reachable(base_url: str, timeout: float = 1.0) -> bool:
    """Return True when an Ollama-compatible server is accepting requests.

    This is deliberately a very short preflight. If Ollama is not running,
    we prefer the deterministic fallback immediately instead of surfacing a
    noisy connection-refused error in the generated report.
    """
    url = f"{base_url.rstrip('/')}/api/tags"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            response.read(1)
        return True
    except (
        TimeoutError,
        urllib.error.URLError,
        OSError,
        socket.timeout,
    ):
        return False


def _http_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
    sensitive_substring: str | None = None,
) -> dict[str, Any]:
    """POST JSON and return parsed body.

    Raises :class:`LLMProviderError` with a *redacted, human-readable* message
    on any failure. The original ``HTTPError`` body is parsed so callers can
    surface a precise reason (401 invalid_api_key, 429 rate_limit_exceeded,
    timeouts, DNS failures, etc.) without leaking the API key.
    """
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            body_bytes = exc.read() or b""
        except Exception:  # pragma: no cover - defensive
            body_bytes = b""
        raw_body = body_bytes.decode("utf-8", errors="replace")
        message = _extract_provider_error_message(exc.code, raw_body, url)
        raise LLMProviderError(_redact(message, sensitive_substring)) from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise LLMProviderError(
            _redact(
                f"Connexion au LLM impossible ({reason}).", sensitive_substring
            )
        ) from None
    except TimeoutError as exc:
        raise LLMProviderError(
            _redact(
                f"Timeout après {timeout}s en attendant la réponse du LLM.",
                sensitive_substring,
            )
        ) from None

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        snippet = body[:200].replace("\n", " ")
        raise LLMProviderError(
            f"Réponse LLM non-JSON ({exc.msg}) : {snippet!r}"
        ) from None


def _extract_provider_error_message(
    status_code: int, raw_body: str, url: str
) -> str:
    """Map an HTTP error body (OpenAI / Anthropic / Ollama shapes) to a
    short, human-readable French message."""
    detail: str | None = None
    try:
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        body = {}

    if isinstance(body, dict):
        # OpenAI: {"error": {"message": "...", "type": "...", "code": "..."}}
        err = body.get("error")
        if isinstance(err, dict):
            detail = err.get("message") or err.get("code") or err.get("type")
        elif isinstance(err, str):
            detail = err
        # Anthropic also uses {"error": {"type": "...", "message": "..."}}
        # Ollama returns {"error": "model not found"}.

    code_hint = {
        400: "Requête invalide",
        401: "Clé API invalide ou absente",
        403: "Accès refusé (permissions ou organisation)",
        404: "Endpoint ou modèle introuvable",
        408: "Le LLM a dépassé son délai de réponse",
        409: "Conflit côté LLM",
        413: "Prompt trop volumineux pour ce modèle",
        422: "Paramètres rejetés par le LLM",
        429: "Quota / rate limit dépassé",
        500: "Erreur interne du fournisseur LLM",
        502: "Mauvaise passerelle vers le LLM",
        503: "Service LLM indisponible",
        504: "Le fournisseur LLM a dépassé son timeout",
    }.get(status_code, f"HTTP {status_code}")

    if detail:
        return f"{code_hint} : {detail}"
    return f"{code_hint} (aucun détail renvoyé par {_safe_url(url)})."


def _safe_url(url: str) -> str:
    """Return a host:path display without query strings (for error messages)."""
    if "?" in url:
        return url.split("?", 1)[0]
    return url


# Pattern matching anything that looks like a bearer token or sk-... key.
_BEARER_RE = re.compile(r"(Bearer\s+)?(sk-[A-Za-z0-9_-]{16,})", re.IGNORECASE)


def _redact(text: str, sensitive_substring: str | None) -> str:
    """Mask API keys in error strings before they reach logs / API responses.

    Two defences are layered:
      1. Strip any literal occurrence of ``sensitive_substring`` (the actual
         configured API key).
      2. Mask anything matching the ``sk-…`` / ``Bearer …`` shape, so even an
         unrelated key embedded in a response body never leaks.
    """
    safe = text or ""
    if sensitive_substring and len(sensitive_substring) >= 8:
        # Replace the literal key with a masked marker.
        safe = safe.replace(sensitive_substring, "<API_KEY>")
        # Defence in depth: shorter prefixes.
        if len(sensitive_substring) >= 12:
            safe = safe.replace(sensitive_substring[:12], "<API_KEY>")
    return _BEARER_RE.sub(lambda m: f"{m.group(1) or ''}<API_KEY>", safe)
