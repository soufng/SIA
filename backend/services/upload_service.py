import logging
from pathlib import Path
from uuid import uuid4


logger = logging.getLogger(__name__)


class UploadService:
    """Service responsible for saving uploaded PDF files locally."""

    DEFAULT_UPLOAD_DIR = Path("data/raw")

    def __init__(self, upload_dir: str | Path | None = None) -> None:
        """Initialize the upload service.

        Args:
            upload_dir: Directory where uploaded PDF files should be stored.
        """
        self.upload_dir = Path(upload_dir or self.DEFAULT_UPLOAD_DIR)

    def save_uploaded_file(
        self,
        file_content: bytes,
        original_filename: str,
    ) -> dict[str, str | int]:
        """Validate and save an uploaded PDF file.

        Args:
            file_content: Raw file bytes, for example from FastAPI UploadFile.read().
            original_filename: Original filename provided by the user.

        Returns:
            Dictionary containing original filename, stored filename, file path,
            and file size in bytes.

        Raises:
            TypeError: If file_content is not bytes or original_filename is not a string.
            ValueError: If file content is empty, filename is empty, or extension is not .pdf.
            RuntimeError: If saving the file fails.
        """
        self._validate_inputs(
            file_content=file_content,
            original_filename=original_filename,
        )

        try:
            self.upload_dir.mkdir(parents=True, exist_ok=True)

            original_path = Path(original_filename)
            stored_filename = f"{uuid4().hex}.pdf"
            file_path = self.upload_dir / stored_filename

            logger.info(
                "Saving uploaded PDF file. original_filename=%s stored_filename=%s",
                original_path.name,
                stored_filename,
            )
            file_path.write_bytes(file_content)

            file_info = {
                "original_filename": original_path.name,
                "stored_filename": stored_filename,
                "file_path": str(file_path),
                "file_size": len(file_content),
            }

            logger.info("Uploaded PDF file saved: %s", file_path)
            return file_info
        except Exception as exc:
            logger.exception("Failed to save uploaded PDF file: %s", original_filename)
            raise RuntimeError("Failed to save uploaded file") from exc

    def _validate_inputs(self, file_content: bytes, original_filename: str) -> None:
        """Validate uploaded file content and filename.

        Args:
            file_content: Raw file bytes.
            original_filename: Original filename provided by the user.

        Raises:
            TypeError: If input types are invalid.
            ValueError: If content or filename values are invalid.
        """
        if not isinstance(file_content, bytes):
            logger.error("Invalid file_content type: %s", type(file_content).__name__)
            raise TypeError("file_content must be bytes")

        if not file_content:
            logger.error("Empty uploaded file content received.")
            raise ValueError("file_content must not be empty")

        if not isinstance(original_filename, str):
            logger.error(
                "Invalid original_filename type: %s",
                type(original_filename).__name__,
            )
            raise TypeError("original_filename must be a string")

        if not original_filename.strip():
            logger.error("Empty original filename received.")
            raise ValueError("original_filename must not be empty")

        filename = Path(original_filename).name
        if Path(filename).suffix.lower() != ".pdf":
            logger.error("Invalid uploaded file extension: %s", filename)
            raise ValueError("uploaded file must be a PDF")
