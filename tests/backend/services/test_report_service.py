from backend.services.report_service import ReportService, example_usage


def test_generate_pdf_report_returns_pdf_bytes() -> None:
    service = ReportService()

    pdf = service.generate_pdf_report(
        {
            "scenario_id": "scenario-test",
            "analysis_timestamp": "2026-06-03T22:15:00",
            "document_stats": {"words_count": 250, "chunks_count": 2},
            "plagiarism": {
                "global_similarity_score": 0.65,
                "matches": [
                    {
                        "similarity_score": 0.82,
                        "matched_scenario_id": "scenario-source",
                        "chunk_text": "Passage analyse.",
                        "matched_chunk_text": "Passage similaire.",
                    }
                ],
            },
            "profanity": {
                "profanity_score": 0,
                "detected_words": [],
            },
            "adult_content": {
                "adult_content_score": 0,
                "risk_level": "low",
                "detected_terms": [],
            },
            "rag_report": {
                "summary": "Resume de test.",
                "risk_level": "medium",
                "plagiarism_explanation": "Similarite detectee.",
                "moderation_explanation": "Aucune alerte.",
                "recommendations": ["Verifier les passages similaires."],
            },
        }
    )

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_example_usage_returns_pdf_bytes() -> None:
    pdf = example_usage()

    assert pdf.startswith(b"%PDF")
