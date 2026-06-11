from pathlib import Path

import pytest

from backend.services.upload_service import UploadService


def test_save_uploaded_file_saves_pdf_with_unique_filename(tmp_path: Path) -> None:
    service = UploadService(upload_dir=tmp_path)
    file_content = b"%PDF-1.4 fake content"

    result = service.save_uploaded_file(file_content, "scenario.pdf")

    saved_path = Path(result["file_path"])
    assert result["original_filename"] == "scenario.pdf"
    assert result["stored_filename"].endswith(".pdf")
    assert result["stored_filename"] != "scenario.pdf"
    assert result["file_size"] == len(file_content)
    assert saved_path.exists()
    assert saved_path.read_bytes() == file_content


def test_save_uploaded_file_accepts_uppercase_pdf_extension(tmp_path: Path) -> None:
    service = UploadService(upload_dir=tmp_path)

    result = service.save_uploaded_file(b"content", "SCENARIO.PDF")

    assert result["original_filename"] == "SCENARIO.PDF"
    assert Path(result["file_path"]).exists()


def test_save_uploaded_file_keeps_only_filename_from_path_input(tmp_path: Path) -> None:
    service = UploadService(upload_dir=tmp_path)

    result = service.save_uploaded_file(b"content", "folder/scenario.pdf")

    assert result["original_filename"] == "scenario.pdf"


def test_save_uploaded_file_creates_upload_directory(tmp_path: Path) -> None:
    upload_dir = tmp_path / "nested" / "raw"
    service = UploadService(upload_dir=upload_dir)

    result = service.save_uploaded_file(b"content", "scenario.pdf")

    assert upload_dir.exists()
    assert Path(result["file_path"]).exists()


def test_save_uploaded_file_raises_type_error_for_non_bytes_content(tmp_path: Path) -> None:
    service = UploadService(upload_dir=tmp_path)

    with pytest.raises(TypeError, match="file_content must be bytes"):
        service.save_uploaded_file("content", "scenario.pdf")  # type: ignore[arg-type]


def test_save_uploaded_file_raises_value_error_for_empty_content(tmp_path: Path) -> None:
    service = UploadService(upload_dir=tmp_path)

    with pytest.raises(ValueError, match="file_content must not be empty"):
        service.save_uploaded_file(b"", "scenario.pdf")


def test_save_uploaded_file_raises_type_error_for_non_string_filename(
    tmp_path: Path,
) -> None:
    service = UploadService(upload_dir=tmp_path)

    with pytest.raises(TypeError, match="original_filename must be a string"):
        service.save_uploaded_file(b"content", None)  # type: ignore[arg-type]


def test_save_uploaded_file_raises_value_error_for_empty_filename(tmp_path: Path) -> None:
    service = UploadService(upload_dir=tmp_path)

    with pytest.raises(ValueError, match="original_filename must not be empty"):
        service.save_uploaded_file(b"content", "   ")


def test_save_uploaded_file_raises_value_error_for_non_pdf_extension(
    tmp_path: Path,
) -> None:
    service = UploadService(upload_dir=tmp_path)

    with pytest.raises(ValueError, match="uploaded file must be a PDF"):
        service.save_uploaded_file(b"content", "scenario.txt")
