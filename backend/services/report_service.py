from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


class ReportService:
    """Generate PDF analysis reports from structured analysis results."""

    def generate_pdf_report(self, analysis_result: dict[str, Any]) -> bytes:
        """Generate a complete PDF report from one analysis result."""
        if not isinstance(analysis_result, dict):
            raise TypeError("analysis_result must be a dictionary")

        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=1.8 * cm,
            leftMargin=1.8 * cm,
            topMargin=1.8 * cm,
            bottomMargin=1.8 * cm,
            title="Rapport d'analyse de scenario",
        )

        styles = self._build_styles()
        story: list[Any] = []

        self._add_cover_page(story, analysis_result, styles)
        story.append(PageBreak())
        self._add_document_statistics(story, analysis_result, styles)
        self._add_plagiarism_section(story, analysis_result, styles)
        self._add_profanity_section(story, analysis_result, styles)
        self._add_adult_content_section(story, analysis_result, styles)
        self._add_rag_section(story, analysis_result, styles)
        self._add_similar_passages_section(story, analysis_result, styles)

        document.build(story)
        return buffer.getvalue()

    def _build_styles(self) -> dict[str, ParagraphStyle]:
        """Build reusable paragraph styles for the PDF document."""
        base_styles = getSampleStyleSheet()
        return {
            "title": ParagraphStyle(
                "ReportTitle",
                parent=base_styles["Title"],
                fontName="Helvetica-Bold",
                fontSize=22,
                leading=28,
                alignment=TA_CENTER,
                textColor=colors.HexColor("#1F2937"),
                spaceAfter=18,
            ),
            "subtitle": ParagraphStyle(
                "ReportSubtitle",
                parent=base_styles["Normal"],
                fontName="Helvetica",
                fontSize=11,
                leading=16,
                alignment=TA_CENTER,
                textColor=colors.HexColor("#4B5563"),
                spaceAfter=10,
            ),
            "heading": ParagraphStyle(
                "SectionHeading",
                parent=base_styles["Heading2"],
                fontName="Helvetica-Bold",
                fontSize=14,
                leading=18,
                textColor=colors.HexColor("#111827"),
                spaceBefore=14,
                spaceAfter=8,
            ),
            "body": ParagraphStyle(
                "Body",
                parent=base_styles["BodyText"],
                fontName="Helvetica",
                fontSize=9.5,
                leading=13,
                textColor=colors.HexColor("#1F2937"),
                spaceAfter=6,
            ),
            "small": ParagraphStyle(
                "Small",
                parent=base_styles["BodyText"],
                fontName="Helvetica",
                fontSize=8.5,
                leading=11,
                textColor=colors.HexColor("#374151"),
                spaceAfter=5,
            ),
        }

    def _add_cover_page(
        self,
        story: list[Any],
        analysis_result: dict[str, Any],
        styles: dict[str, ParagraphStyle],
    ) -> None:
        """Add the report cover page."""
        scenario_id = self._get_text(analysis_result.get("scenario_id"), "n/a")
        analysis_date = self._get_text(analysis_result.get("analysis_timestamp"), "n/a")

        story.append(Spacer(1, 5 * cm))
        story.append(Paragraph("Rapport d'analyse de scenario", styles["title"]))
        story.append(Paragraph(f"Scenario ID: {scenario_id}", styles["subtitle"]))
        story.append(Paragraph(f"Date d'analyse: {analysis_date}", styles["subtitle"]))

    def _add_document_statistics(
        self,
        story: list[Any],
        analysis_result: dict[str, Any],
        styles: dict[str, ParagraphStyle],
    ) -> None:
        """Add document statistics section."""
        document_stats = self._get_dict(analysis_result, "document_stats")
        words = self._first_available(document_stats, "words_count", "word_count")
        chunks = self._first_available(document_stats, "chunks_count", "chunk_count")

        story.append(Paragraph("1. Statistiques du document", styles["heading"]))
        self._add_key_value_table(
            story,
            [
                ("Nombre de mots", self._get_text(words, "0")),
                ("Nombre de chunks", self._get_text(chunks, "0")),
            ],
            styles,
        )

    def _add_plagiarism_section(
        self,
        story: list[Any],
        analysis_result: dict[str, Any],
        styles: dict[str, ParagraphStyle],
    ) -> None:
        """Add plagiarism detection section."""
        plagiarism = self._get_dict(analysis_result, "plagiarism")
        rag_report = self._get_dict(analysis_result, "rag_report")
        matches = plagiarism.get("matches", [])
        if not isinstance(matches, list):
            matches = []

        story.append(Paragraph("2. Detection de plagiat", styles["heading"]))
        self._add_key_value_table(
            story,
            [
                (
                    "Score de similarite",
                    self._format_score(plagiarism.get("global_similarity_score", 0.0)),
                ),
                ("Niveau de risque", self._get_text(rag_report.get("risk_level"), "unknown")),
                ("Correspondances trouvees", str(len(matches))),
            ],
            styles,
        )

    def _add_profanity_section(
        self,
        story: list[Any],
        analysis_result: dict[str, Any],
        styles: dict[str, ParagraphStyle],
    ) -> None:
        """Add profanity moderation section."""
        profanity = self._get_dict(analysis_result, "profanity")
        detected_words = profanity.get("detected_words", [])

        story.append(Paragraph("3. Vulgarite", styles["heading"]))
        self._add_key_value_table(
            story,
            [
                ("Score", self._format_score(profanity.get("profanity_score", 0.0))),
                ("Mots detectes", self._join_values(detected_words)),
            ],
            styles,
        )

    def _add_adult_content_section(
        self,
        story: list[Any],
        analysis_result: dict[str, Any],
        styles: dict[str, ParagraphStyle],
    ) -> None:
        """Add adult-content moderation section."""
        adult_content = self._get_dict(analysis_result, "adult_content")
        detected_terms = adult_content.get("detected_terms", [])

        story.append(Paragraph("4. Contenu adulte", styles["heading"]))
        self._add_key_value_table(
            story,
            [
                (
                    "Score",
                    self._format_score(adult_content.get("adult_content_score", 0.0)),
                ),
                ("Niveau de risque", self._get_text(adult_content.get("risk_level"), "low")),
                ("Termes detectes", self._join_values(detected_terms)),
            ],
            styles,
        )

    def _add_rag_section(
        self,
        story: list[Any],
        analysis_result: dict[str, Any],
        styles: dict[str, ParagraphStyle],
    ) -> None:
        """Add RAG report section."""
        rag_report = self._get_dict(analysis_result, "rag_report")
        recommendations = rag_report.get("recommendations", [])
        if not isinstance(recommendations, list):
            recommendations = []

        story.append(Paragraph("5. Rapport RAG", styles["heading"]))
        self._add_paragraph(story, "Resume", rag_report.get("summary"), styles)
        self._add_paragraph(
            story,
            "Explication du plagiat",
            rag_report.get("plagiarism_explanation"),
            styles,
        )
        self._add_paragraph(
            story,
            "Explication de la moderation",
            rag_report.get("moderation_explanation"),
            styles,
        )
        self._add_paragraph(
            story,
            "Recommandations",
            "\n".join(f"- {item}" for item in recommendations),
            styles,
        )

    def _add_similar_passages_section(
        self,
        story: list[Any],
        analysis_result: dict[str, Any],
        styles: dict[str, ParagraphStyle],
    ) -> None:
        """Add similar passages section."""
        plagiarism = self._get_dict(analysis_result, "plagiarism")
        matches = plagiarism.get("matches", [])
        if not isinstance(matches, list):
            matches = []

        story.append(Paragraph("6. Passages similaires", styles["heading"]))
        if not matches:
            story.append(
                Paragraph("Aucun passage similaire detecte.", styles["body"])
            )
            return

        for index, match in enumerate(matches, start=1):
            if not isinstance(match, dict):
                continue

            score = self._format_score(match.get("similarity_score", 0.0))
            matched_scenario = self._get_text(match.get("matched_scenario_id"), "inconnu")
            source_text = self._truncate(match.get("chunk_text"), 900)
            matched_text = self._truncate(match.get("matched_chunk_text"), 900)

            story.append(Paragraph(f"Match {index}", styles["heading"]))
            self._add_key_value_table(
                story,
                [
                    ("Score", score),
                    ("Scenario correspondant", matched_scenario),
                ],
                styles,
            )
            self._add_paragraph(story, "Extrait du chunk analyse", source_text, styles)
            self._add_paragraph(story, "Extrait du chunk similaire", matched_text, styles)

    def _add_key_value_table(
        self,
        story: list[Any],
        rows: list[tuple[str, str]],
        styles: dict[str, ParagraphStyle],
    ) -> None:
        """Add a compact key-value table."""
        table_data = [
            [
                Paragraph(label, styles["small"]),
                Paragraph(self._escape_text(value), styles["small"]),
            ]
            for label, value in rows
        ]
        table = Table(table_data, colWidths=[5.2 * cm, 10.8 * cm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F3F4F6")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E5E7EB")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 0.25 * cm))

    def _add_paragraph(
        self,
        story: list[Any],
        title: str,
        text: Any,
        styles: dict[str, ParagraphStyle],
    ) -> None:
        """Add a titled text block."""
        story.append(Paragraph(f"<b>{self._escape_text(title)}</b>", styles["body"]))
        story.append(
            Paragraph(
                self._escape_text(self._get_text(text, "Non disponible.")),
                styles["body"],
            )
        )

    def _get_dict(self, data: dict[str, Any], key: str) -> dict[str, Any]:
        """Return a nested dictionary or an empty dictionary."""
        value = data.get(key)
        return value if isinstance(value, dict) else {}

    def _first_available(self, data: dict[str, Any], *keys: str) -> Any:
        """Return the first available dictionary value."""
        for key in keys:
            if key in data:
                return data[key]
        return None

    def _format_score(self, value: Any) -> str:
        """Format numeric scores as percentages."""
        try:
            number = float(value or 0.0)
        except (TypeError, ValueError):
            number = 0.0

        if number <= 1:
            number *= 100

        return f"{number:.2f}%"

    def _join_values(self, values: Any) -> str:
        """Join list values for display."""
        if not isinstance(values, list) or not values:
            return "Aucun"
        return ", ".join(map(str, values))

    def _truncate(self, value: Any, max_length: int) -> str:
        """Return a readable excerpt with a maximum length."""
        text = self._get_text(value, "Texte non disponible.")
        if len(text) <= max_length:
            return text
        return f"{text[:max_length].rstrip()}..."

    def _get_text(self, value: Any, default: str) -> str:
        """Return a printable string value."""
        if value is None or value == "":
            return default
        return str(value)

    def _escape_text(self, value: Any) -> str:
        """Escape text for ReportLab Paragraph markup."""
        text = self._get_text(value, "")
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br/>")
        )


def example_usage() -> bytes:
    """Example usage for generating a PDF report in tests or scripts."""
    sample_result = {
        "scenario_id": "example-scenario",
        "analysis_timestamp": "2026-06-03T22:15:00",
        "document_stats": {"words_count": 1200, "chunks_count": 4},
        "plagiarism": {
            "global_similarity_score": 0.42,
            "matches": [
                {
                    "similarity_score": 0.83,
                    "matched_scenario_id": "scenario-reference",
                    "chunk_text": "Extrait du scenario analyse.",
                    "matched_chunk_text": "Extrait similaire retrouve.",
                }
            ],
        },
        "profanity": {"profanity_score": 0.0, "detected_words": []},
        "adult_content": {
            "adult_content_score": 0.0,
            "risk_level": "low",
            "detected_terms": [],
        },
        "rag_report": {
            "summary": "Exemple de resume RAG.",
            "risk_level": "medium",
            "plagiarism_explanation": "Un passage similaire est detecte.",
            "moderation_explanation": "Aucune alerte de moderation.",
            "recommendations": ["Verifier manuellement le passage similaire."],
        },
    }
    return ReportService().generate_pdf_report(sample_result)
