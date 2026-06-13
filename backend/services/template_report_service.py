import logging
import re
from collections import OrderedDict
from typing import Any


logger = logging.getLogger(__name__)


# Mapping from internal lexicon category names to user-facing French labels.
# Anything not in this dictionary is rendered as "non classé (<raw>)".
CATEGORY_LABELS_FR: dict[str, str] = {
    "offensive_words": "mots offensants",
    "profanity": "vulgarité",
    "insults": "insultes",
    "violent_terms": "termes violents",
    "sexual_terms": "termes sexuels",
    "adult_content": "contenu adulte",
    "wiqaya": "détecté par wiqaya",
    "terms": "termes",
    "unknown": "non classé",
    "uncategorized": "non classé",
    "": "non classé",
    None: "non classé",
}


LANGUAGE_LABELS_FR: dict[str, str] = {
    "fr": "français",
    "fr_fr": "français",
    "french": "français",
    "ar": "arabe",
    "arabic": "arabe",
    "en": "anglais",
    "english": "anglais",
    "darija": "darija",
    "ar/darija": "arabe/darija",
    "unknown": "inconnue",
    "": "inconnue",
}


_LATIN_TOKEN_RE = re.compile(r"^[A-Za-zÀ-ÿ' \-]+$")


class TemplateReportService:
    """Service responsible for generating deterministic template-based reports.

    Despite its historical placement next to the RAG layer, this service does
    not perform any retrieval: it only assembles a structured report from
    pre-computed analysis results (plagiarism, profanity, adult content,
    document stats). The actual retrieval-augmented narrative lives in
    ``AdvancedRAGService``.
    """

    MAX_MATCH_EXTRACT = 900
    MAX_SNIPPET_LENGTH = 300

    def generate_report(
        self,
        scenario_id: str,
        plagiarism_result: dict[str, Any],
        profanity_result: dict[str, Any],
        adult_content_result: dict[str, Any],
        document_stats: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a structured explanatory report from analysis results."""
        self._validate_inputs(
            scenario_id=scenario_id,
            plagiarism_result=plagiarism_result,
            profanity_result=profanity_result,
            adult_content_result=adult_content_result,
            document_stats=document_stats,
        )

        try:
            logger.info("Generating RAG report for scenario_id=%s.", scenario_id)
            rag_context = self._build_rag_context(
                plagiarism_result=plagiarism_result,
                profanity_result=profanity_result,
                adult_content_result=adult_content_result,
                document_stats=document_stats,
            )

            risk_level = self._calculate_risk_level(rag_context)
            justification = self._build_risk_justification(rag_context, risk_level)
            summary = self._build_summary(rag_context, risk_level, justification)
            plagiarism_explanation = self._build_plagiarism_explanation(rag_context)
            moderation_explanation = self._build_moderation_explanation(rag_context)
            recommendations = self._build_recommendations(rag_context, risk_level)
            conclusion = self._build_conclusion(rag_context, risk_level, justification)
            generated_report = self._build_generated_report(
                scenario_id=scenario_id,
                summary=summary,
                risk_level=risk_level,
                plagiarism_explanation=plagiarism_explanation,
                moderation_explanation=moderation_explanation,
                recommendations=recommendations,
                conclusion=conclusion,
                document_stats=document_stats,
                rag_context=rag_context,
            )

            logger.info("RAG report generated for scenario_id=%s.", scenario_id)
            return {
                "scenario_id": scenario_id,
                "summary": summary,
                "risk_level": risk_level,
                "risk_justification": justification,
                "plagiarism_explanation": plagiarism_explanation,
                "moderation_explanation": moderation_explanation,
                "recommendations": recommendations,
                "conclusion": conclusion,
                "generated_report": generated_report,
            }
        except Exception as exc:
            logger.exception(
                "Failed to generate RAG report for scenario_id=%s.", scenario_id
            )
            raise RuntimeError("Failed to generate RAG report") from exc

    # ---------- Context ----------

    def _build_rag_context(
        self,
        plagiarism_result: dict[str, Any],
        profanity_result: dict[str, Any],
        adult_content_result: dict[str, Any],
        document_stats: dict[str, Any],
    ) -> dict[str, Any]:
        matches = plagiarism_result.get("matches", [])
        if not isinstance(matches, list):
            matches = []

        vulgarity_matches = profanity_result.get("vulgarity_matches", [])
        if not isinstance(vulgarity_matches, list):
            vulgarity_matches = []

        detected_words = profanity_result.get("detected_words", []) or []
        unique_detected_words = self._dedupe_words(detected_words)

        return {
            "global_similarity_score": float(
                plagiarism_result.get("global_similarity_score", 0.0) or 0.0
            ),
            "exact_duplicate": bool(plagiarism_result.get("exact_duplicate", False)),
            "duplicate_count": int(plagiarism_result.get("duplicate_count") or 0),
            "duplicate_analyses": plagiarism_result.get("duplicate_analyses") or [],
            "plagiarism_detected": bool(
                plagiarism_result.get("plagiarism_detected", False)
            ),
            "matches": matches,
            "total_matches": plagiarism_result.get(
                "total_matches", len(matches)
            ),
            "displayed_matches": plagiarism_result.get(
                "displayed_matches", len(matches)
            ),
            "total_sources": plagiarism_result.get("total_sources", 0),
            "displayed_sources": plagiarism_result.get("displayed_sources", 0),
            "is_truncated": bool(plagiarism_result.get("is_truncated", False)),
            "plagiarism_sources": (
                plagiarism_result.get("plagiarism_sources") or []
            ),
            "contains_profanity": bool(
                profanity_result.get("contains_profanity", False)
            ),
            "profanity_score": float(
                profanity_result.get("profanity_score", 0.0) or 0.0
            ),
            "profanity_occurrences": int(
                profanity_result.get("occurrences_count")
                or len(vulgarity_matches)
                or 0
            ),
            "detected_words": unique_detected_words,
            "vulgarity_matches": vulgarity_matches,
            "contains_adult_content": bool(
                adult_content_result.get("contains_adult_content", False)
            ),
            "adult_content_score": float(
                adult_content_result.get("adult_content_score", 0.0) or 0.0
            ),
            "detected_terms": adult_content_result.get("detected_terms", []) or [],
            "adult_risk_level": adult_content_result.get("risk_level", "low"),
            "document_stats": document_stats,
        }

    def _calculate_risk_level(self, rag_context: dict[str, Any]) -> str:
        similarity_score = rag_context["global_similarity_score"]
        profanity_score = rag_context["profanity_score"]
        adult_score = rag_context["adult_content_score"]
        matches_count = len(rag_context["matches"])

        if bool(rag_context.get("exact_duplicate")):
            return "high"

        if similarity_score >= 0.75 or adult_score > 60 or profanity_score > 60:
            return "high"

        if (
            similarity_score >= 0.4
            or matches_count > 0
            or adult_score > 20
            or profanity_score > 20
        ):
            return "medium"

        return "low"

    def _build_risk_justification(
        self,
        rag_context: dict[str, Any],
        risk_level: str,
    ) -> str:
        """Build the human-readable justification of the risk level.

        The primary driver is the signal that actually pushed the analysis to
        the current risk level (i.e. the one that crossed the matching
        threshold). Other non-zero signals are listed as secondary so the
        narrative never says HIGH is justified by a "faible" score.
        """
        if risk_level == "low":
            return (
                "Aucun signal fort n'a été détecté ; le document ne présente "
                "pas de risque significatif."
            )

        primary, secondaries = self._classify_drivers(rag_context, risk_level)

        if primary is None and not secondaries:
            return (
                "Aucun signal fort n'a été détecté ; le document ne présente "
                "pas de risque significatif."
            )

        if primary is None:
            # Risk was triggered by something we cannot describe individually
            # (e.g. matches_count > 0 with similarity == 0). Fall back to the
            # first available secondary as the primary.
            primary = secondaries.pop(0)

        head = (
            f"Le niveau {self._risk_level_label_fr(risk_level)} est "
            f"principalement justifié par {primary}."
        )

        if not secondaries:
            return head

        secondary_text = self._join_clause(secondaries)
        tail = (
            f" {secondary_text.capitalize()} "
            "constitue un signal secondaire à vérifier."
            if len(secondaries) == 1
            else (
                f" {secondary_text.capitalize()} "
                "constituent des signaux secondaires à vérifier."
            )
        )
        return head + tail

    def _classify_drivers(
        self,
        rag_context: dict[str, Any],
        risk_level: str,
    ) -> tuple[str | None, list[str]]:
        """Return (primary_driver_text, secondary_driver_texts)."""
        similarity = rag_context["global_similarity_score"]
        profanity = rag_context["profanity_score"]
        adult = rag_context["adult_content_score"]
        matches_count = len(rag_context["matches"])
        exact_duplicate = bool(rag_context.get("exact_duplicate"))
        duplicate_count = int(rag_context.get("duplicate_count") or 0)

        # Each candidate carries (key, primary_qualifies, intensity, text).
        # Primary candidates are those that ACTUALLY crossed the threshold for
        # the current risk level. Intensity is used to break ties.
        candidates: list[dict[str, Any]] = []

        if exact_duplicate:
            count_text = (
                f"{duplicate_count} analyse(s) antérieure(s)"
                if duplicate_count
                else "une analyse antérieure"
            )
            candidates.append(
                {
                    "kind": "exact_duplicate",
                    "level_match": "high",
                    "intensity": 1.0,
                    "text": f"un doublon exact déjà analysé ({count_text})",
                    "value": 1.0,
                }
            )

        if similarity > 0 or matches_count > 0:
            qualifier_high = similarity >= 0.75
            qualifier_med = 0.4 <= similarity < 0.75 or matches_count > 0
            text = (
                f"un score de similarité {'élevé' if qualifier_high else 'modéré'} "
                f"de {self._format_percent(similarity)}"
                if (qualifier_high or qualifier_med)
                else f"la présence de termes similaires "
                f"({self._format_percent(similarity)})"
            )
            if risk_level == "high" and qualifier_high:
                level_match = "high"
            elif risk_level == "medium" and (qualifier_med or matches_count > 0):
                level_match = "medium"
            else:
                level_match = "below"
            candidates.append(
                {
                    "kind": "similarity",
                    "level_match": level_match,
                    "intensity": similarity,
                    "text": text,
                    "value": similarity,
                }
            )

        if profanity > 0:
            languages = self._summarize_vulgarity_languages(rag_context)
            qualifier = (
                "important" if profanity > 60
                else ("modéré" if profanity > 20 else "faible")
            )
            tail = f" en {languages}" if languages else ""
            text = (
                f"un score de vulgarité {qualifier} de "
                f"{self._format_score_over_100(profanity)}{tail}"
            )
            if risk_level == "high" and profanity > 60:
                level_match = "high"
            elif risk_level == "medium" and profanity > 20:
                level_match = "medium"
            elif profanity > 0:
                # Always include as a candidate, just not as primary.
                level_match = "below"
            else:
                level_match = "below"
            # Replace the headline if it's a "faible" signal but still surface
            # the presence of vulgar terms as a secondary clue.
            if qualifier == "faible":
                text = (
                    "la présence de termes vulgaires"
                    + (f" en {languages}" if languages else "")
                    + f" (score {self._format_score_over_100(profanity)})"
                )
            candidates.append(
                {
                    "kind": "profanity",
                    "level_match": level_match,
                    "intensity": profanity / 100.0,
                    "text": text,
                    "value": profanity,
                }
            )

        if adult > 0:
            qualifier = (
                "important" if adult > 60
                else ("modéré" if adult > 20 else "faible")
            )
            text = (
                f"un score de contenu adulte {qualifier} de "
                f"{self._format_score_over_100(adult)}"
            )
            if risk_level == "high" and adult > 60:
                level_match = "high"
            elif risk_level == "medium" and adult > 20:
                level_match = "medium"
            else:
                level_match = "below"
            if qualifier == "faible":
                text = (
                    "la présence de contenu adulte "
                    f"(score {self._format_score_over_100(adult)})"
                )
            candidates.append(
                {
                    "kind": "adult",
                    "level_match": level_match,
                    "intensity": adult / 100.0,
                    "text": text,
                    "value": adult,
                }
            )

        # Pick primary: matches the current level, with highest intensity.
        primary_candidates = [
            c for c in candidates if c["level_match"] == risk_level
        ]
        primary = (
            max(primary_candidates, key=lambda c: c["intensity"])
            if primary_candidates
            else None
        )

        secondaries = [
            c["text"] for c in candidates if primary is None or c is not primary
        ]
        return (primary["text"] if primary else None), secondaries

    @staticmethod
    def _join_clause(parts: list[str]) -> str:
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return f"{parts[0]} et {parts[1]}"
        return ", ".join(parts[:-1]) + f" et {parts[-1]}"

    def _build_summary(
        self,
        rag_context: dict[str, Any],
        risk_level: str,
        justification: str,
    ) -> str:
        if risk_level == "high":
            head = (
                "Le scénario présente un risque important et nécessite une "
                "revue manuelle approfondie."
            )
        elif risk_level == "medium":
            head = (
                "Le scénario présente des signaux de risque modérés à vérifier "
                "avant validation."
            )
        else:
            head = (
                "Le scénario ne présente pas de risque significatif selon les "
                "données analysées."
            )

        return f"{head} {justification}".strip()

    # ---------- Plagiarism ----------

    def _build_plagiarism_explanation(self, rag_context: dict[str, Any]) -> str:
        similarity_score = rag_context["global_similarity_score"]
        matches = rag_context["matches"]
        score_pct = self._format_percent(similarity_score)
        exact_duplicate = bool(rag_context.get("exact_duplicate"))
        duplicate_count = int(rag_context.get("duplicate_count") or 0)
        lines: list[str] = []

        if exact_duplicate:
            count_text = f"{duplicate_count} fois" if duplicate_count != 1 else "1 fois"
            lines.extend(
                [
                    "Doublon exact détecté",
                    f"Ce document a déjà été analysé {count_text} auparavant.",
                    (
                        "Les anciennes analyses identiques ont été regroupées "
                        "et ne sont pas comptées comme passages de plagiat partiel."
                    ),
                ]
            )

        if not matches:
            lines.append(
                "Aucun passage similaire significatif n'a été détecté. "
                f"Score global de similarité : {score_pct}."
            )
            return "\n".join(lines)

        total = int(rag_context.get("total_matches") or len(matches))
        displayed = int(rag_context.get("displayed_matches") or len(matches))
        is_truncated = bool(rag_context.get("is_truncated"))
        total_sources = int(rag_context.get("total_sources") or 0)

        if total > displayed:
            headline = (
                f"Score global de similarité : {score_pct}. "
                f"{total} passages similaires détectés, dont {displayed} affichés "
                f"dans le rapport (regroupés sur {total_sources} document(s) source)."
            )
        else:
            headline = (
                f"Score global de similarité : {score_pct}. "
                f"{displayed} passage(s) similaire(s) détecté(s) "
                f"sur {total_sources} document(s) source :"
                if total_sources
                else (
                    f"Score global de similarité : {score_pct}. "
                    f"{displayed} passage(s) similaire(s) détecté(s) :"
                )
            )

        lines.append(headline)
        if is_truncated:
            lines.append(
                "  Les résultats ont été limités pour la lisibilité. Les "
                "passages affichés correspondent aux scores les plus élevés "
                "après déduplication."
            )

        for index, match in enumerate(matches, start=1):
            if not isinstance(match, dict):
                continue
            lines.append(self._format_match_block(index, match))
        return "\n".join(lines)

    def _format_match_block(self, index: int, match: dict[str, Any]) -> str:
        original_filename = self._clean_str(
            match.get("original_filename")
        ) or "non disponible"
        stored_filename = self._clean_str(
            match.get("stored_filename")
            or match.get("filename")
            or match.get("matched_chunk_id")
        ) or "non disponible"
        scenario_id = self._clean_str(match.get("matched_scenario_id")) or "non disponible"

        score_value = (
            match.get("similarity_score")
            or match.get("similarity")
            or match.get("score")
            or 0.0
        )
        score_pct = self._format_percent(score_value)

        # Display-only field selection. Detection, scoring, deduplication
        # and aggregation are untouched: we only choose which slice of text
        # to render in the "Extrait" field of the report.
        #
        # `snippet` is built by `build_plagiarism_snippet`: it's centred on
        # the actual overlap, anti-boilerplate weighted, and expanded to a
        # readable 250-400 char window. It is therefore the right field to
        # show when present.
        #
        # The remaining fields stay in the chain as fallbacks for matches
        # produced by other paths (e.g. legacy data, custom integrations)
        # that don't expose a polished snippet.
        extract_source = (
            match.get("snippet")
            or match.get("overlap_text")
            or match.get("common_text")
            or match.get("matched_text")
            or match.get("matched_chunk_text_display")
            or match.get("matched_chunk_text")
            # Legacy fallbacks kept for backward compatibility.
            or match.get("matched_chunk_text_original")
            or match.get("snippet_original")
            or match.get("display_text")
            or match.get("original_text")
            or match.get("raw_text")
        )
        extract = self._truncate(extract_source, self.MAX_MATCH_EXTRACT)

        parts = [
            f"  Match {index}",
            f"    Nom original : {original_filename}",
            f"    Nom stocké : {stored_filename}",
            f"    Scénario source : {scenario_id}",
            f"    Score : {score_pct}",
        ]
        if extract:
            parts.append(f"    Extrait : {extract}")
        return "\n".join(parts)

    # ---------- Moderation ----------

    def _build_moderation_explanation(self, rag_context: dict[str, Any]) -> str:
        sections: list[str] = []

        # Vulgarity headline
        if rag_context["contains_profanity"]:
            score_value = self._format_score_over_100(rag_context["profanity_score"])
            words = ", ".join(rag_context["detected_words"]) or "(aucun mot listé)"
            sections.append(
                f"Score de vulgarité : {score_value}. Mots détectés : {words}."
            )
        else:
            sections.append("Aucune vulgarité significative n'a été détectée.")

        # Grouped word summary
        grouped_block = self._format_vulgarity_summary_block(
            rag_context["vulgarity_matches"]
        )
        if grouped_block:
            sections.append(grouped_block)

        # Detailed per-occurrence passages
        vulgarity_block = self._format_vulgarity_matches_block(
            rag_context["vulgarity_matches"]
        )
        if vulgarity_block:
            sections.append(vulgarity_block)

        # Adult content — always show the explicit score line.
        adult_score = self._format_score_over_100(rag_context["adult_content_score"])
        if rag_context["contains_adult_content"]:
            terms = ", ".join(
                str(term) for term in rag_context["detected_terms"] if term
            ) or "(aucun terme listé)"
            sections.append(
                f"Score contenu adulte : {adult_score} (niveau : "
                f"{rag_context['adult_risk_level']}). "
                "Contenu adulte significatif détecté. "
                f"Termes détectés : {terms}."
            )
        else:
            sections.append(
                f"Score contenu adulte : {adult_score}. "
                "Aucun contenu adulte significatif n'a été détecté."
            )

        return "\n\n".join(sections)

    def _format_vulgarity_summary_block(
        self,
        vulgarity_matches: list[dict[str, Any]],
    ) -> str:
        groups = self._group_vulgarity_matches(vulgarity_matches)
        if not groups:
            return ""

        lines = ["Résumé des mots détectés"]
        for display_word, info in groups.items():
            language = self._translate_language(info["language"])
            category = self._translate_category(info["category"])
            lines.append(
                f"  - {display_word} : {info['count']} occurrence(s), "
                f"langue {language}, catégorie {category}"
            )
        return "\n".join(lines)

    def _format_vulgarity_matches_block(
        self,
        vulgarity_matches: list[dict[str, Any]],
    ) -> str:
        if not vulgarity_matches:
            return ""

        lines: list[str] = ["Passages contenant des mots vulgaires :"]
        for index, match in enumerate(vulgarity_matches, start=1):
            if not isinstance(match, dict):
                continue
            word = self._clean_str(match.get("word")) or "(mot inconnu)"
            language = self._translate_language(match.get("language"))
            category = self._translate_category(match.get("category"))
            snippet = self._truncate(match.get("snippet"), self.MAX_SNIPPET_LENGTH)
            block = [
                f"  {index}. Mot détecté : {word}",
                f"     Langue : {language}",
                f"     Catégorie : {category}",
            ]
            if snippet:
                block.append(f'     Passage : "{snippet}"')
            lines.append("\n".join(block))
        return "\n".join(lines)

    # ---------- Recommendations & conclusion ----------

    def _build_recommendations(
        self,
        rag_context: dict[str, Any],
        risk_level: str,
    ) -> list[str]:
        recommendations: list[str] = []

        similarity = rag_context["global_similarity_score"]
        profanity = rag_context["profanity_score"]
        adult = rag_context["adult_content_score"]
        matches_count = len(rag_context["matches"])
        occurrences = rag_context.get("profanity_occurrences", 0)
        similarity_pct = self._format_percent(similarity)

        if similarity >= 0.8:
            recommendations.append(
                "Vérification manuelle indispensable : score de similarité très "
                f"élevé ({similarity_pct})."
            )
        elif similarity >= 0.4 or matches_count > 0:
            recommendations.append(
                "Vérifier manuellement les passages similaires avant validation "
                f"(score actuel : {similarity_pct}, {matches_count} "
                "correspondance(s))."
            )

        if profanity > 0:
            recommendations.append(
                "Reformuler les passages contenant des mots vulgaires "
                f"({occurrences} occurrence(s) détectée(s), score "
                f"{self._format_score_over_100(profanity)})."
            )

        if adult > 0:
            recommendations.append(
                "Vérifier le contenu adulte selon la politique de modération "
                f"(score {self._format_score_over_100(adult)})."
            )

        if not recommendations:
            recommendations.append(
                "Le document ne présente pas de risque significatif ; "
                "aucune action corrective n'est requise."
            )

        # Always end with the traceability instruction so the audit trail is
        # explicit, regardless of the risk level.
        recommendations.append(
            "Conserver une trace de la décision finale dans l'historique de "
            "l'analyse."
        )

        return recommendations

    def _build_conclusion(
        self,
        rag_context: dict[str, Any],
        risk_level: str,
        justification: str,
    ) -> str:
        """Build the final closing paragraph of the report."""
        if risk_level == "high":
            base = (
                "Le document doit être revu manuellement avant validation. "
                f"Le risque {self._risk_level_label_fr(risk_level)} est "
                f"principalement lié à {self._reason_short(rag_context)}."
            )
        elif risk_level == "medium":
            base = (
                "Le document présente des signaux modérés et doit être vérifié "
                "avant validation. "
                f"Points d'attention : {self._reason_short(rag_context)}."
            )
        else:
            base = (
                "Le document ne présente pas de risque significatif selon les "
                "critères analysés."
            )
        return base

    def _reason_short(self, rag_context: dict[str, Any]) -> str:
        """Short bullet-style summary of risk drivers for the conclusion."""
        drivers: list[str] = []
        similarity = rag_context["global_similarity_score"]
        profanity = rag_context["profanity_score"]
        adult = rag_context["adult_content_score"]

        if similarity >= 0.4 or rag_context["matches"]:
            drivers.append(
                f"la similarité détectée ({self._format_percent(similarity)})"
            )

        if profanity > 0:
            languages = self._summarize_vulgarity_languages(rag_context)
            tail = f" en {languages}" if languages else ""
            drivers.append(
                f"la présence de termes vulgaires{tail}"
            )

        if adult > 0:
            drivers.append("la présence de contenu adulte")

        if not drivers:
            return "aux signaux faibles détectés"

        if len(drivers) == 1:
            return drivers[0]
        if len(drivers) == 2:
            return f"{drivers[0]} et {drivers[1]}"
        return ", ".join(drivers[:-1]) + f" et {drivers[-1]}"

    # ---------- Final report ----------

    def _build_generated_report(
        self,
        scenario_id: str,
        summary: str,
        risk_level: str,
        plagiarism_explanation: str,
        moderation_explanation: str,
        recommendations: list[str],
        conclusion: str,
        document_stats: dict[str, Any],
        rag_context: dict[str, Any],
    ) -> str:
        stats_block = self._format_document_stats(document_stats)
        recommendations_text = (
            "\n".join(f"- {rec}" for rec in recommendations) or "- (aucune)"
        )

        return (
            f"Rapport d'analyse du scénario {scenario_id}\n"
            f"Niveau de risque : {risk_level.upper()}\n"
            "\n"
            "Résumé\n"
            f"{summary}\n"
            "\n"
            "Statistiques du document\n"
            f"{stats_block}\n"
            "\n"
            "Analyse plagiat\n"
            f"{plagiarism_explanation}\n"
            "\n"
            "Analyse modération\n"
            f"{moderation_explanation}\n"
            "\n"
            "Recommandations\n"
            f"{recommendations_text}\n"
            "\n"
            "Conclusion\n"
            f"{conclusion}"
        )

    def _format_document_stats(self, document_stats: dict[str, Any]) -> str:
        if not isinstance(document_stats, dict) or not document_stats:
            return "- (aucune statistique disponible)"

        rows = [
            ("Nom du fichier original", document_stats.get("original_filename")),
            ("Nom stocké", document_stats.get("file_name")),
            (
                "Nombre de mots",
                document_stats.get("words_count")
                or document_stats.get("word_count"),
            ),
            (
                "Nombre de chunks",
                document_stats.get("chunks_count")
                or document_stats.get("chunk_count"),
            ),
            (
                "Caractères extraits",
                document_stats.get("raw_characters_count"),
            ),
            (
                "Caractères nettoyés",
                document_stats.get("cleaned_characters_count"),
            ),
        ]

        lines = []
        for label, value in rows:
            if value in (None, ""):
                continue
            lines.append(f"- {label} : {value}")

        return "\n".join(lines) if lines else "- (aucune statistique disponible)"

    # ---------- Grouping & translation helpers ----------

    def _group_vulgarity_matches(
        self,
        vulgarity_matches: list[dict[str, Any]],
    ) -> "OrderedDict[str, dict[str, Any]]":
        """Group matches by normalized word, keeping insertion order."""
        groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
        for match in vulgarity_matches:
            if not isinstance(match, dict):
                continue
            word = self._clean_str(match.get("word"))
            if not word:
                continue
            key = self._word_group_key(word)
            display = self._display_word_for_group(word)
            entry = groups.get(key)
            if entry is None:
                groups[key] = {
                    "display_word": display,
                    "count": 1,
                    "language": match.get("language") or "",
                    "category": match.get("category") or "",
                }
            else:
                entry["count"] += 1
        # Re-key by display word for stable presentation.
        return OrderedDict(
            (info["display_word"], info) for info in groups.values()
        )

    @staticmethod
    def _word_group_key(word: str) -> str:
        """Lowercase Latin words for grouping; keep non-Latin words intact."""
        if _LATIN_TOKEN_RE.match(word):
            return word.lower()
        return word

    @staticmethod
    def _display_word_for_group(word: str) -> str:
        """Use lowercase form for Latin words so 'Putain' + 'putain' merge cleanly."""
        if _LATIN_TOKEN_RE.match(word):
            return word.lower()
        return word

    @staticmethod
    def _dedupe_words(words: list[str]) -> list[str]:
        """Remove case-insensitive duplicates from a French word list."""
        seen: set[str] = set()
        result: list[str] = []
        for raw in words:
            if not isinstance(raw, str):
                continue
            word = raw.strip()
            if not word:
                continue
            key = word.lower() if _LATIN_TOKEN_RE.match(word) else word
            if key in seen:
                continue
            seen.add(key)
            result.append(word)
        return result

    @staticmethod
    def _translate_category(category: Any) -> str:
        if category is None:
            return CATEGORY_LABELS_FR[None]
        key = str(category).strip()
        label = CATEGORY_LABELS_FR.get(key)
        if label is None:
            label = CATEGORY_LABELS_FR.get(key.lower())
        if label is not None:
            return label
        return f"non classé ({key})" if key else "non classé"

    @staticmethod
    def _translate_language(language: Any) -> str:
        if language is None:
            return LANGUAGE_LABELS_FR[""]
        key = str(language).strip()
        return LANGUAGE_LABELS_FR.get(key, key or LANGUAGE_LABELS_FR[""])

    def _summarize_vulgarity_languages(self, rag_context: dict[str, Any]) -> str:
        languages: list[str] = []
        seen: set[str] = set()
        for match in rag_context["vulgarity_matches"]:
            if not isinstance(match, dict):
                continue
            label = self._translate_language(match.get("language"))
            if label in seen or label == "inconnue":
                continue
            seen.add(label)
            languages.append(label)
        if not languages:
            return ""
        if len(languages) == 1:
            return languages[0]
        if len(languages) == 2:
            return f"{languages[0]} et {languages[1]}"
        return ", ".join(languages[:-1]) + f" et {languages[-1]}"

    # ---------- Helpers ----------

    @staticmethod
    def _clean_str(value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if text.lower() in {"none", "null"}:
            return ""
        return text

    @staticmethod
    def _truncate(value: Any, max_length: int) -> str:
        if value is None:
            return ""
        text = " ".join(str(value).split())
        if not text:
            return ""
        if len(text) <= max_length:
            return text
        return text[:max_length].rstrip() + "..."

    @staticmethod
    def _format_percent(value: Any) -> str:
        try:
            number = float(value or 0.0)
        except (TypeError, ValueError):
            number = 0.0
        if number <= 1:
            number *= 100
        return f"{number:.2f}%"

    @staticmethod
    def _format_score_over_100(value: Any) -> str:
        """Render a 0..100 moderation score with an explicit unit.

        Accepts both fractions (0..1) and percentages (0..100); fractions
        are scaled up so the rendered figure matches the indicator badge.
        """
        try:
            number = float(value or 0.0)
        except (TypeError, ValueError):
            number = 0.0
        if 0 < number <= 1:
            number *= 100
        return f"{number:.2f} / 100"

    @staticmethod
    def _risk_level_label_fr(risk_level: str) -> str:
        mapping = {
            "low": "FAIBLE",
            "medium": "MODÉRÉ",
            "high": "ÉLEVÉ",
            "tres_eleve": "TRÈS ÉLEVÉ",
            "très élevé": "TRÈS ÉLEVÉ",
        }
        return mapping.get(str(risk_level or "").lower(), str(risk_level).upper())

    def _validate_inputs(
        self,
        scenario_id: str,
        plagiarism_result: dict[str, Any],
        profanity_result: dict[str, Any],
        adult_content_result: dict[str, Any],
        document_stats: dict[str, Any],
    ) -> None:
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise ValueError("scenario_id must not be empty")

        inputs = {
            "plagiarism_result": plagiarism_result,
            "profanity_result": profanity_result,
            "adult_content_result": adult_content_result,
            "document_stats": document_stats,
        }
        for name, value in inputs.items():
            if not isinstance(value, dict):
                raise TypeError(f"{name} must be a dictionary")
