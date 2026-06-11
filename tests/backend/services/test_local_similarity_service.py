from pathlib import Path
from unittest.mock import Mock

from backend.services.local_similarity_service import LocalSimilarityService


def test_local_similarity_excludes_current_file_by_path(tmp_path: Path) -> None:
    """The exact file being analysed (same path) must not be reported."""
    current = tmp_path / "current.pdf"
    current.write_bytes(b"%PDF-1.4 lonely file")

    service = LocalSimilarityService(
        raw_dir=tmp_path,
        pdf_service=Mock(),
        text_cleaning_service=Mock(),
        chunking_service=Mock(),
        analysis_repository=Mock(),
    )
    service.analysis_repository.find_by_file_hash.return_value = []

    result = service.analyze(
        scenario_id="scenario-1",
        current_file_path=str(current),
        current_text="cleaned text",
        current_chunks=["cleaned text"],
        file_hash=service.compute_file_hash(current),
        text_hash=service.compute_text_hash("cleaned text"),
        original_filename="current.pdf",
    )

    assert result["matches"] == []
    assert result["score"] == 0.0
    assert result["exact_duplicate"] is False
    assert result["duplicate_count"] == 0


def test_local_similarity_reports_exact_duplicate_uploaded_earlier(
    tmp_path: Path,
) -> None:
    """A previous upload with the same file_hash must be flagged as duplicate."""
    current = tmp_path / "current.pdf"
    previous = tmp_path / "test_scenario.pdf"
    content = b"%PDF-1.4 same bytes"
    current.write_bytes(content)
    previous.write_bytes(content)

    pdf_mock = Mock()
    pdf_mock.extract_text.return_value = "Texte du précédent fichier"
    cleaning_mock = Mock()
    cleaning_mock.clean_text.return_value = "texte du precedent fichier"
    chunking_mock = Mock()
    chunking_mock.chunk_text.return_value = ["texte du precedent fichier"]

    service = LocalSimilarityService(
        raw_dir=tmp_path,
        pdf_service=pdf_mock,
        text_cleaning_service=cleaning_mock,
        chunking_service=chunking_mock,
        analysis_repository=Mock(),
    )
    service.analysis_repository.find_by_file_hash.return_value = []

    result = service.analyze(
        scenario_id="scenario-1",
        current_file_path=str(current),
        current_text="same cleaned text",
        current_chunks=["same cleaned text"],
        file_hash=service.compute_file_hash(current),
        text_hash=service.compute_text_hash("same cleaned text"),
        original_filename="current.pdf",
    )

    assert result["score"] == 1.0
    assert result["risk"] == "high"
    assert result["duplicate"] is True
    assert result["exact_duplicate"] is True
    assert result["duplicate_count"] == 1
    assert result["matches"] == []
    first = result["duplicate_analyses"][0]
    assert first["stored_filename"] == "test_scenario.pdf"
    assert first["source"] == "raw"


def test_local_similarity_reports_near_duplicate_by_text_hash(
    tmp_path: Path,
) -> None:
    """Same cleaned content under different binary content is still a duplicate."""
    current = tmp_path / "current.pdf"
    other = tmp_path / "rewritten.pdf"
    current.write_bytes(b"%PDF-1.4 original bytes")
    other.write_bytes(b"%PDF-1.4 different bytes but same text")

    cleaning_mock = Mock()
    cleaning_mock.clean_text.return_value = "same cleaned text"
    pdf_mock = Mock()
    pdf_mock.extract_text.return_value = "same cleaned text"
    chunking_mock = Mock()
    chunking_mock.chunk_text.return_value = ["same cleaned text"]

    service = LocalSimilarityService(
        raw_dir=tmp_path,
        pdf_service=pdf_mock,
        text_cleaning_service=cleaning_mock,
        chunking_service=chunking_mock,
        analysis_repository=Mock(),
    )
    service.analysis_repository.find_by_file_hash.return_value = []

    text_hash = service.compute_text_hash("same cleaned text")
    result = service.analyze(
        scenario_id="scenario-1",
        current_file_path=str(current),
        current_text="same cleaned text",
        current_chunks=["same cleaned text"],
        file_hash=service.compute_file_hash(current),
        text_hash=text_hash,
        original_filename="current.pdf",
    )

    assert result["matches"] == []
    assert result["exact_duplicate"] is True
    assert result["score"] == 1.0
    assert result["risk"] == "high"
    assert result["duplicate_count"] == 1
    first = result["duplicate_analyses"][0]
    assert first["stored_filename"] == "rewritten.pdf"
    assert first["text_hash"] == text_hash


def test_local_similarity_reports_partial_match_for_distinct_content(
    tmp_path: Path,
) -> None:
    """Genuine partial similarity (distinct hashes) must still be reported."""
    current = tmp_path / "current.pdf"
    other = tmp_path / "neighbour.pdf"
    current.write_bytes(b"%PDF-1.4 current")
    other.write_bytes(b"%PDF-1.4 other")

    pdf_mock = Mock()
    pdf_mock.extract_text.return_value = "raw text of other file"
    cleaning_mock = Mock()
    cleaning_mock.clean_text.return_value = "alpha beta gamma delta epsilon zeta"
    chunking_mock = Mock()
    chunking_mock.chunk_text.return_value = [
        "alpha beta gamma delta epsilon zeta"
    ]

    service = LocalSimilarityService(
        raw_dir=tmp_path,
        pdf_service=pdf_mock,
        text_cleaning_service=cleaning_mock,
        chunking_service=chunking_mock,
        analysis_repository=Mock(),
    )
    service.analysis_repository.find_by_file_hash.return_value = []

    current_text = "alpha beta gamma delta epsilon eta"
    result = service.analyze(
        scenario_id="scenario-1",
        current_file_path=str(current),
        current_text=current_text,
        current_chunks=[current_text],
        file_hash=service.compute_file_hash(current),
        text_hash=service.compute_text_hash(current_text),
        original_filename="current.pdf",
    )

    assert result["matches"], "expected at least one partial-similarity match"
    assert result["matches"][0]["filename"] == "neighbour.pdf"
    assert result["matches"][0]["duplicate"] is False
    assert 0.0 < result["score"] < 1.0


def test_local_similarity_reports_mongodb_duplicate_with_different_scenario(
    tmp_path: Path,
) -> None:
    """A MongoDB analysis with same file_hash and different scenario_id is reported."""
    current = tmp_path / "current.pdf"
    current.write_bytes(b"%PDF-1.4 unique bytes")

    service = LocalSimilarityService(
        raw_dir=tmp_path,
        pdf_service=Mock(),
        text_cleaning_service=Mock(),
        chunking_service=Mock(),
        analysis_repository=Mock(),
    )
    service.analysis_repository.find_by_file_hash.return_value = [
        {
            "scenario_id": "scenario-previous",
            "filename": "scenario_X.pdf",
            "stored_filename": "abc123.pdf",
            "chunk_count": 3,
        }
    ]

    result = service.analyze(
        scenario_id="scenario-current",
        current_file_path=str(current),
        current_text="content",
        current_chunks=["content"],
        file_hash=service.compute_file_hash(current),
        text_hash=service.compute_text_hash("content"),
        original_filename="current.pdf",
    )

    assert result["matches"] == []
    assert result["exact_duplicate"] is True
    assert result["score"] == 1.0
    assert result["risk"] == "high"
    assert result["duplicate_count"] == 1
    mongo_duplicate = next(
        (m for m in result["duplicate_analyses"] if m["source"] == "mongodb"),
        None,
    )
    assert mongo_duplicate is not None
    assert mongo_duplicate["scenario_id"] == "scenario-previous"
