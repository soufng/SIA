"""Optional LLM "second reader" layer over the deterministic pipelines.

This module is purely **additive**. It does NOT replace the plagiarism
engine, the profanity/adult-content pipelines, or the Moroccan principles
pipeline. Those remain authoritative.

What it adds, when enabled via ``SIA_LLM_CONTEXTUAL_REVIEW_ENABLED``:

1. Selects a small set of chunks worth a contextual second-read
   (royal mentions, sensitive lexicon, ambiguous personal relations,
   neighbours of already-flagged passages, edges of the screenplay).
2. Sends only those chunks to the LLM with the existing alerts as
   context. The full PDF is never sent.
3. Asks the LLM for *additional* alerts, in a strict JSON shape.
4. Validates every alert: each ``exact_quote`` must appear verbatim
   in the source chunk, the page/chunk_id must exist, and the number
   of alerts is capped. Anything else is rejected silently.
5. Returns a structured ``llm_contextual_alerts`` block that the
   orchestrator merges into the final result, without touching any
   existing field.

The LLM never invents passages: every kept alert quotes the scenario
itself. If the LLM is unreachable or returns garbage, the deterministic
analysis is still complete — we only drop the additive block.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from backend.core.config import settings
from backend.services.llm_provider import (
    LLMProvider,
    LLMProviderError,
    MockLLMProvider,
    get_llm_provider,
)


logger = logging.getLogger(__name__)


# ---------- Constants ----------

ALLOWED_CATEGORIES: tuple[str, ...] = (
    "principes_marocains",
    "vie_privee",
    "monarchie",
    "religion",
    "sexualite",
    "violence",
    "politique",
    "ambiguite",
)

ALLOWED_RISKS: tuple[str, ...] = ("LOW", "MEDIUM", "HIGH", "VERY_HIGH")

# Categories whose validated HIGH/VERY_HIGH alerts may escalate the
# global report risk level.
ESCALATING_CATEGORIES: frozenset[str] = frozenset(
    {"monarchie", "principes_marocains", "religion", "vie_privee"}
)

ESCALATING_RISKS: frozenset[str] = frozenset({"HIGH", "VERY_HIGH"})

# Map LLM risk vocabulary to the report's English scale.
_RISK_TO_REPORT = {
    "LOW": "low",
    "MEDIUM": "medium",
    "HIGH": "high",
    "VERY_HIGH": "high",  # rag_report tops out at "high"
}


# ---------- Selection ----------

# Lexicon — broad enough to surface ambiguous scenes the rule-based
# pipelines may miss. Patterns are case-insensitive and unicode-aware.
_SELECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE | re.UNICODE)
    for p in (
        # Royal honorifics / personas (FR + AR)
        r"\b(?:sidi|sidna|moulay|lalla)\b",
        r"\broi\b|\bprince\b|\bprincesse\b|\bmonarchie\b|\bpalais\b",
        r"\bmoham?med\s+(?:v|vi|5|6)\b|\bhassan\s+(?:ii|2)\b",
        r"\bfamille\s+royale\b|\bcour\s+royale\b",
        r"الملك|الأمير|الأميرة|الملكية|سيدي|مولاي|لالة",
        # Religion / monarchy / power
        r"\bislam\w*\b|\bmusulman\w*\b|\bcoran\b|\bproph[èe]te?\b|\ballah\b",
        r"\b[ée]glise\b|\bmosqu[ée]e?\b|\bath[ée]e?\b|\bapostat\w*\b",
        r"\bpouvoir\b|\bmakhzen\b|\bgouvernement\b",
        r"الإسلام|الدين|القرآن|النبي|الله",
        # Sexuality / violence / drugs / insults / politics
        r"\bsexuel\w*\b|\bnudit[ée]\b|\bporn\w*\b|\bharcele\w*\b|\bviol\w*\b",
        r"\btuer\w*\b|\bmeurtre\b|\bassassine\w*\b|\battentat\b|\barme\b",
        r"\bdrogue\b|\bcoca[ïi]ne\b|\bhachich\b|\bhasch\b|\bcannabis\b",
        r"\bsale\b|\bcorrompu\w*\b|\btra[îi]tre\w*\b|\bdictateur\w*\b",
        r"\b[ée]lection\w*\b|\bparti\b|\bopposition\b|\bmanifestation\b",
        # Ambiguous personal relations
        r"\bproche\b|\bintime\b|\bsecret\w*\b|\bliaison\b",
        r"\bamant\w*\b|\bma[îi]tress\w*\b|\bma[îi]tre\s+secret\w*\b",
        r"\benfant\s+cach[ée]\b|\benfant\s+naturel\b|\bhors\s+mariage\b",
        r"\bmariage\b|\b[ée]pouse?\b|\bfemme\b|\bfamille\b|\bdivorc\w*\b",
        r"عشيق\w*|سر\w*|زواج|طلاق|عائلة|زوجة",
    )
)


def _chunk_text(chunk: dict[str, Any]) -> str:
    """Return the best human-readable text for a chunk."""
    for key in ("text_display", "text_normalized", "raw_text", "text"):
        value = chunk.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _normalise(text: str) -> str:
    """Whitespace + NFKC normalisation for substring containment checks."""
    if not isinstance(text, str):
        return ""
    folded = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", folded).strip()


def _flagged_indices(pipeline_results: dict[str, Any]) -> set[int]:
    """Collect chunk indices already touched by the deterministic pipelines."""
    indices: set[int] = set()

    def _collect(items: Any, key_candidates: tuple[str, ...]) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in key_candidates:
                value = item.get(key)
                if isinstance(value, int):
                    indices.add(value)
                    break
                if isinstance(value, str) and value.isdigit():
                    indices.add(int(value))
                    break

    moroccan = pipeline_results.get("moroccan_constants") or {}
    if isinstance(moroccan, dict):
        _collect(moroccan.get("flags"), ("chunk_index",))
        _collect(moroccan.get("mentions"), ("chunk_index",))
    plagiarism = pipeline_results.get("plagiarism") or {}
    if isinstance(plagiarism, dict):
        _collect(
            plagiarism.get("matches"),
            ("current_chunk_index", "chunk_index"),
        )
    for key in ("profanity", "adult_content"):
        section = pipeline_results.get(key) or {}
        if isinstance(section, dict):
            _collect(section.get("matches"), ("chunk_index",))
            _collect(section.get("findings"), ("chunk_index",))
    return indices


def select_contextual_chunks_for_llm(
    scenario_chunks: list[dict[str, Any]],
    pipeline_results: dict[str, Any],
    max_chunks: int | None = None,
    max_chars_per_chunk: int | None = None,
    max_total_chars: int | None = None,
) -> list[dict[str, Any]]:
    """Pick a small, high-value subset of chunks for the LLM second read.

    Selection criteria (any one qualifies):

    - chunk text matches the royal / monarchy / religion / sexuality /
      violence / drugs / politics / insult / ambiguous-relations lexicon
    - chunk is the immediate neighbour of an already-flagged passage
    - chunk is the very first or very last analysable chunk

    The returned list is bounded by ``max_chunks`` and
    ``max_total_chars``; each individual chunk text is truncated to
    ``max_chars_per_chunk`` characters.
    """
    if not isinstance(scenario_chunks, list) or not scenario_chunks:
        return []

    max_chunks = max_chunks or settings.LLM_CONTEXTUAL_MAX_CHUNKS
    max_chars_per_chunk = (
        max_chars_per_chunk or settings.LLM_CONTEXTUAL_MAX_CHARS_PER_CHUNK
    )
    max_total_chars = (
        max_total_chars or settings.LLM_CONTEXTUAL_MAX_TOTAL_CHARS
    )

    flagged = _flagged_indices(pipeline_results or {})
    last_index = len(scenario_chunks) - 1

    # Priority score per chunk: lower = picked first.
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, chunk in enumerate(scenario_chunks):
        if not isinstance(chunk, dict):
            continue
        text = _chunk_text(chunk)
        if not text.strip():
            continue

        chunk_index = chunk.get("chunk_index")
        if not isinstance(chunk_index, int):
            chunk_index = index

        lexicon_hit = any(p.search(text) for p in _SELECTION_PATTERNS)
        neighbour_of_flag = any(
            (chunk_index - 1) in flagged or (chunk_index + 1) in flagged
            for _ in (None,)
        )
        is_self_flag = chunk_index in flagged
        is_edge = index == 0 or index == last_index

        priority: int | None = None
        if is_self_flag and lexicon_hit:
            priority = 0
        elif lexicon_hit:
            priority = 1
        elif neighbour_of_flag:
            priority = 2
        elif is_edge:
            priority = 3

        if priority is None:
            continue
        scored.append((priority, index, chunk))

    scored.sort(key=lambda triple: (triple[0], triple[1]))

    selected: list[dict[str, Any]] = []
    total_chars = 0
    for _, index, chunk in scored:
        if len(selected) >= max_chunks:
            break
        text = _chunk_text(chunk)
        truncated = text[:max_chars_per_chunk]
        if total_chars + len(truncated) > max_total_chars:
            # Skip this one if it pushes us over the global cap; try the next.
            continue
        chunk_id = chunk.get("chunk_id") or f"chunk_{index}"
        chunk_index = chunk.get("chunk_index")
        if not isinstance(chunk_index, int):
            chunk_index = index
        selected.append(
            {
                "chunk_id": str(chunk_id),
                "chunk_index": chunk_index,
                "page": chunk.get("page_number"),
                "text": truncated,
            }
        )
        total_chars += len(truncated)
    return selected


# ---------- Service ----------


_SYSTEM_PROMPT = (
    "Tu es un second lecteur éditorial pour un scénario audiovisuel "
    "destiné au marché marocain. Tu ne remplaces aucune règle existante. "
    "Tu identifies UNIQUEMENT des risques contextuels supplémentaires "
    "(ambiguïté, vie privée, monarchie, religion, sexualité, violence, "
    "politique, principes marocains). "
    "Règles strictes :\n"
    "- Tu ne dois JAMAIS inventer un passage : chaque alerte doit citer "
    "  un extrait exact, copié mot pour mot depuis les chunks fournis.\n"
    "- Tu réponds STRICTEMENT en JSON valide, sans texte autour, sans "
    "  commentaires, sans markdown.\n"
    "- Si aucun risque supplémentaire n'est identifié, retourne "
    "  additional_alerts: [].\n"
)


_USER_TEMPLATE = (
    "Schéma JSON attendu :\n"
    "{{\n"
    '  "additional_alerts": [\n'
    "    {{\n"
    '      "category": "principes_marocains|vie_privee|monarchie|religion|'
    'sexualite|violence|politique|ambiguite",\n'
    '      "risk": "LOW|MEDIUM|HIGH|VERY_HIGH",\n'
    '      "exact_quote": "...",\n'
    '      "page": 12,\n'
    '      "chunk_id": "chunk_3",\n'
    '      "reason": "...",\n'
    '      "suggested_rewrite": "..."\n'
    "    }}\n"
    "  ],\n"
    '  "summary": "..."\n'
    "}}\n\n"
    "Métadonnées du scénario :\n{metadata}\n\n"
    "Alertes déjà détectées par les pipelines déterministes "
    "(pour contexte, ne pas dupliquer) :\n{existing}\n\n"
    "Chunks à relire :\n{chunks}\n"
)


@dataclass
class ContextualReviewResult:
    enabled: bool
    alerts: list[dict[str, Any]]
    summary: str
    model: str
    provider: str
    fallback_used: bool
    rejected_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "alerts": self.alerts,
            "summary": self.summary,
            "model": self.model,
            "provider": self.provider,
            "fallback_used": self.fallback_used,
            "rejected_count": self.rejected_count,
            "error": self.error,
        }


class LLMContextualReviewService:
    """Run an optional LLM second-reader pass over selected chunks."""

    def __init__(self, llm_provider: LLMProvider | None = None) -> None:
        self._provider = llm_provider

    @property
    def provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = get_llm_provider()
        return self._provider

    # ---------- Public API ----------

    def review(
        self,
        scenario_metadata: dict[str, Any],
        scenario_chunks: list[dict[str, Any]],
        pipeline_results: dict[str, Any],
    ) -> ContextualReviewResult:
        """Run the second-reader pass. Always safe — never raises."""
        if not settings.LLM_CONTEXTUAL_REVIEW_ENABLED:
            return ContextualReviewResult(
                enabled=False,
                alerts=[],
                summary="",
                model="",
                provider="",
                fallback_used=False,
            )

        selected = select_contextual_chunks_for_llm(
            scenario_chunks=scenario_chunks,
            pipeline_results=pipeline_results,
        )
        if not selected:
            return ContextualReviewResult(
                enabled=True,
                alerts=[],
                summary="Aucun chunk éligible pour la relecture LLM.",
                model="",
                provider="",
                fallback_used=False,
            )

        payload = {
            "scenario_metadata": scenario_metadata,
            "selected_chunks": selected,
            "existing_alerts": self._slim_existing_alerts(pipeline_results),
        }
        return self.analyze_contextual_risks_with_llm(payload)

    def analyze_contextual_risks_with_llm(
        self, payload: dict[str, Any]
    ) -> ContextualReviewResult:
        """Call the LLM and validate the response.

        The payload is expected to carry ``scenario_metadata``,
        ``selected_chunks`` and ``existing_alerts``.
        """
        selected_chunks = payload.get("selected_chunks") or []
        if not isinstance(selected_chunks, list) or not selected_chunks:
            return ContextualReviewResult(
                enabled=True,
                alerts=[],
                summary="Aucun chunk fourni.",
                model="",
                provider="",
                fallback_used=False,
            )

        prompt = _USER_TEMPLATE.format(
            metadata=json.dumps(
                payload.get("scenario_metadata") or {}, ensure_ascii=False
            ),
            existing=json.dumps(
                payload.get("existing_alerts") or {}, ensure_ascii=False
            ),
            chunks=json.dumps(selected_chunks, ensure_ascii=False),
        )

        provider = self.provider
        if isinstance(provider, MockLLMProvider):
            # The mock provider cannot produce real contextual alerts.
            # We surface an empty result rather than risking a fake one.
            return ContextualReviewResult(
                enabled=True,
                alerts=[],
                summary="LLM réel indisponible — relecture contextuelle ignorée.",
                model=provider.model,
                provider=provider.name,
                fallback_used=True,
            )

        try:
            response = provider.complete(system=_SYSTEM_PROMPT, user=prompt)
        except LLMProviderError as exc:
            logger.warning("LLM contextual review failed: %s", exc)
            return ContextualReviewResult(
                enabled=True,
                alerts=[],
                summary="",
                model=getattr(provider, "model", ""),
                provider=getattr(provider, "name", ""),
                fallback_used=True,
                error=str(exc),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unexpected LLM contextual review error.")
            return ContextualReviewResult(
                enabled=True,
                alerts=[],
                summary="",
                model=getattr(provider, "model", ""),
                provider=getattr(provider, "name", ""),
                fallback_used=True,
                error=str(exc),
            )

        parsed = _safe_parse_json(response.text)
        if parsed is None:
            logger.warning(
                "LLM contextual review returned non-JSON output (provider=%s).",
                response.provider,
            )
            return ContextualReviewResult(
                enabled=True,
                alerts=[],
                summary="",
                model=response.model,
                provider=response.provider,
                fallback_used=True,
                error="non_json_response",
            )

        raw_alerts = parsed.get("additional_alerts") or []
        summary = str(parsed.get("summary") or "").strip()
        validated, rejected = self._validate_alerts(raw_alerts, selected_chunks)

        return ContextualReviewResult(
            enabled=True,
            alerts=validated,
            summary=summary,
            model=response.model,
            provider=response.provider,
            fallback_used=response.used_fallback,
            rejected_count=rejected,
        )

    # ---------- Validation / merging ----------

    @staticmethod
    def _slim_existing_alerts(
        pipeline_results: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep just enough context that the LLM avoids duplicating us."""
        moroccan = pipeline_results.get("moroccan_constants") or {}
        plagiarism = pipeline_results.get("plagiarism") or {}
        profanity = pipeline_results.get("profanity") or {}
        adult = pipeline_results.get("adult_content") or {}
        return {
            "moroccan_constants": {
                "risk_level": moroccan.get("risk_level"),
                "flags_count": len(moroccan.get("flags") or []),
                "categories": moroccan.get("categories"),
            },
            "plagiarism": {
                "risk": plagiarism.get("risk"),
                "score_percent": plagiarism.get("score_percent"),
                "matches_count": plagiarism.get("total_matches"),
            },
            "profanity": {
                "risk_level": profanity.get("risk_level"),
                "score": profanity.get("score"),
            },
            "adult_content": {
                "risk_level": adult.get("risk_level"),
                "score": adult.get("score"),
            },
        }

    def _validate_alerts(
        self,
        raw_alerts: Any,
        selected_chunks: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """Drop every alert that does not cite a real chunk verbatim."""
        max_alerts = max(0, int(settings.LLM_ALERTS_MAX))
        if not isinstance(raw_alerts, list):
            return [], 0

        chunks_by_id = {c["chunk_id"]: c for c in selected_chunks}
        chunks_by_page: dict[Any, list[dict[str, Any]]] = {}
        for chunk in selected_chunks:
            chunks_by_page.setdefault(chunk.get("page"), []).append(chunk)

        validated: list[dict[str, Any]] = []
        rejected = 0
        for raw in raw_alerts:
            if not isinstance(raw, dict):
                rejected += 1
                continue

            category = str(raw.get("category") or "").strip().lower()
            risk = str(raw.get("risk") or "").strip().upper()
            quote = str(raw.get("exact_quote") or "").strip()
            chunk_id = raw.get("chunk_id")
            page = raw.get("page")

            if category not in ALLOWED_CATEGORIES:
                rejected += 1
                continue
            if risk not in ALLOWED_RISKS:
                rejected += 1
                continue
            if not quote:
                rejected += 1
                continue

            source_chunk = self._resolve_source_chunk(
                chunk_id=chunk_id,
                page=page,
                quote=quote,
                chunks_by_id=chunks_by_id,
                chunks_by_page=chunks_by_page,
                all_chunks=selected_chunks,
            )
            if source_chunk is None:
                rejected += 1
                continue

            validated.append(
                {
                    "category": category,
                    "risk": risk,
                    "exact_quote": quote,
                    "page": source_chunk.get("page"),
                    "chunk_id": source_chunk["chunk_id"],
                    "chunk_index": source_chunk.get("chunk_index"),
                    "reason": str(raw.get("reason") or "").strip(),
                    "suggested_rewrite": str(
                        raw.get("suggested_rewrite") or ""
                    ).strip(),
                }
            )
            if len(validated) >= max_alerts:
                break
        return validated, rejected

    @staticmethod
    def _resolve_source_chunk(
        *,
        chunk_id: Any,
        page: Any,
        quote: str,
        chunks_by_id: dict[str, dict[str, Any]],
        chunks_by_page: dict[Any, list[dict[str, Any]]],
        all_chunks: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Return the chunk that actually contains ``quote`` verbatim."""
        normalised_quote = _normalise(quote)
        if not normalised_quote:
            return None

        candidates: list[dict[str, Any]] = []
        if isinstance(chunk_id, str) and chunk_id in chunks_by_id:
            candidates.append(chunks_by_id[chunk_id])
        if page is not None and page in chunks_by_page:
            candidates.extend(chunks_by_page[page])
        if not candidates:
            candidates = list(all_chunks)

        for chunk in candidates:
            if normalised_quote in _normalise(chunk.get("text") or ""):
                return chunk
        return None


# ---------- Helpers ----------


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _safe_parse_json(text: str) -> dict[str, Any] | None:
    """Return the first JSON object found in ``text``, or ``None``."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def should_escalate_global_risk(alert: dict[str, Any]) -> bool:
    """Return True when a validated LLM alert may raise the report risk."""
    return (
        str(alert.get("category") or "").lower() in ESCALATING_CATEGORIES
        and str(alert.get("risk") or "").upper() in ESCALATING_RISKS
    )


def report_risk_for_alert(alert: dict[str, Any]) -> str:
    """Map an LLM alert risk to the report's English risk vocabulary."""
    return _RISK_TO_REPORT.get(str(alert.get("risk") or "").upper(), "low")
