import logging
from pathlib import Path

import fitz


logger = logging.getLogger(__name__)


class PDFService:
    """Service responsible for extracting text content from PDF files."""

    def extract_pages(self, file_path: str) -> list[dict[str, object]]:
        """Extract PDF text as page-numbered records."""
        path = self._validate_pdf_path(file_path)

        try:
            with fitz.open(path) as document:
                if document.page_count == 0:
                    logger.error("PDF contains no pages: %s", path)
                    raise ValueError(f"PDF contains no pages: {file_path}")

                pages = [
                    {"page_number": page_number, "text": page.get_text()}
                    for page_number, page in enumerate(document, start=1)
                ]
        except fitz.FileDataError as exc:
            logger.exception("Corrupted or invalid PDF file: %s", path)
            raise ValueError(f"PDF file is corrupted or invalid: {file_path}") from exc
        except RuntimeError as exc:
            logger.exception("Unable to read PDF file: %s", path)
            raise ValueError(f"Unable to read PDF file: {file_path}") from exc

        if not any(str(page.get("text") or "").strip() for page in pages):
            logger.error("No text extracted from PDF: %s", path)
            raise ValueError(f"PDF contains no extractable text: {file_path}")
        return pages

    def extract_text(self, file_path: str) -> str:
        """Extract text from a PDF file using PyMuPDF.

        Args:
            file_path: Path to the PDF file.

        Returns:
            The extracted text as a single string.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the file is not a PDF, is empty, has no text, or is corrupted.
        """
        path = self._validate_pdf_path(file_path)
        logger.info("Starting PDF text extraction: %s", path)

        try:
            with fitz.open(path) as document:
                if document.page_count == 0:
                    logger.error("PDF contains no pages: %s", path)
                    raise ValueError(f"PDF contains no pages: {file_path}")

                text_parts = []
                for page_number, page in enumerate(document, start=1):
                    logger.debug("Extracting text from page %s of %s", page_number, path)
                    text_parts.append(page.get_text())

        except fitz.FileDataError as exc:
            logger.exception("Corrupted or invalid PDF file: %s", path)
            raise ValueError(f"PDF file is corrupted or invalid: {file_path}") from exc
        except RuntimeError as exc:
            logger.exception("Unable to read PDF file: %s", path)
            raise ValueError(f"Unable to read PDF file: {file_path}") from exc

        extracted_text = "\n".join(text_parts).strip()
        if not extracted_text:
            logger.error("No text extracted from PDF: %s", path)
            raise ValueError(f"PDF contains no extractable text: {file_path}")

        logger.info("PDF text extraction completed: %s", path)
        return extracted_text

    def _validate_pdf_path(self, file_path: str) -> Path:
        path = Path(file_path)

        if not path.exists():
            logger.error("PDF file not found: %s", path)
            raise FileNotFoundError(f"File not found: {file_path}")

        if not path.is_file():
            logger.error("Provided path is not a file: %s", path)
            raise ValueError(f"Path is not a file: {file_path}")

        if path.suffix.lower() != ".pdf":
            logger.error("Invalid file extension for PDF extraction: %s", path)
            raise ValueError(f"File is not a PDF: {file_path}")

        if path.stat().st_size == 0:
            logger.error("PDF file is empty: %s", path)
            raise ValueError(f"PDF file is empty: {file_path}")

        return path
