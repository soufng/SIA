"""End-to-end smoke test of the analysis pipeline.

Runs ``test_scenario.pdf`` through ``AnalysisService.analyze_scenario``
twice under different scenario ids. The first pass indexes the document in
Qdrant; the second pass should retrieve the first one as a similar match
via the e5 semantic search.

This is a manual sanity check — not a pytest test — so a developer can
visually confirm the full chain (PDF → e5 embeddings → Qdrant → matches →
template report) without spinning up the API server.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import settings  # noqa: E402
from backend.services.analysis_service import AnalysisService  # noqa: E402


PDF_PATH = PROJECT_ROOT / "data" / "raw" / "test_scenario.pdf"


def _summarize(scenario_id: str, result: dict) -> None:
    plagiarism = result.get("plagiarism", {})
    sources = plagiarism.get("plagiarism_sources") or []
    matches = plagiarism.get("matches") or []
    diagnostics = plagiarism.get("diagnostics") or {}
    print(f"\n=== scenario_id={scenario_id} ===")
    print(f"  status                 : {result.get('status')}")
    print(f"  warnings               : {result.get('warnings')}")
    print(
        f"  chunks_count           : "
        f"{result.get('document_stats', {}).get('chunks_count')}"
    )
    print(
        f"  global_similarity_score: "
        f"{plagiarism.get('global_similarity_score')}"
    )
    print(f"  plagiarism_detected    : {plagiarism.get('plagiarism_detected')}")
    print(f"  total_matches          : {plagiarism.get('total_matches')}")
    print(f"  total_sources          : {plagiarism.get('total_sources')}")
    if diagnostics:
        print(f"  diagnostics            : {json.dumps(diagnostics)}")
    if matches:
        top = matches[0]
        print("  top match              :")
        print(f"    similarity   : {top.get('similarity_score')}")
        print(f"    matched_sid  : {top.get('matched_scenario_id')}")
        snippet = (top.get("snippet") or "")[:120]
        try:
            print(f"    snippet[:120]: {snippet!r}")
        except UnicodeEncodeError:
            # Windows console encoding can't render some glyphs (Arabic
            # script, etc.). Fall back to a safe ASCII form.
            print(f"    snippet[:120]: {snippet.encode('ascii', 'replace').decode()!r}")
    if sources:
        print("  sources :")
        for src in sources[:3]:
            print(
                f"    - scenario={src.get('source_scenario_id')} "
                f"matches={src.get('matches_count')} "
                f"best_score={src.get('best_score')}"
            )


def main() -> int:
    if not PDF_PATH.exists():
        print(f"PDF introuvable: {PDF_PATH}", file=sys.stderr)
        return 1

    print("=== Configuration ===")
    print(f"  embedding model: {settings.EMBEDDING_MODEL_NAME}")
    print(f"  vector size    : {settings.EMBEDDING_VECTOR_SIZE}")
    print(f"  threshold      : {settings.PLAGIARISM_SIMILARITY_THRESHOLD}")
    print(f"  top_k          : {settings.PLAGIARISM_TOP_K}")

    service = AnalysisService()

    # First pass: index the document.
    sid_1 = f"e2e-pass1-{uuid4().hex[:8]}"
    print(f"\n--- Pass 1 (indexing) : {sid_1} ---")
    result_1 = service.analyze_scenario(
        scenario_id=sid_1,
        file_path=str(PDF_PATH),
        original_filename="test_scenario.pdf",
    )
    _summarize(sid_1, result_1)

    # Second pass: should find pass 1 as a near-perfect match.
    sid_2 = f"e2e-pass2-{uuid4().hex[:8]}"
    print(f"\n--- Pass 2 (should retrieve pass 1) : {sid_2} ---")
    result_2 = service.analyze_scenario(
        scenario_id=sid_2,
        file_path=str(PDF_PATH),
        original_filename="test_scenario.pdf",
    )
    _summarize(sid_2, result_2)

    plagiarism_2 = result_2.get("plagiarism", {})
    matches_2 = plagiarism_2.get("matches") or []
    score_2 = plagiarism_2.get("global_similarity_score") or 0.0

    print("\n=== Verdict ===")
    if matches_2 and score_2 >= 0.6:
        # Strict-match path would actually mark this as exact_duplicate
        # because file_hash + text_hash are identical — that's also a pass.
        print(
            "OK: pass 2 retrieved pass 1 via the vector pipeline "
            f"(score={score_2}, matches={len(matches_2)})."
        )
        return 0
    if result_2.get("strict_match", {}).get("verdict") == "identical":
        print(
            "OK: pass 2 was flagged as an exact duplicate by the strict "
            "similarity verdict (file_hash match)."
        )
        return 0
    print("FAIL: pass 2 did not retrieve pass 1.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
