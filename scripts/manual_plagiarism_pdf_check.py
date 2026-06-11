"""Run a manual plagiarism check against arbitrary PDF files.

Usage:
    python scripts/manual_plagiarism_pdf_check.py file1.pdf file2.pdf

The script intentionally avoids hard-coded fixture names. It is meant for
manual verification with short PDFs, long scenarios, exact re-uploads, and
partially similar documents in a running local stack.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from backend.services.analysis_service import AnalysisService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdfs", nargs="+", help="PDF files to analyze")
    args = parser.parse_args()

    service = AnalysisService()
    summaries = []
    for pdf in args.pdfs:
        path = Path(pdf)
        result = service.analyze_scenario(
            scenario_id=f"manual-{uuid4()}",
            file_path=str(path),
            original_filename=path.name,
        )
        plagiarism = result.get("plagiarism") or {}
        summaries.append(
            {
                "file": path.name,
                "scenario_id": result.get("scenario_id"),
                "similarity_score": plagiarism.get("global_similarity_score"),
                "total_matches": plagiarism.get("total_matches"),
                "total_sources": plagiarism.get("total_sources"),
                "exact_duplicate": plagiarism.get("exact_duplicate"),
                "duplicate_count": plagiarism.get("duplicate_count"),
                "is_truncated": plagiarism.get("is_truncated"),
            }
        )

    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
