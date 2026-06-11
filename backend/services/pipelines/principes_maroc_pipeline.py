"""Détection des atteintes potentielles aux constantes nationales marocaines.

Quatre catégories couvertes (الثوابت الدولة المغربية) :

- ``islam`` — الدين الإسلامي المعتدل
- ``national_unity`` — الوحدة الوطنية متعددة الروافد
- ``monarchy`` — الملكية الدستورية
- ``democratic_choice`` — الاختيار الديمقراطي

Le pipeline est **déterministe** : il s'appuie sur des lexiques et des
expressions régulières. Il **signale**, il ne censure pas. Une mention
neutre de "roi", "islam", "Sahara", "Maroc", "démocratie" ou
"constitution" ne déclenche aucun drapeau ; seules les co-occurrences
avec un déclencheur sensible (insulte, appel à la violence, propos
séparatiste, ironie agressive) sont remontées.

Échelle de risque (cf. spec) :

==========  =============  ====================
Score       Niveau         Bucket
==========  =============  ====================
0.00–0.24   ``faible``     mention neutre / RAS
0.25–0.49   ``moyen``      ambigu, ironique
0.50–0.74   ``élevé``      attaque explicite
0.75–1.00   ``très élevé`` violence, haine, séparatisme
==========  =============  ====================
"""

from __future__ import annotations

import logging
import re
from typing import Any


logger = logging.getLogger(__name__)


# ---------- Risk levels ----------

LEVEL_FAIBLE = "faible"
LEVEL_MOYEN = "moyen"
LEVEL_ELEVE = "élevé"
LEVEL_TRES_ELEVE = "très élevé"

# Severity → score weight. Mapped so that the worst severity alone lands
# squarely in its bucket (0.10, 0.30, 0.60, 0.90).
_SEVERITY_WEIGHTS: dict[str, float] = {
    LEVEL_FAIBLE: 0.10,
    LEVEL_MOYEN: 0.30,
    LEVEL_ELEVE: 0.60,
    LEVEL_TRES_ELEVE: 0.90,
}


CATEGORIES: tuple[str, ...] = (
    "islam",
    "national_unity",
    "monarchy",
    "democratic_choice",
)


# ---------- Subjects ----------

# Subjects identify the protected topic. Their mere presence does NOT
# create a flag — only the co-occurrence with a severity trigger inside
# ``_PROXIMITY_WINDOW`` characters does.

_SUBJECT_PATTERNS: dict[str, list[str]] = {
    "islam": [
        r"\bislam\w*\b",
        r"\bmusulman\w*\b",
        r"\bcoran\w*\b",
        r"\bproph[èe]te?\b",
        r"\bmahomet\b",
        r"\ballah\b",
        r"\breligion\w*\b",
        r"\bdine\b",
        r"\brasoul\b",
        # Arabic
        r"إسلام",
        r"مسلم",
        r"مسلمين",
        r"قرآن",
        r"محمد",
        r"الله",
        r"الدين",
    ],
    "national_unity": [
        r"\bmaroc(?:ain[se]?)?\b",
        r"\bnation\b",
        r"\bsahara\w*\b",
        r"\bsahraoui[es]?\b",
        r"\bpeuple marocain\b",
        r"\bmaghrib\b",
        r"\bsahra\b",
        # Arabic
        r"المغرب",
        r"المغاربة",
        r"الصحراء",
        r"الشعب المغربي",
        r"الوحدة الوطنية",
    ],
    "monarchy": [
        r"\bmonarchie\b",
        r"\broi\b",
        r"\bmonarque\b",
        r"\btr[ôo]ne\b",
        r"\bmohammed vi\b",
        r"\bmohamed vi\b",
        r"\bpalais royal\b",
        r"\bcouronne\b",
        r"\bsidna\b",
        r"\bmalek\b",
        # Arabic
        r"الملك",
        r"الملكية",
        r"العرش",
        r"المخزن",
    ],
    "democratic_choice": [
        r"\bd[ée]mocrat\w+\b",
        r"\bconstitution\w*\b",
        r"\bparlement\w*\b",
        r"\b[ée]lection\w*\b",
        r"\bsuffrage\b",
        # Arabic
        r"الديمقراطية",
        r"الدستور",
        r"البرلمان",
        r"الانتخابات",
    ],
}


# ---------- Severity triggers ----------

# Patterns that, when present in the proximity window of a subject, raise
# the flag to the corresponding severity. Higher severities are checked
# first so a "very high" trigger always wins over a milder one.

_TRIGGERS: dict[str, list[str]] = {
    LEVEL_TRES_ELEVE: [
        # Violence verbs / calls to harm
        r"\btuer(?:s|ez|ons)?\b",
        r"\bassassine\w*\b",
        r"\b[ée]liminer\b",
        r"\bmort\s+(?:au|aux|à)\b",
        r"\bpendre\b",
        r"\blyncher?\b",
        r"\bguillotine\w*\b",
        r"\b[ée]gorger\b",
        r"\bd[ée]truire\b",
        r"\bbombarder\b",
        r"\bmassacrer?\b",
        r"\bex[ée]cuter\b",
        # Separatism
        r"\bind[ée]pendance\s+du\s+sahara\b",
        r"\bsahara\s+libre\b",
        r"\bpolisario\b",
        r"\br[ée]publique\s+sahraouie\b",
        # Hate / mass insults
        r"\btous\s+des\s+terroristes\b",
        r"\bsales?\s+arabes?\b",
        # Direct overthrow / regicide
        r"\b[àa]\s+bas\s+le\s+roi\b",
        r"\bmort\s+au\s+roi\b",
        r"\bd[ée]gage(?:r|z)?\s+le\s+roi\b",
        r"\brenverser\s+la\s+monarchie\b",
        r"\babolir\s+la\s+monarchie\b",
        r"\bd[ée]truire\s+l'?islam\b",
        # Arabic violence/hate/separatist
        r"اقتلوا",
        r"الموت\s+ل",
        r"إعدام",
        r"اغتيال",
        r"إبادة",
        r"استقلال\s+الصحراء",
        r"الصحراء\s+حرة",
        r"البوليساريو",
        r"الإطاحة\s+بالملك",
        r"إسقاط\s+النظام",
        r"الموت\s+للملك",
    ],
    LEVEL_ELEVE: [
        # Strong direct insults towards an institution / religion
        r"\bcorrompu\w*\b",
        r"\bvoleur\w*\b",
        r"\bill[ée]gitime\b",
        r"\bimposteur\w*\b",
        r"\btra[îi]tre\w*\b",
        r"\bdictateur\w*\b",
        r"\btyran\w*\b",
        r"\bsale\s+roi\b",
        r"\bsale\s+religion\b",
        r"\barri[ée]r[ée]\w*\b",
        r"\bbarbare\w*\b",
        r"\bobscurantiste\w*\b",
        r"\bfanatique\w*\b",
        r"\bd[ée]testable\b",
        r"\b[àa]\s+bas\b",
        r"\bnon\s+[àa]\s+la\s+monarchie\b",
        r"\bnon\s+[àa]\s+l[' ]islam\b",
        r"\brejet\w*\s+de\s+l[' ]?islam\b",
        r"\bbouffon\b",
        # Arabic
        r"فاسد",
        r"خائن",
        r"دكتاتور",
        r"طاغية",
        r"محتل",
        r"يسقط\s+الملك",
        r"يسقط\s+الإسلام",
        r"يسقط\s+النظام",
    ],
    LEVEL_MOYEN: [
        # Ambiguous, ironic or critical lexicon
        r"\binjustice\b",
        r"\bcorruption\b",
        r"\bdictature\b",
        r"\boppression\b",
        r"\b[ée]touffe\w*\b",
        r"\bmanque\s+de\s+libert[ée]\b",
        r"\bcensur\w*\b",
        r"\bmonopol\w*\b",
        r"\babsolu\w*\b",
        r"\b[ée]touffement\b",
        # Generic critical irony
        r"\bsoi[- ]disant\b",
        r"\bpr[ée]tendu\w*\b",
        # Arabic
        r"ظلم",
        r"فساد",
        r"قمع",
        r"دكتاتورية",
        r"احتقار",
    ],
}


# Pre-compiled patterns for performance.
_SUBJECT_RE: dict[str, list[re.Pattern[str]]] = {
    cat: [re.compile(p, re.IGNORECASE | re.UNICODE) for p in patterns]
    for cat, patterns in _SUBJECT_PATTERNS.items()
}
_TRIGGER_RE: dict[str, list[re.Pattern[str]]] = {
    lvl: [re.compile(p, re.IGNORECASE | re.UNICODE) for p in patterns]
    for lvl, patterns in _TRIGGERS.items()
}


# Characters of context around a subject occurrence within which a
# trigger is considered "near" — about one paragraph.
_PROXIMITY_WINDOW = 120

# Characters of context kept in the ``evidence`` field of each flag.
_EVIDENCE_HALF_WINDOW = 80

# Hard cap on the number of neutral mentions surfaced to the operator.
# Beyond this we set ``mentions_truncated = True``. Picked so a long
# documentary scenario (~150 chunks × ~3 mentions per chunk) still fits
# under the MongoDB 16 MiB document limit.
_MAX_MENTIONS = 400


# ---------- Mapping helpers ----------

# Bridge between the project's legacy English risk vocabulary and the
# French scale required by this pipeline.
_ENGLISH_TO_FR = {
    "low": LEVEL_FAIBLE,
    "medium": LEVEL_MOYEN,
    "high": LEVEL_ELEVE,
    "tres_eleve": LEVEL_TRES_ELEVE,
    "très élevé": LEVEL_TRES_ELEVE,
}

_FR_TO_ENGLISH = {
    LEVEL_FAIBLE: "low",
    LEVEL_MOYEN: "medium",
    LEVEL_ELEVE: "high",
    LEVEL_TRES_ELEVE: "tres_eleve",
}

_ORDER = {"low": 0, "medium": 1, "high": 2, "tres_eleve": 3}


def map_fr_to_english(level: str) -> str:
    """Map a French risk level to the project's internal English vocabulary.

    Adds ``"tres_eleve"`` for severities above ``high``.
    """
    return _FR_TO_ENGLISH.get(level, "low")


def map_english_to_fr(level: str) -> str:
    """Reverse helper for places where the French scale is exposed."""
    return _ENGLISH_TO_FR.get(level, LEVEL_FAIBLE)


def escalate_risk_level(current: str, candidate_fr: str) -> str:
    """Return whichever of the two risk levels is higher (English vocab).

    ``current`` is in the legacy ``low/medium/high/tres_eleve`` scale.
    ``candidate_fr`` is in the French scale (``faible``…``très élevé``).
    """
    candidate = _FR_TO_ENGLISH.get(candidate_fr, "low")
    if _ORDER.get(candidate, 0) > _ORDER.get(current, 0):
        return candidate
    return current


# ---------- Pipeline ----------


class PrincipesMarocPipeline:
    """Detect potential breaches of the Moroccan national constants.

    The pipeline is intentionally deterministic so the verdict is
    auditable. It flags passages for *manual* review — it never censors.
    """

    def analyze(self, text: str, chunks: list[str]) -> dict[str, Any]:
        """Run the analysis on the cleaned full text and on each chunk.

        Args:
            text: Cleaned full document text. Used only as a fallback
                segment when ``chunks`` is empty.
            chunks: Ordered list of cleaned text chunks. ``chunk_index``
                in each flag refers to this list.

        Returns:
            A dict with ``score``, ``risk_level``, ``flags``, ``categories``,
            ``mentions`` and ``mentions_total``. See the module docstring for
            the score scale.

            - ``flags`` keeps only the passages where a severity trigger
              co-occurs with a protected subject. These drive the risk
              level.
            - ``mentions`` lists *every* occurrence of a protected subject
              (neutral or risky) so the operator can manually review the
              full coverage rather than trusting the deterministic filter.
        """
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if not isinstance(chunks, list):
            raise TypeError("chunks must be a list")
        for index, chunk in enumerate(chunks):
            if not isinstance(chunk, str):
                raise TypeError(
                    f"chunks[{index}] must be a string, got "
                    f"{type(chunk).__name__}"
                )

        flags: list[dict[str, Any]] = []
        mentions: list[dict[str, Any]] = []
        if chunks:
            for index, chunk in enumerate(chunks):
                if chunk.strip():
                    seg_flags, seg_mentions = self._analyze_segment(
                        chunk, chunk_index=index
                    )
                    flags.extend(seg_flags)
                    mentions.extend(seg_mentions)
        elif text.strip():
            seg_flags, seg_mentions = self._analyze_segment(
                text, chunk_index=None
            )
            flags.extend(seg_flags)
            mentions.extend(seg_mentions)

        mentions = self._dedupe_mentions(mentions)
        mentions_total = len(mentions)
        mentions_truncated = mentions_total > _MAX_MENTIONS
        mentions = mentions[:_MAX_MENTIONS]
        categories_count = self._count_mentions_by_category(mentions)

        result = self._build_result(flags)
        result["mentions"] = mentions
        result["mentions_total"] = mentions_total
        result["mentions_truncated"] = mentions_truncated
        result["mentions_by_category"] = categories_count
        logger.info(
            "PrincipesMarocPipeline: flags=%s mentions=%s score=%s risk=%s",
            len(flags),
            mentions_total,
            result["score"],
            result["risk_level"],
        )
        return result

    # ---------- Internals ----------

    def _analyze_segment(
        self, segment: str, chunk_index: int | None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Detect flags AND every neutral mention inside a segment.

        Returns a ``(flags, mentions)`` tuple. Flags are the subset of
        mentions that have a severity trigger nearby.
        """
        flags: list[dict[str, Any]] = []
        mentions: list[dict[str, Any]] = []
        for category, subject_patterns in _SUBJECT_RE.items():
            for subject_pattern in subject_patterns:
                for subject_match in subject_pattern.finditer(segment):
                    severity = self._severity_for_subject_match(
                        segment=segment,
                        subject_start=subject_match.start(),
                        subject_end=subject_match.end(),
                    )
                    mention = self._build_mention(
                        category=category,
                        chunk_index=chunk_index,
                        segment=segment,
                        subject_match=subject_match,
                        severity=severity,
                    )
                    mentions.append(mention)
                    if severity is not None:
                        flags.append(
                            self._build_flag(
                                category=category,
                                severity=severity,
                                chunk_index=chunk_index,
                                segment=segment,
                                subject_match=subject_match,
                            )
                        )
        return self._dedupe_flags(flags), mentions

    @staticmethod
    def _build_mention(
        category: str,
        chunk_index: int | None,
        segment: str,
        subject_match: re.Match[str],
        severity: str | None,
    ) -> dict[str, Any]:
        start = max(0, subject_match.start() - _EVIDENCE_HALF_WINDOW)
        end = min(len(segment), subject_match.end() + _EVIDENCE_HALF_WINDOW)
        evidence = re.sub(r"\s+", " ", segment[start:end]).strip()
        return {
            "category": category,
            "subject": subject_match.group(0),
            "chunk_index": chunk_index,
            "evidence": evidence,
            # ``None`` when the mention is purely neutral. Set to the
            # severity bucket when a trigger was found nearby so the UI
            # can mark this mention as "déjà signalée".
            "flagged_severity": severity,
        }

    @staticmethod
    def _dedupe_mentions(
        mentions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Drop exact duplicates (same category + chunk + evidence).

        We keep mentions from different chunks/positions even if they
        share the same word — the operator wants the full distribution.
        """
        seen: set[tuple[Any, ...]] = set()
        out: list[dict[str, Any]] = []
        for mention in mentions:
            key = (
                mention["category"],
                mention.get("chunk_index"),
                mention["evidence"],
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(mention)
        return out

    @staticmethod
    def _count_mentions_by_category(
        mentions: list[dict[str, Any]],
    ) -> dict[str, int]:
        out = {cat: 0 for cat in CATEGORIES}
        for mention in mentions:
            cat = str(mention.get("category") or "")
            if cat in out:
                out[cat] += 1
        return out

    @staticmethod
    def _severity_for_subject_match(
        segment: str,
        subject_start: int,
        subject_end: int,
    ) -> str | None:
        """Return the worst severity triggered near this subject, or None."""
        win_start = max(0, subject_start - _PROXIMITY_WINDOW)
        win_end = min(len(segment), subject_end + _PROXIMITY_WINDOW)
        window = segment[win_start:win_end]

        for level in (LEVEL_TRES_ELEVE, LEVEL_ELEVE, LEVEL_MOYEN):
            for trigger_pattern in _TRIGGER_RE[level]:
                if trigger_pattern.search(window):
                    return level
        return None

    @staticmethod
    def _build_flag(
        category: str,
        severity: str,
        chunk_index: int | None,
        segment: str,
        subject_match: re.Match[str],
    ) -> dict[str, Any]:
        start = max(0, subject_match.start() - _EVIDENCE_HALF_WINDOW)
        end = min(len(segment), subject_match.end() + _EVIDENCE_HALF_WINDOW)
        evidence = re.sub(r"\s+", " ", segment[start:end]).strip()
        return {
            "category": category,
            "severity": severity,
            "chunk_index": chunk_index,
            "evidence": evidence,
            "explanation": _explanation_for(category, severity),
        }

    @staticmethod
    def _dedupe_flags(
        flags: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Drop duplicate ``(category, severity, evidence)`` triples."""
        seen: set[tuple[Any, ...]] = set()
        out: list[dict[str, Any]] = []
        for flag in flags:
            key = (flag["category"], flag["severity"], flag["evidence"])
            if key in seen:
                continue
            seen.add(key)
            out.append(flag)
        return out

    @staticmethod
    def _build_result(flags: list[dict[str, Any]]) -> dict[str, Any]:
        if not flags:
            score = 0.0
            risk_level = LEVEL_FAIBLE
        else:
            top_weight = max(
                _SEVERITY_WEIGHTS[flag["severity"]] for flag in flags
            )
            # Soft density bonus capped so it cannot escalate to the
            # next bucket on its own.
            density_bonus = min(0.04 * (len(flags) - 1), 0.08)
            score = min(1.0, top_weight + density_bonus)
            risk_level = _risk_level_from_score(score)

        categories: dict[str, dict[str, Any]] = {}
        for cat in CATEGORIES:
            cat_flags = [f for f in flags if f["category"] == cat]
            if not cat_flags:
                categories[cat] = {
                    "count": 0,
                    "risk_level": LEVEL_FAIBLE,
                    "score": 0.0,
                }
                continue
            top = max(_SEVERITY_WEIGHTS[f["severity"]] for f in cat_flags)
            categories[cat] = {
                "count": len(cat_flags),
                "risk_level": _risk_level_from_score(top),
                "score": round(top, 4),
            }

        return {
            "score": round(score, 4),
            "risk_level": risk_level,
            "flags": flags,
            "categories": categories,
        }


# ---------- Module-level helpers ----------


def _risk_level_from_score(score: float) -> str:
    if score >= 0.75:
        return LEVEL_TRES_ELEVE
    if score >= 0.50:
        return LEVEL_ELEVE
    if score >= 0.25:
        return LEVEL_MOYEN
    return LEVEL_FAIBLE


def _explanation_for(category: str, severity: str) -> str:
    label = {
        "islam": "la religion (Islam modéré)",
        "national_unity": "l'unité nationale et l'intégrité territoriale",
        "monarchy": "la Monarchie constitutionnelle",
        "democratic_choice": "le choix démocratique",
    }[category]
    return (
        f"Passage sensible touchant à {label}. "
        f"Risque de non-conformité niveau {severity}. "
        "À examiner manuellement — alerte contextuelle, pas une censure."
    )
