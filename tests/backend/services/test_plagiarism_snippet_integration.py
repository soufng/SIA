"""Integration test: snippet centring inside PlagiarismService matches."""

from unittest.mock import Mock

from backend.services.plagiarism_service import PlagiarismService


CURRENT_CHUNK_BASE = (
    "Page 1. Préambule du chunk avec un en-tête répété et du texte de remplissage. "
    "{passage}"
    " Suite du chunk avec d'autres choses non importantes pour l'analyse."
)

SOURCE_CHUNK_BASE = (
    "Page 7. Sommaire du document source rempli de boilerplate. "
    "{passage}"
    " Fin de chunk avec encore du remplissage."
)


def _build_service_with_payloads(payloads: list[dict]) -> PlagiarismService:
    embedding_service = Mock()
    embedding_service.generate_embeddings.return_value = [[0.1] * 3]
    vector_service = Mock()
    vector_service.search_similar_chunks.return_value = payloads
    return PlagiarismService(
        embedding_service=embedding_service,
        vector_service=vector_service,
    )


def test_snippet_is_centred_on_overlap_inside_chunk() -> None:
    passage = (
        "Le détective remarque un détail crucial : la fenêtre était fermée "
        "de l'intérieur."
    )
    current_chunk = CURRENT_CHUNK_BASE.format(passage=passage)
    source_chunk = SOURCE_CHUNK_BASE.format(passage=passage)

    service = _build_service_with_payloads(
        [
            {
                "id": "p1",
                "score": 0.92,
                "payload": {
                    "scenario_id": "OTHER",
                    "chunk_id": "OTHER_3",
                    "chunk_text": source_chunk,
                    "chunk_text_display": source_chunk,
                },
            }
        ]
    )

    result = service.analyze_chunks(
        scenario_id="current",
        chunks=[current_chunk],
        similarity_threshold=0.5,
        top_k=1,
    )

    assert len(result["matches"]) == 1
    match = result["matches"][0]
    snippet = match["snippet"]
    assert match["snippet_source"] == "overlap"
    assert "détective remarque un détail crucial" in snippet
    assert "fenêtre était fermée" in snippet
    # Snippet must not start with the chunk header.
    assert not snippet.lower().lstrip("… ").startswith("page 7")
    assert not snippet.lower().lstrip("… ").startswith("sommaire du document")
    # Detection itself must remain unchanged: similarity score is preserved.
    assert match["similarity_score"] == 0.92
    # Original full text remains exposed for downstream consumers.
    assert match["matched_chunk_text"] == source_chunk


def test_multiple_passages_get_distinct_snippets() -> None:
    passages = [
        "Le chevalier traverse les marais brumeux et combat un dragon ancien.",
        "Une princesse oubliée révèle le secret de la prophétie millénaire.",
        "Le royaume se prépare à un siège imminent contre les forces obscures.",
    ]
    payloads = []
    for i, passage in enumerate(passages):
        source_chunk = SOURCE_CHUNK_BASE.format(passage=passage)
        payloads.append(
            {
                "id": f"p{i}",
                "score": 0.88 - i * 0.01,
                "payload": {
                    "scenario_id": "OTHER",
                    "chunk_id": f"OTHER_{i}",
                    "chunk_text": source_chunk,
                    "chunk_text_display": source_chunk,
                },
            }
        )

    # Single Qdrant call returns all three payloads (top_k=3).
    service = _build_service_with_payloads(payloads)
    # Mimic three different current chunks each containing one passage.
    current_chunks = [
        CURRENT_CHUNK_BASE.format(passage=passage) for passage in passages
    ]

    # Stub the embedding service to return one vector per chunk.
    service.embedding_service.generate_embeddings.return_value = [
        [0.1] * 3 for _ in current_chunks
    ]
    # Each Qdrant call returns ALL payloads — the highest-score one will win
    # for each chunk because token-level overlap is what selects the snippet.
    service.vector_service.search_similar_chunks.return_value = payloads

    result = service.analyze_chunks(
        scenario_id="current",
        chunks=current_chunks,
        similarity_threshold=0.5,
        top_k=3,
    )

    matches = result["matches"]
    # Three current chunks × three payloads each (above threshold) → 9 matches,
    # but each unique payload must yield a snippet centred on its own passage.
    snippets = {m["snippet"] for m in matches}
    assert len(snippets) >= 3, "expected distinct snippets per passage"

    found_passages = 0
    for passage in passages:
        marker = passage.split(" ", 4)[3]  # a mid-passage word
        if any(marker in s for s in snippets):
            found_passages += 1
    assert found_passages == 3, "each passage must appear in at least one snippet"

    # At least one match per passage should be tagged as "overlap" (those
    # where the current chunk's passage matches the source payload's passage).
    overlap_matches = [m for m in matches if m["snippet_source"] == "overlap"]
    assert len(overlap_matches) >= 3


def test_snippet_falls_back_when_no_meaningful_overlap() -> None:
    current_chunk = "Une histoire totalement différente sur la cuisine."
    source_chunk = (
        "Page 1. Header. Aventure dans un univers de science-fiction "
        "complètement éloignée du sujet d'origine."
    )

    service = _build_service_with_payloads(
        [
            {
                "id": "p1",
                "score": 0.78,
                "payload": {
                    "scenario_id": "OTHER",
                    "chunk_id": "OTHER_0",
                    "chunk_text": source_chunk,
                    "chunk_text_display": source_chunk,
                },
            }
        ]
    )

    result = service.analyze_chunks(
        scenario_id="current",
        chunks=[current_chunk],
        similarity_threshold=0.5,
        top_k=1,
    )

    match = result["matches"][0]
    assert match["snippet_source"] == "fallback"
    assert match["overlap_text"] is None
    # Detection-level score is unchanged.
    assert match["similarity_score"] == 0.78


def test_snippet_centring_does_not_affect_similarity_score() -> None:
    """Sanity: same payload + same threshold → same score with snippet helper."""
    passage = "Un passage unique au milieu du chunk pour test de stabilité."
    current = CURRENT_CHUNK_BASE.format(passage=passage)
    source = SOURCE_CHUNK_BASE.format(passage=passage)

    service = _build_service_with_payloads(
        [
            {
                "id": "p1",
                "score": 0.84,
                "payload": {
                    "scenario_id": "OTHER",
                    "chunk_id": "OTHER_0",
                    "chunk_text": source,
                    "chunk_text_display": source,
                },
            }
        ]
    )

    result = service.analyze_chunks(
        scenario_id="current",
        chunks=[current],
        similarity_threshold=0.5,
        top_k=1,
    )
    assert result["matches"][0]["similarity_score"] == 0.84
    assert result["global_similarity_score"] == 0.84
