"""Tests for the OpenAI provider in the LLM abstraction.

We patch ``urllib.request.urlopen`` so the tests are pure-Python and never
contact the real OpenAI API.
"""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import urllib.error

from backend.services.advanced_rag_service import AdvancedRAGService
from backend.services.llm_provider import (
    LLMProviderError,
    MockLLMProvider,
    OllamaProvider,
    OpenAIProvider,
    _redact,
    get_llm_provider,
)


SAMPLE_KEY = "sk-thisIsAFakeTestKey1234567890"


def _ok_response(payload: dict[str, Any]) -> MagicMock:
    """Build a context-manager mock that mimics urllib's HTTPResponse."""
    body = json.dumps(payload).encode("utf-8")
    response = MagicMock()
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _http_error(status: int, body: dict[str, Any]) -> urllib.error.HTTPError:
    payload = json.dumps(body).encode("utf-8")
    fp = io.BytesIO(payload)
    err = urllib.error.HTTPError(
        url="https://api.openai.com/v1/chat/completions",
        code=status,
        msg="Error",
        hdrs=None,  # type: ignore[arg-type]
        fp=fp,
    )
    return err


# ---------- Provider direct tests ----------


def test_openai_provider_success_returns_llm_response() -> None:
    provider = OpenAIProvider(api_key=SAMPLE_KEY, model="gpt-4o-mini")
    fake_body = {
        "choices": [
            {"message": {"role": "assistant", "content": "  Voici le rapport.  "}}
        ]
    }
    with patch("urllib.request.urlopen", return_value=_ok_response(fake_body)):
        result = provider.complete(system="sys", user="user prompt")

    assert result.text == "Voici le rapport."
    assert result.provider == "openai"
    assert result.model == "gpt-4o-mini"
    assert result.used_fallback is False


def test_openai_provider_401_raises_redacted_error() -> None:
    provider = OpenAIProvider(api_key=SAMPLE_KEY)
    # Simulate the worst case: the response body echoes the FULL API key.
    # Our defence layer must strip it before the message reaches logs / UI.
    body = {
        "error": {
            "message": f"Incorrect API key provided: {SAMPLE_KEY}.",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        }
    }
    with patch("urllib.request.urlopen", side_effect=_http_error(401, body)):
        with pytest.raises(LLMProviderError) as exc_info:
            provider.complete(system="sys", user="user")

    message = str(exc_info.value)
    assert "Clé API invalide" in message
    # The literal API key MUST NOT leak in the raised error.
    assert SAMPLE_KEY not in message
    # And the masked marker takes its place.
    assert "<API_KEY>" in message


def test_openai_provider_429_rate_limit_surfaces_clear_message() -> None:
    provider = OpenAIProvider(api_key=SAMPLE_KEY)
    body = {"error": {"message": "Rate limit reached", "type": "requests"}}
    with patch("urllib.request.urlopen", side_effect=_http_error(429, body)):
        with pytest.raises(LLMProviderError) as exc_info:
            provider.complete(system="sys", user="user")
    message = str(exc_info.value)
    assert "Quota" in message or "rate limit" in message.lower()
    assert "Rate limit" in message


def test_openai_provider_network_error_raises_provider_error() -> None:
    provider = OpenAIProvider(api_key=SAMPLE_KEY)
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("Name or service not known"),
    ):
        with pytest.raises(LLMProviderError) as exc_info:
            provider.complete(system="sys", user="user")
    assert "Connexion au LLM impossible" in str(exc_info.value)


def test_openai_provider_empty_completion_raises() -> None:
    provider = OpenAIProvider(api_key=SAMPLE_KEY, model="gpt-4o-mini")
    fake_body = {"choices": [{"message": {"role": "assistant", "content": ""}}]}
    with patch("urllib.request.urlopen", return_value=_ok_response(fake_body)):
        with pytest.raises(LLMProviderError) as exc_info:
            provider.complete(system="sys", user="user")
    assert "empty completion" in str(exc_info.value).lower()


def test_openai_provider_sends_correct_payload_and_headers() -> None:
    provider = OpenAIProvider(
        api_key=SAMPLE_KEY,
        model="gpt-4o-mini",
        max_tokens=500,
        temperature=0.3,
    )
    fake_body = {"choices": [{"message": {"content": "ok"}}]}
    with patch("urllib.request.urlopen", return_value=_ok_response(fake_body)) as mock_urlopen:
        provider.complete(system="SYS", user="USER")

    request = mock_urlopen.call_args.args[0]
    body = json.loads(request.data.decode("utf-8"))
    assert body["model"] == "gpt-4o-mini"
    assert body["max_tokens"] == 500
    assert body["temperature"] == 0.3
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    assert body["messages"][1] == {"role": "user", "content": "USER"}
    # Authorization header carries the key — that's normal — but we want to
    # confirm there's no other accidental copy of it in the body.
    assert SAMPLE_KEY not in request.data.decode("utf-8")


# ---------- Redaction helper ----------


def test_redact_strips_literal_api_key() -> None:
    msg = f"HTTP 401: Authorization: Bearer {SAMPLE_KEY} was rejected"
    safe = _redact(msg, SAMPLE_KEY)
    assert SAMPLE_KEY not in safe
    assert "<API_KEY>" in safe


def test_redact_masks_sk_pattern_even_without_known_key() -> None:
    msg = "Body contained sk-1234567890abcdefABCDEFGHIJKLMN from another env."
    safe = _redact(msg, None)
    assert "sk-1234567890" not in safe
    assert "<API_KEY>" in safe


def test_redact_passthrough_when_nothing_sensitive() -> None:
    assert _redact("plain message", None) == "plain message"
    assert _redact("", None) == ""


# ---------- Factory: openai selection & fallbacks ----------


def test_get_llm_provider_selects_openai_when_explicit_and_key_present(
    monkeypatch,
) -> None:
    from backend.core import config as config_module

    monkeypatch.setattr(
        config_module.settings, "ADVANCED_RAG_ENABLED", True, raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "ADVANCED_RAG_PROVIDER", "openai", raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "ADVANCED_RAG_API_KEY", SAMPLE_KEY, raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "ADVANCED_RAG_MODEL", "gpt-4o-mini", raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "ADVANCED_RAG_BASE_URL", "", raising=False
    )

    provider = get_llm_provider()
    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "gpt-4o-mini"
    assert provider.api_key == SAMPLE_KEY


def test_get_llm_provider_falls_back_to_mock_when_openai_key_missing(
    monkeypatch,
) -> None:
    from backend.core import config as config_module

    monkeypatch.setattr(
        config_module.settings, "ADVANCED_RAG_ENABLED", True, raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "ADVANCED_RAG_PROVIDER", "openai", raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "ADVANCED_RAG_API_KEY", "", raising=False
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    provider = get_llm_provider()
    assert isinstance(provider, MockLLMProvider)


def test_get_llm_provider_falls_back_to_mock_when_ollama_unreachable(
    monkeypatch,
) -> None:
    from backend.core import config as config_module

    monkeypatch.setattr(
        config_module.settings, "ADVANCED_RAG_ENABLED", True, raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "ADVANCED_RAG_PROVIDER", "ollama", raising=False
    )
    monkeypatch.setattr(
        config_module.settings,
        "ADVANCED_RAG_BASE_URL",
        "http://localhost:11434",
        raising=False,
    )

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        provider = get_llm_provider()

    assert isinstance(provider, MockLLMProvider)


def test_get_llm_provider_selects_ollama_when_reachable(monkeypatch) -> None:
    from backend.core import config as config_module

    monkeypatch.setattr(
        config_module.settings, "ADVANCED_RAG_ENABLED", True, raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "ADVANCED_RAG_PROVIDER", "ollama", raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "ADVANCED_RAG_MODEL", "llama3.2:3b", raising=False
    )
    monkeypatch.setattr(
        config_module.settings,
        "ADVANCED_RAG_BASE_URL",
        "http://localhost:11434",
        raising=False,
    )

    with patch("urllib.request.urlopen", return_value=_ok_response({"models": []})):
        provider = get_llm_provider()

    assert isinstance(provider, OllamaProvider)
    assert provider.model == "llama3.2:3b"


# ---------- End-to-end via AdvancedRAGService ----------


def _basic_analysis() -> dict[str, Any]:
    return {
        "scenario_id": "S-1",
        "document_stats": {"original_filename": "demo.pdf", "words_count": 800, "chunks_count": 6},
        "rag_report": {"risk_level": "medium"},
        "plagiarism": {
            "global_similarity_score": 0.5,
            "total_matches": 1,
            "total_sources": 1,
            "matches": [
                {
                    "similarity_score": 0.5,
                    "matched_chunk_text_display": "passage source",
                    "matched_chunk_text": "passage source",
                    "chunk_text": "passage analysé",
                    "overlap_text": "passage",
                    "matched_scenario_id": "S-other",
                    "stored_filename": "other.pdf",
                    "filename": "other.pdf",
                    "original_filename": "other.pdf",
                }
            ],
            "plagiarism_sources": [],
        },
        "profanity": {"profanity_score": 0.0, "detected_words": []},
        "adult_content": {"adult_content_score": 0.0, "risk_level": "low"},
    }


def test_advanced_rag_marks_provider_openai_on_success() -> None:
    provider = OpenAIProvider(api_key=SAMPLE_KEY, model="gpt-4o-mini")
    service = AdvancedRAGService(llm_provider=provider)
    fake_body = {
        "choices": [
            {"message": {"content": "Une narrative produite par OpenAI."}}
        ]
    }
    with patch("urllib.request.urlopen", return_value=_ok_response(fake_body)):
        report = service.generate(
            analysis=_basic_analysis(), scenario_id="S-1"
        )

    assert report["llm"]["provider"] == "openai"
    assert report["llm"]["model"] == "gpt-4o-mini"
    assert report["llm"]["used_fallback"] is False
    assert report["llm"]["error"] is None
    assert report["narrative"] == "Une narrative produite par OpenAI."

    # Defence: serialised report MUST NOT contain the API key anywhere.
    serialised = json.dumps(report)
    assert SAMPLE_KEY not in serialised


def test_advanced_rag_falls_back_when_openai_raises() -> None:
    provider = OpenAIProvider(api_key=SAMPLE_KEY)
    service = AdvancedRAGService(llm_provider=provider)
    body = {
        "error": {
            "message": "You exceeded your current quota",
            "type": "insufficient_quota",
        }
    }
    with patch("urllib.request.urlopen", side_effect=_http_error(429, body)):
        report = service.generate(
            analysis=_basic_analysis(), scenario_id="S-1"
        )

    assert report["llm"]["used_fallback"] is True
    assert report["llm"]["provider"] == "mock"
    assert report["llm"]["model"] == "deterministic-template"
    assert "Quota" in (report["llm"]["error"] or "")
    # Fallback narrative still has the canonical sections.
    assert "Synthèse globale" in report["narrative"]
    # API key never leaks.
    assert SAMPLE_KEY not in json.dumps(report)


def test_advanced_rag_payload_never_contains_api_key_after_redaction() -> None:
    """Even if the upstream error message contained the literal key, the
    final report's ``llm.error`` must be redacted."""
    provider = OpenAIProvider(api_key=SAMPLE_KEY)
    service = AdvancedRAGService(llm_provider=provider)
    body = {
        "error": {
            "message": f"Incorrect API key provided: {SAMPLE_KEY}.",
            "type": "invalid_request_error",
        }
    }
    with patch("urllib.request.urlopen", side_effect=_http_error(401, body)):
        report = service.generate(
            analysis=_basic_analysis(), scenario_id="S-1"
        )

    serialised = json.dumps(report)
    assert SAMPLE_KEY not in serialised
    assert report["llm"]["used_fallback"] is True
    assert "Clé API invalide" in (report["llm"]["error"] or "")
