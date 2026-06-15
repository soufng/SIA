"""LLM-based reranker for the advanced RAG candidate pool.

Cosine similarity in Qdrant ranks passages by lexical/semantic surface
proximity. It overweights boilerplate (headers, common scene directions)
and underweights passages that share a *narrative function* without sharing
many tokens. This reranker takes the candidate passages produced by
`AdvancedRAGService` (legacy matches + multi-query hits) and asks an LLM to
score each one for editorial relevance to the uploaded document, returning
the top-K by LLM score.

Designed to be additive and fail-soft: on any error (LLM down, JSON parse
failure, score length mismatch) the caller keeps the original cosine-ranked
order. Never makes the report worse.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from backend.services.llm_provider import LLMProvider


logger = logging.getLogger(__name__)


_RERANK_SYSTEM_PROMPT = (
    "Tu es un évaluateur de pertinence éditoriale. Tu reçois un résumé du "
    "scénario analysé et une liste de passages candidats issus d'autres "
    "scénarios. Tu attribues à chaque candidat un score de pertinence "
    "entier de 0 (sans rapport) à 10 (forte ressemblance narrative ou "
    "thématique). Tu réponds UNIQUEMENT par un tableau JSON d'objets de la "
    'forme {"i": <index>, "s": <score>}, sans commentaire.'
)


_JSON_ARRAY_RE = re.compile(r"\[\s*\{.*?\}\s*(?:,\s*\{.*?\}\s*)*\]", re.DOTALL)


@dataclass
class RerankItem:
    """One candidate passage paired with the score the LLM gave it."""

    index: int
    score: float


@dataclass
class RerankResult:
    """Outcome of a rerank pass."""

    ordered_indexes: list[int]
    scores: dict[int, float]
    used_fallback: bool
    parse_error: str | None = None


class LLMReranker:
    """Re-order candidate passages by LLM-judged relevance."""

    def __init__(
        self,
        llm_provider: LLMProvider,
        max_excerpt_chars: int = 400,
    ) -> None:
        self.llm_provider = llm_provider
        self.max_excerpt_chars = max_excerpt_chars

    def rerank(
        self,
        document_summary: str,
        candidates: list[str],
    ) -> RerankResult:
        """Score candidates and return them ordered best-first.

        ``candidates`` is a list of plain-text excerpts. The returned
        ``ordered_indexes`` indexes back into the original list, so the
        caller can carry full passage metadata without re-encoding it for
        the LLM.
        """
        if not candidates:
            return RerankResult(ordered_indexes=[], scores={}, used_fallback=False)
        if len(candidates) == 1:
            return RerankResult(
                ordered_indexes=[0], scores={0: 0.0}, used_fallback=False
            )

        user_prompt = self._render_prompt(document_summary, candidates)
        try:
            response = self.llm_provider.complete(
                system=_RERANK_SYSTEM_PROMPT, user=user_prompt
            )
        except Exception as exc:
            logger.warning("Reranker: LLM call failed: %s", exc)
            return self._fallback(candidates, parse_error=f"llm_error: {exc}")

        items, parse_error = _parse_score_list(response.text)
        if not items:
            return self._fallback(candidates, parse_error=parse_error)

        # Keep only items whose index is in range. Don't error out on bad
        # indexes — small models sometimes invent extra rows.
        scores: dict[int, float] = {}
        for item in items:
            if 0 <= item.index < len(candidates) and item.index not in scores:
                scores[item.index] = item.score

        if not scores:
            return self._fallback(candidates, parse_error="no_valid_indexes")

        # Candidates the LLM didn't score: keep them at the tail in their
        # original cosine order. This guarantees the rerank can only ever
        # *improve* the ordering, never drop a passage.
        scored = sorted(scores.keys(), key=lambda i: scores[i], reverse=True)
        missing = [i for i in range(len(candidates)) if i not in scores]
        return RerankResult(
            ordered_indexes=scored + missing,
            scores=scores,
            used_fallback=False,
        )

    def _render_prompt(self, document_summary: str, candidates: list[str]) -> str:
        summary = document_summary.strip() or "(résumé non disponible)"
        if len(summary) > 1500:
            summary = summary[:1500].rstrip() + "…"

        lines = ["# Résumé du scénario analysé", "", summary, "", "# Passages candidats"]
        for index, text in enumerate(candidates):
            excerpt = (text or "").strip().replace("\n", " ")
            if len(excerpt) > self.max_excerpt_chars:
                excerpt = excerpt[: self.max_excerpt_chars].rstrip() + "…"
            lines.append(f"[{index}] {excerpt or '(extrait vide)'}")

        lines.extend(
            [
                "",
                "# Consigne",
                f"Donne un score de pertinence (0-10) pour chacun des "
                f"{len(candidates)} passages. Réponds par un tableau JSON, "
                'par exemple : [{"i": 0, "s": 8}, {"i": 1, "s": 3}].',
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _fallback(
        candidates: list[str], parse_error: str | None
    ) -> RerankResult:
        return RerankResult(
            ordered_indexes=list(range(len(candidates))),
            scores={},
            used_fallback=True,
            parse_error=parse_error,
        )


def _parse_score_list(raw: str) -> tuple[list[RerankItem], str | None]:
    """Extract ``[{"i": int, "s": number}, ...]`` from an LLM reply.

    Accepts a few common deviations: markdown fences, leading preamble,
    and string-typed scores. Anything else returns an empty list and a
    short error code.
    """
    text = (raw or "").strip()
    if not text:
        return [], "empty_response"

    fenced = re.match(
        r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE
    )
    if fenced:
        text = fenced.group(1).strip()

    candidates_raw: list[str] = [text]
    match = _JSON_ARRAY_RE.search(text)
    if match:
        candidates_raw.append(match.group(0))

    for blob in candidates_raw:
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, list):
            continue
        items: list[RerankItem] = []
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            index = entry.get("i")
            score = entry.get("s")
            if not isinstance(index, int):
                continue
            try:
                score_f = float(score)
            except (TypeError, ValueError):
                continue
            items.append(RerankItem(index=index, score=score_f))
        if items:
            return items, None

    return [], "json_parse_failed"
