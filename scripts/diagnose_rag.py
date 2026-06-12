"""Diagnostic script for the Advanced RAG layer.

Run with:
    python scripts/diagnose_rag.py

Tells you, in order:
  1. Whether Ollama (or the configured LLM provider) is reachable.
  2. Which model is actually loaded.
  3. How long the configured model takes for a *real* RAG-sized prompt.
  4. If the call fails, prints the exact exception so you know whether
     to bump the timeout, switch model, or restart Ollama.

This is the first thing to run when the UI shows
``Modèle : fallback déterministe`` unexpectedly.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Force UTF-8 stdout on Windows consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

# Allow running directly from the repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.config import settings  # noqa: E402
from backend.services.advanced_rag_service import AdvancedRAGService  # noqa: E402
from backend.services.llm_provider import (  # noqa: E402
    MockLLMProvider,
    OllamaProvider,
    get_llm_provider,
)


def main() -> int:
    print("=" * 60)
    print("DIAGNOSTIC — Advanced RAG layer")
    print("=" * 60)

    print("\n[1/4] Settings chargées depuis .env")
    print(f"  ADVANCED_RAG_ENABLED      = {settings.ADVANCED_RAG_ENABLED}")
    print(f"  ADVANCED_RAG_PROVIDER     = {settings.ADVANCED_RAG_PROVIDER}")
    print(f"  ADVANCED_RAG_MODEL        = {settings.ADVANCED_RAG_MODEL}")
    print(f"  ADVANCED_RAG_BASE_URL     = {settings.ADVANCED_RAG_BASE_URL}")
    print(f"  ADVANCED_RAG_TIMEOUT      = {settings.ADVANCED_RAG_TIMEOUT_SECONDS}s")
    print(f"  ADVANCED_RAG_MAX_PASSAGES = {settings.ADVANCED_RAG_MAX_PASSAGES}")

    provider = get_llm_provider()
    print(f"\n[2/4] Provider sélectionné : {type(provider).__name__}")
    if isinstance(provider, MockLLMProvider):
        print(
            "  ⚠ MockLLMProvider est actif — aucun vrai LLM ne sera contacté. "
            "Vérifie ton .env (SIA_RAG_LLM_PROVIDER) ou que l'API key / le "
            "service Ollama est bien accessible."
        )
        return 1

    if isinstance(provider, OllamaProvider):
        base = provider.base_url
        model = provider.model
        print(f"  base_url = {base}")
        print(f"  model    = {model}")
        print(f"  timeout  = {provider.timeout}s")

        print("\n[3/4] Test de connectivité Ollama")
        try:
            with urllib.request.urlopen(f"{base}/api/tags", timeout=5) as r:
                models = [m.get("name") for m in json.loads(r.read()).get("models", [])]
            print(f"  ✓ Ollama répond — {len(models)} modèle(s) disponibles")
            if model not in models and not any(m.startswith(model) for m in models):
                print(
                    f"  ✗ Modèle '{model}' INTROUVABLE dans Ollama. Liste : {models}"
                )
                print(f"     → lance : ollama pull {model}")
                return 2
            print(f"  ✓ Modèle '{model}' présent")
        except urllib.error.URLError as exc:
            print(f"  ✗ Ollama injoignable sur {base} : {exc}")
            print("     → démarre Ollama (commande `ollama serve` ou service Windows)")
            return 3

    print("\n[4/4] Génération RAG réelle (timing complet, prompt RAG-sized)")
    analysis = _make_analysis_fixture(num_passages=settings.ADVANCED_RAG_MAX_PASSAGES)
    svc = AdvancedRAGService(llm_provider=provider)
    t0 = time.time()
    try:
        report = svc.generate(analysis=analysis, scenario_id="diag-scenario")
    except Exception as exc:
        elapsed = time.time() - t0
        print(f"  ✗ ÉCHEC après {elapsed:.1f}s : {type(exc).__name__}: {exc}")
        return 4
    elapsed = time.time() - t0

    print(f"  ✓ Temps total : {elapsed:.1f}s")
    print(f"  provider     = {report['llm']['provider']}")
    print(f"  model        = {report['llm']['model']}")
    print(f"  used_fallback= {report['llm']['used_fallback']}")
    print(f"  error        = {report['llm']['error']}")
    print(f"  narrative len= {len(report['narrative'])} chars")
    print(f"  prompt len   = {len(report['prompt'])} chars")

    if report["llm"]["used_fallback"]:
        print()
        print("⚠ Le LLM a échoué → fallback déterministe activé.")
        print(f"  Erreur exacte : {report['llm']['error']}")
        print("  Pistes :")
        print(f"    - Bumper SIA_RAG_LLM_TIMEOUT_SECONDS (actuel "
              f"{settings.ADVANCED_RAG_TIMEOUT_SECONDS}s) à ≥ {int(elapsed * 1.5)}s")
        print("    - Vérifier la RAM disponible (le modèle peut être déchargé)")
        print("    - Réduire SIA_RAG_MAX_PASSAGES pour un prompt plus court")
        print("    - Essayer un modèle plus petit (llama3.2:1b ou qwen2.5:1.5b)")
        return 5

    print("\n✓ Tout est OK — le RAG fonctionne en mode LLM réel.")
    return 0


def _make_analysis_fixture(num_passages: int = 4) -> dict:
    """Build an analysis dict that approximates the prompt size of a real
    100-page scenario with several plagiarism matches."""
    matches = []
    for i in range(num_passages):
        matches.append({
            "similarity_score": 0.55 + i * 0.02,
            "matched_chunk_text_display": (
                f"Passage extrait {i+1} avec assez de contexte pour ressembler "
                "à un vrai chunk : il décrit un passage du document source qui "
                "contient à la fois du texte de remplissage et le passage "
                "réellement copié, sur plusieurs phrases avec des détails "
                "stylistiques variés."
            ),
            "chunk_text": (
                f"Page {2*i+1}/100 — chunk courant du scénario analysé, avec "
                "exactement la même longueur typique d'un chunk PyMuPDF de "
                "400 mots pour simuler le coût réel du prompt en tokens."
            ),
            "overlap_text": f"passage commun numéro {i+1}",
            "matched_scenario_id": f"S-source-{i}",
            "stored_filename": f"source_{i}.pdf",
            "filename": f"source_{i}.pdf",
            "original_filename": f"document_source_{i}.pdf",
            "current_chunk_index": 3 * i + 1,
            "source_chunk_index": i,
        })
    return {
        "scenario_id": "diag-scenario",
        "document_stats": {
            "original_filename": "scenario_100_pages.pdf",
            "file_name": "abc.pdf",
            "words_count": 30000,
            "chunks_count": 200,
        },
        "rag_report": {"risk_level": "medium"},
        "plagiarism": {
            "global_similarity_score": 0.5925,
            "total_matches": 28,
            "total_sources": 3,
            "plagiarism_sources": [{
                "source_scenario_id": "S-source-0",
                "original_filename": "document_source_0.pdf",
                "stored_filename": "source_0.pdf",
                "best_score": 0.65,
                "matches_count": num_passages,
                "matches": matches,
            }],
            "matches": [],
        },
        "profanity": {"profanity_score": 0.0, "detected_words": []},
        "adult_content": {"adult_content_score": 0.0, "risk_level": "low"},
    }


if __name__ == "__main__":
    sys.exit(main())
