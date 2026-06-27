"""Aggregation/dedupe/grouping behavior of PlagiarismPipeline._merge_plagiarism_results."""

from typing import Any
from unittest.mock import Mock

from backend.core.config import settings
from backend.pipelines.plagiarism_pipeline import PlagiarismPipeline
from backend.services.text_cleaning_service import TextCleaningService


def _build_service() -> PlagiarismPipeline:
    return PlagiarismPipeline(
        local_similarity_service=Mock(),
        plagiarism_service=Mock(),
        strict_similarity_service=Mock(),
        embedding_service=Mock(),
        vector_service=Mock(),
        minhash_service=Mock(),
    )


def _vector_match(
    scenario_id: str,
    chunk_index: int,
    matched_text: str,
    score: float,
) -> dict[str, Any]:
    return {
        "chunk_index": chunk_index,
        "current_chunk_id": f"current_{chunk_index}",
        "chunk_text": f"current chunk {chunk_index}",
        "matched_scenario_id": scenario_id,
        "matched_chunk_id": f"{scenario_id}_{chunk_index}",
        "source_chunk_id": f"{scenario_id}_{chunk_index}",
        "matched_chunk_text": matched_text,
        "matched_chunk_text_display": matched_text,
        "similarity_score": score,
    }


def test_aggregation_dedupes_identical_snippets_from_same_source() -> None:
    service = _build_service()
    raw = [
        _vector_match("S2", 0, "extrait identique", 0.95),
        _vector_match("S2", 0, "extrait identique", 0.95),
        _vector_match("S2", 1, "extrait identique", 0.90),  # same snippet, lower
    ]

    merged = service._merge_plagiarism_results(
        scenario_id="S1",
        local_result={"score": 0.0, "matches": []},
        vector_result={"global_similarity_score": 0.95, "matches": raw},
    )

    # Same source + same snippet → collapsed into one match with grouped_copies.
    assert merged["total_matches"] == 1
    assert merged["displayed_matches"] == 1
    only = merged["matches"][0]
    assert only.get("grouped_copies") == 3


def test_aggregation_groups_matches_by_source_document() -> None:
    service = _build_service()
    raw = [
        _vector_match("S2", 0, "passage A unique", 0.95),
        _vector_match("S2", 1, "passage B unique", 0.90),
        _vector_match("S3", 0, "autre source A", 0.85),
        _vector_match("S3", 1, "autre source B", 0.80),
    ]

    merged = service._merge_plagiarism_results(
        scenario_id="S1",
        local_result={"score": 0.0, "matches": []},
        vector_result={"global_similarity_score": 0.95, "matches": raw},
    )

    sources = merged["plagiarism_sources"]
    assert merged["total_sources"] == 2
    assert merged["displayed_sources"] == 2
    assert len(sources) == 2

    # First source should be the one with the highest best_score.
    assert sources[0]["source_scenario_id"] == "S2"
    assert sources[0]["best_score"] == 0.95
    assert sources[0]["matches_count"] == 2
    assert sources[1]["source_scenario_id"] == "S3"
    assert sources[1]["best_score"] == 0.85


def test_aggregation_truncates_when_too_many_matches() -> None:
    service = _build_service()
    # Build 60 distinct matches across 10 sources.
    raw: list[dict[str, Any]] = []
    for source_idx in range(10):
        for chunk_idx in range(6):
            raw.append(
                _vector_match(
                    scenario_id=f"S{source_idx}",
                    chunk_index=chunk_idx,
                    matched_text=f"distinct text {source_idx}-{chunk_idx}",
                    score=0.9 - source_idx * 0.05 - chunk_idx * 0.001,
                )
            )

    merged = service._merge_plagiarism_results(
        scenario_id="current",
        local_result={"score": 0.0, "matches": []},
        vector_result={"global_similarity_score": 0.9, "matches": raw},
    )

    assert merged["total_matches"] == 60
    assert merged["total_sources"] == 10
    assert merged["displayed_sources"] == PlagiarismPipeline.MAX_SOURCES_DISPLAYED
    assert merged["is_truncated"] is True
    # Displayed matches respect both per-source and global caps.
    assert merged["displayed_matches"] <= PlagiarismPipeline.MAX_TOTAL_MATCHES_DISPLAYED
    for source in merged["plagiarism_sources"]:
        assert len(source["matches"]) <= PlagiarismPipeline.MAX_MATCHES_PER_SOURCE


def test_aggregation_keeps_best_score_per_dedup_key() -> None:
    service = _build_service()
    raw = [
        _vector_match("S2", 0, "même extrait", 0.70),
        _vector_match("S2", 0, "même extrait", 0.99),  # better score
        _vector_match("S2", 0, "même extrait", 0.85),
    ]
    merged = service._merge_plagiarism_results(
        scenario_id="S1",
        local_result={"score": 0.0, "matches": []},
        vector_result={"global_similarity_score": 0.99, "matches": raw},
    )
    assert merged["displayed_matches"] == 1
    assert merged["matches"][0]["similarity_score"] == 0.99
    assert merged["matches"][0]["grouped_copies"] == 3


def test_aggregation_handles_empty_input() -> None:
    service = _build_service()
    merged = service._merge_plagiarism_results(
        scenario_id="S1",
        local_result={"score": 0.0, "matches": []},
        vector_result={"global_similarity_score": 0.0, "matches": []},
    )
    assert merged["total_matches"] == 0
    assert merged["displayed_matches"] == 0
    assert merged["total_sources"] == 0
    assert merged["is_truncated"] is False
    assert merged["matches"] == []
    assert merged["plagiarism_sources"] == []


def test_plagiarism_service_excludes_specific_scenarios() -> None:
    """Qdrant matches from excluded scenario_ids must be dropped."""
    from backend.services.plagiarism_service import PlagiarismService

    # Use texts with enough shared informative tokens (5+ consecutive) so
    # the MIN_TEXTUAL_EVIDENCE / is_likely_false_positive filters don't drop
    # the legitimate match before the exclusion logic is exercised.
    _shared = "Yasmine decouvre boite cachee plancher ancien grenier immeuble fouille"
    _current = _shared + " enquete approfondie"
    _source = _shared + " pendant inspection nocturne"

    embedding_service = Mock()
    embedding_service.generate_embeddings.return_value = [[0.1] * 3]
    vector_service = Mock()
    vector_service.search_similar_chunks.return_value = [
        {
            "id": "p1",
            "score": 0.95,
            "payload": {
                "scenario_id": "EXCLUDED",
                "chunk_id": "EXCLUDED_0",
                "chunk_text": _source,
            },
        },
        {
            "id": "p2",
            "score": 0.92,
            "payload": {
                "scenario_id": "OTHER",
                "chunk_id": "OTHER_0",
                "chunk_text": _source,
            },
        },
    ]

    service = PlagiarismService(
        embedding_service=embedding_service,
        vector_service=vector_service,
    )
    result = service.analyze_chunks(
        scenario_id="current",
        chunks=[_current],
        similarity_threshold=0.5,
        top_k=5,
        excluded_scenario_ids={"EXCLUDED"},
    )
    assert len(result["matches"]) == 1
    assert result["matches"][0]["matched_scenario_id"] == "OTHER"


def test_aggregation_groups_local_and_vector_matches_together() -> None:
    """Local exact duplicate and vector matches must merge by source where applicable."""
    service = _build_service()
    local_match = {
        "filename": "previous.pdf",
        "stored_filename": "previous.pdf",
        "original_filename": "scenario.pdf",
        "matched_scenario_id": "S2",
        "similarity_score": 1.0,
        "matched_chunk_text": "doublon exact",
        "duplicate": True,
    }
    vector_match = _vector_match("S2", 0, "autre passage de S2", 0.85)

    merged = service._merge_plagiarism_results(
        scenario_id="S1",
        local_result={"score": 1.0, "matches": [local_match]},
        vector_result={
            "global_similarity_score": 0.85,
            "matches": [vector_match],
        },
    )

    # Both matches share matched_scenario_id=S2 → grouped into one source.
    assert merged["total_sources"] == 1
    source = merged["plagiarism_sources"][0]
    assert source["source_scenario_id"] == "S2"
    assert source["matches_count"] == 1
    assert merged["total_matches"] == 1


def test_aggregation_groups_sources_by_source_hash_before_filename() -> None:
    service = _build_service()
    raw = [
        {
            **_vector_match("S2", 0, "passage A", 0.95),
            "source_file_hash": "same-hash",
            "original_filename": "copy-a.pdf",
        },
        {
            **_vector_match("S3", 1, "passage B", 0.91),
            "source_file_hash": "same-hash",
            "original_filename": "copy-b.pdf",
        },
    ]

    merged = service._merge_plagiarism_results(
        scenario_id="S1",
        local_result={"score": 0.0, "matches": []},
        vector_result={"global_similarity_score": 0.95, "matches": raw},
    )

    assert merged["total_sources"] == 1
    assert merged["plagiarism_sources"][0]["matches_count"] == 2


def test_aggregation_keeps_separate_passages_from_same_source() -> None:
    service = _build_service()
    raw = [
        {
            **_vector_match("S2", 0, "passage copie numero un", 0.95),
            "current_page_number": 1,
            "source_page_number": 2,
            "source_chunk_id": "S2_10",
        },
        {
            **_vector_match("S2", 1, "passage copie numero deux", 0.93),
            "current_page_number": 5,
            "source_page_number": 8,
            "source_chunk_id": "S2_11",
        },
    ]

    merged = service._merge_plagiarism_results(
        scenario_id="S1",
        local_result={"score": 0.0, "matches": []},
        vector_result={"global_similarity_score": 0.95, "matches": raw},
    )

    assert merged["total_matches"] == 2
    assert merged["total_sources"] == 1
    assert merged["plagiarism_sources"][0]["matches_count"] == 2


def test_aggregation_keeps_five_distinct_passages() -> None:
    service = _build_service()
    raw = [
        {
            **_vector_match("S2", index, f"passage distinct {index}", 0.9),
            "current_page_number": index + 1,
            "source_page_number": index + 10,
            "source_chunk_id": f"S2_{index}",
        }
        for index in range(5)
    ]

    merged = service._merge_plagiarism_results(
        scenario_id="S1",
        local_result={"score": 0.0, "matches": []},
        vector_result={"global_similarity_score": 0.9, "matches": raw},
    )

    assert merged["total_matches"] == 5
    assert len(merged["matches"]) == 5


def test_aggregation_dedupes_identical_local_and_vector_result() -> None:
    service = _build_service()
    match = {
        **_vector_match("S2", 0, "meme passage", 0.9),
        "source_chunk_id": "S2_0",
        "current_chunk_id": "S1_0",
    }

    merged = service._merge_plagiarism_results(
        scenario_id="S1",
        local_result={"score": 0.9, "matches": [dict(match)]},
        vector_result={"global_similarity_score": 0.9, "matches": [dict(match)]},
    )

    assert merged["total_matches"] == 1
    assert merged["matches"][0]["grouped_copies"] == 2


def test_aggregation_truncates_long_result_set() -> None:
    service = _build_service()
    raw = [
        {
            **_vector_match(f"S{index}", 0, f"long passage {index}", 0.99 - index * 0.001),
            "current_page_number": index + 1,
            "source_page_number": index + 1,
            "source_chunk_id": f"S{index}_0",
        }
        for index in range(40)
    ]

    merged = service._merge_plagiarism_results(
        scenario_id="S1",
        local_result={"score": 0.0, "matches": []},
        vector_result={"global_similarity_score": 0.95, "matches": raw},
    )

    assert merged["total_matches"] == 40
    assert merged["displayed_matches"] <= PlagiarismPipeline.MAX_TOTAL_MATCHES_DISPLAYED
    assert merged["is_truncated"] is True


def test_aggregation_keeps_boilerplate_matches_but_ranks_equal_scores_by_quality() -> None:
    service = _build_service()
    filler = {
        **_vector_match(
            "S2",
            0,
            "Texte de remplissage non commun: cette ligne existe seulement pour occuper la page.",
            0.94,
        ),
        "boilerplate_ratio": 0.9,
        "informative_word_count": 2,
        "match_quality_score": 0.01,
    }
    informative_1 = {
        **_vector_match(
            "S2",
            1,
            "Passage identique 1 - Le systeme de verification compare des scenes detaillees.",
            0.94,
        ),
        "boilerplate_ratio": 0.0,
        "informative_word_count": 10,
        "match_quality_score": 0.5,
    }
    informative_2 = {
        **_vector_match(
            "S2",
            2,
            "Passage identique 2 - La qualite d'un test depend des exemples couverts.",
            0.93,
        ),
        "boilerplate_ratio": 0.0,
        "informative_word_count": 10,
        "match_quality_score": 0.49,
    }

    merged = service._merge_plagiarism_results(
        scenario_id="S1",
        local_result={"score": 0.0, "matches": []},
        vector_result={
            "global_similarity_score": 0.94,
            "matches": [filler, informative_1, informative_2],
        },
    )

    snippets = " ".join(match["matched_chunk_text"] for match in merged["matches"])
    assert merged["total_matches"] == 3
    assert merged["matches"][0]["matched_chunk_text"] == informative_1["matched_chunk_text"]
    assert "Passage identique 1" in snippets
    assert "Passage identique 2" in snippets
    assert "remplissage" in snippets


def test_aggregation_prefers_real_score_before_quality() -> None:
    service = _build_service()
    filler = {
        **_vector_match("S2", 0, "texte remplissage page test ligne", 0.98),
        "match_quality_score": 0.02,
        "informative_word_count": 2,
        "boilerplate_ratio": 0.2,
    }
    informative = {
        **_vector_match("S2", 0, "passage informatif avec decision narrative precise", 0.94),
        "match_quality_score": 0.6,
        "informative_word_count": 8,
        "boilerplate_ratio": 0.0,
    }

    merged = service._merge_plagiarism_results(
        scenario_id="S1",
        local_result={"score": 0.0, "matches": []},
        vector_result={"global_similarity_score": 0.98, "matches": [filler, informative]},
    )

    assert merged["matches"][0]["matched_chunk_text"] == filler["matched_chunk_text"]


def test_aggregation_prefers_quality_when_scores_are_close() -> None:
    service = _build_service()
    repetitive = {
        **_vector_match("S2", 0, "page 1 texte remplissage page test ligne", 0.95),
        "boilerplate_ratio": 0.8,
    }
    informative = {
        **_vector_match(
            "S2",
            1,
            "La scene du marche nocturne revele le conflit entre la cheffe et son frere absent.",
            0.93,
        ),
        "boilerplate_ratio": 0.0,
    }

    merged = service._merge_plagiarism_results(
        scenario_id="S1",
        local_result={"score": 0.0, "matches": []},
        vector_result={
            "global_similarity_score": 0.95,
            "matches": [repetitive, informative],
        },
    )

    assert merged["total_matches"] == 2
    assert merged["matches"][0]["matched_chunk_text"] == informative["matched_chunk_text"]


def test_aggregation_prioritizes_informative_passages_without_dropping_repetitive_matches() -> None:
    service = _build_service()
    repeated = "CONFIDENTIEL PROJET SCENARIO PAGE TEST"
    repetitive_match = {
        **_vector_match(
            "DOC-B",
            0,
            f"{repeated}. {repeated}. {repeated}.",
            0.94,
        ),
        "current_page_number": 1,
        "source_page_number": 1,
        "boilerplate_ratio": 0.85,
    }
    first_passage = {
        **_vector_match(
            "DOC-B",
            1,
            (
                "Avant ce paragraphe le contenu diverge. "
                "La conservatrice cache la cle numerique dans le vieux projecteur "
                "pendant que le temoin decrit precisement le plan de sortie. "
                "Apres ce paragraphe le contenu diverge encore."
            ),
            0.93,
        ),
        "current_page_number": 2,
        "source_page_number": 4,
        "best_overlap": (
            "La conservatrice cache la cle numerique dans le vieux projecteur "
            "pendant que le temoin decrit precisement le plan de sortie."
        ),
        "boilerplate_ratio": 0.0,
    }
    second_passage = {
        **_vector_match(
            "DOC-B",
            2,
            (
                "Texte different autour. "
                "Le pilote refuse le protocole automatique car la balise secondaire "
                "signale une panne thermique au-dessus du port. "
                "Autre contenu different autour."
            ),
            0.92,
        ),
        "current_page_number": 5,
        "source_page_number": 7,
        "common_text": (
            "Le pilote refuse le protocole automatique car la balise secondaire "
            "signale une panne thermique au-dessus du port."
        ),
        "boilerplate_ratio": 0.0,
    }

    merged = service._merge_plagiarism_results(
        scenario_id="DOC-A",
        local_result={"score": 0.0, "matches": []},
        vector_result={
            "global_similarity_score": 0.94,
            "matches": [repetitive_match, first_passage, second_passage],
        },
    )

    assert merged["total_matches"] == 3
    assert merged["displayed_matches"] == 3
    top_snippets = " ".join(match["snippet"] for match in merged["matches"][:2])
    assert "conservatrice cache la cle numerique" in top_snippets
    assert "pilote refuse le protocole automatique" in top_snippets
    assert "CONFIDENTIEL" not in merged["matches"][0]["snippet"]
    assert "CONFIDENTIEL" in " ".join(match["snippet"] for match in merged["matches"])
    for match in merged["matches"]:
        assert match["similarity_score"] in {0.94, 0.93, 0.92}
        assert "match_quality_score" in match
        assert "boilerplate_ratio" in match


def test_diagnostics_are_opt_in_and_report_non_destructive_counts() -> None:
    service = _build_service()
    old_value = settings.PLAGIARISM_DIAGNOSTICS_ENABLED
    settings.PLAGIARISM_DIAGNOSTICS_ENABLED = True
    try:
        merged = service._merge_plagiarism_results(
            scenario_id="S1",
            local_result={"score": 0.0, "matches": [_vector_match("S2", 0, "a", 0.8)]},
            vector_result={
                "global_similarity_score": 0.8,
                "matches": [_vector_match("S2", 0, "a", 0.8)],
                "diagnostics": {
                    "chunks_analyzed": 2,
                    "raw_qdrant_results_count": 4,
                },
            },
        )
    finally:
        settings.PLAGIARISM_DIAGNOSTICS_ENABLED = old_value

    assert merged["diagnostics"]["chunks_generated"] == 2
    assert merged["diagnostics"]["raw_qdrant_matches"] == 4
    assert merged["diagnostics"]["raw_local_matches"] == 1
    assert merged["diagnostics"]["raw_matches_before_deduplication"] == 2
    assert merged["diagnostics"]["matches_after_deduplication"] == 1
    assert merged["diagnostics"]["filtered_matches"] == 0
    assert merged["diagnostics"]["filter_reasons"] == []


def test_page_records_keep_repeated_boilerplate_text_for_chunking() -> None:
    pdf_service = Mock()
    repeated = "ENTETE CONFIDENTIELLE PROJET SCENARIO UNIQUE"
    pdf_service.extract_pages.return_value = [
        {
            "page_number": 1,
            "text": (
                f"{repeated}\n"
                "Un passage narratif specifique doit rester indexe pour comparaison.\n"
                f"{repeated}"
            ),
        }
    ]
    from backend.pipelines.document_pipeline import DocumentPipeline

    pipeline = DocumentPipeline(
        pdf_service=pdf_service,
        text_cleaning_service=TextCleaningService(),
        chunking_service=Mock(),
        local_similarity_service=Mock(),
    )

    records = pipeline._build_page_records(
        file_path="fake.pdf",
        fallback_raw_text="",
        repeated_lines={repeated.lower()},
    )

    assert len(records) == 1
    assert repeated in records[0]["text_normalized"]
    assert repeated in records[0]["text_display"]
    assert records[0]["boilerplate_ratio"] > 0
