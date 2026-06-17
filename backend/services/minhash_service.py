"""MinHash / LSH plagiarism detection.

Lexical fingerprinting layer that complements the semantic (e5 + Qdrant)
search. Where embeddings rank by *style and topic*, MinHash on token
shingles ranks by *actual textual reuse* — this is the signal Turnitin
and similar tools rely on.

How it works
------------
* Each chunk is reduced to a set of overlapping ``SHINGLE_SIZE``-token
  windows ("shingles").
* A MinHash signature (``NUM_PERM`` hashes) approximates the set's
  Jaccard similarity with any other set.
* An ``MinHashLSH`` index lets us retrieve, in sub-linear time, the
  signatures that have an estimated Jaccard >= ``THRESHOLD`` with a
  query signature.
* For each candidate we recompute the exact MinHash Jaccard so the
  score we report is sharp.

Indexing strategy
-----------------
The LSH index is held in memory as a process-wide singleton. It is
populated lazily from Qdrant the first time a search happens (one full
scroll). After that, the indexing service stays in sync by calling
``add_chunk(...)`` whenever a new chunk is upserted to Qdrant.

This keeps the design isolated from the existing pipeline:
* No new persistent store — Qdrant remains the source of truth.
* Wiping Qdrant (``reset_all.ps1``) automatically wipes the MinHash
  index next time the backend starts.
* If the process restarts, the index is rebuilt from Qdrant on demand.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from backend.utils.composite_scoring import normalize_tokens


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MinHash parameters
# ---------------------------------------------------------------------------

# Length of the token window used as a shingle. 5 is a good balance: short
# enough to detect partial paraphrasing, long enough that random co-occurrence
# of common words doesn't trigger a match.
SHINGLE_SIZE = 5

# Number of hash permutations in the MinHash signature. 128 keeps memory
# usage low (~1 KB / chunk) while keeping the Jaccard approximation error
# under 10%.
NUM_PERM = 128

# Estimated Jaccard threshold used by LSH to decide whether two
# signatures are "close enough" to be returned as candidates. The exact
# Jaccard is recomputed after retrieval, so this only filters how many
# candidates we look at — keeping it loose (0.05) trades a bit of speed
# for much higher recall on partial copies.
LSH_THRESHOLD = 0.05

# Below this exact Jaccard score the match is dropped. Anything under
# ~5% shared shingles is noise (a couple of common bigrams).
MIN_REPORT_JACCARD = 0.05


def make_shingles(text: str, size: int = SHINGLE_SIZE) -> set[bytes]:
    """Return the set of token-shingles for ``text``.

    Tokens are reused from ``normalize_tokens`` so stopwords (screenplay
    boilerplate, darija interjections, slug lines) are stripped before
    shingling — the MinHash signature is computed on *informative*
    content only.
    """
    tokens = normalize_tokens(text)
    if len(tokens) < size:
        return set()
    shingles: set[bytes] = set()
    for i in range(len(tokens) - size + 1):
        shingles.add(" ".join(tokens[i : i + size]).encode("utf-8"))
    return shingles


def build_signature(text: str) -> Any | None:
    """Return a MinHash signature for ``text``, or ``None`` if too short."""
    try:
        from datasketch import MinHash
    except ImportError as exc:
        raise RuntimeError(
            "datasketch is not installed. Run `pip install datasketch`."
        ) from exc
    shingles = make_shingles(text)
    if not shingles:
        return None
    m = MinHash(num_perm=NUM_PERM)
    for shingle in shingles:
        m.update(shingle)
    return m


# ---------------------------------------------------------------------------
# Process-wide singleton index
# ---------------------------------------------------------------------------


class MinHashIndex:
    """In-memory LSH index over all known chunks.

    Thread-safe. Lazily populated from Qdrant on first use, then kept in
    sync by the indexing pipeline.
    """

    _instance: "MinHashIndex | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        from datasketch import MinHashLSH

        self._lsh = MinHashLSH(threshold=LSH_THRESHOLD, num_perm=NUM_PERM)
        # Keep the signatures around so we can recompute the exact
        # Jaccard for each candidate (LSH only returns the keys).
        self._signatures: dict[str, Any] = {}
        # Mirror of the chunk metadata (scenario_id, page, filename, etc.)
        # so the search layer can build a plagiarism match directly.
        self._payloads: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._bootstrapped = False

    @classmethod
    def get(cls) -> "MinHashIndex":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def is_bootstrapped(self) -> bool:
        return self._bootstrapped

    def mark_bootstrapped(self) -> None:
        self._bootstrapped = True

    def add_chunk(
        self,
        key: str,
        text: str,
        payload: dict[str, Any],
    ) -> bool:
        """Index a single chunk. Returns True if the chunk was added."""
        signature = build_signature(text)
        if signature is None:
            return False
        with self._lock:
            if key in self._signatures:
                # Already indexed — skip the duplicate insertion to avoid
                # the "key already exists" exception from MinHashLSH.
                return False
            try:
                self._lsh.insert(key, signature)
            except ValueError:
                # Race with another worker — safe to ignore.
                return False
            self._signatures[key] = signature
            self._payloads[key] = payload
        return True

    def search(
        self,
        text: str,
        exclude_scenario_id: str | None = None,
        excluded_scenario_ids: set[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return the top matches for ``text`` ranked by exact Jaccard.

        The signature is built once, queried against the LSH, then we
        recompute the exact Jaccard for each candidate so the score is
        as accurate as possible (LSH only filters).
        """
        signature = build_signature(text)
        if signature is None:
            return []
        with self._lock:
            try:
                candidate_keys = list(self._lsh.query(signature))
            except Exception:
                logger.exception("MinHash LSH query failed.")
                return []
            results: list[dict[str, Any]] = []
            excluded = {str(sid) for sid in (excluded_scenario_ids or set()) if sid}
            for key in candidate_keys:
                payload = self._payloads.get(key, {})
                matched_scenario = str(payload.get("scenario_id") or "")
                if exclude_scenario_id and matched_scenario == str(exclude_scenario_id):
                    continue
                if matched_scenario and matched_scenario in excluded:
                    continue
                other = self._signatures.get(key)
                if other is None:
                    continue
                jaccard = float(signature.jaccard(other))
                if jaccard < MIN_REPORT_JACCARD:
                    continue
                results.append({"key": key, "score": jaccard, "payload": dict(payload)})
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

    def size(self) -> int:
        with self._lock:
            return len(self._signatures)

    def clear(self) -> None:
        from datasketch import MinHashLSH

        with self._lock:
            self._lsh = MinHashLSH(threshold=LSH_THRESHOLD, num_perm=NUM_PERM)
            self._signatures.clear()
            self._payloads.clear()
            self._bootstrapped = False


# ---------------------------------------------------------------------------
# Bootstrap from Qdrant
# ---------------------------------------------------------------------------


def bootstrap_from_qdrant(vector_service: Any, batch_size: int = 256) -> int:
    """Populate the singleton index by scrolling all Qdrant points.

    Returns the number of chunks indexed.
    """
    index = MinHashIndex.get()
    if index.is_bootstrapped():
        return index.size()

    client = getattr(vector_service, "client", None)
    collection = getattr(vector_service, "collection_name", None)
    if client is None or not collection:
        logger.warning("Cannot bootstrap MinHash: vector_service has no client.")
        index.mark_bootstrapped()
        return 0

    if not client.collection_exists(collection_name=collection):
        logger.info(
            "Qdrant collection %s does not exist yet — MinHash index left empty.",
            collection,
        )
        index.mark_bootstrapped()
        return 0

    offset = None
    indexed = 0
    while True:
        try:
            points, offset = client.scroll(
                collection_name=collection,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
        except Exception:
            logger.exception("Qdrant scroll failed during MinHash bootstrap.")
            break

        for point in points or []:
            payload = dict(getattr(point, "payload", None) or {})
            text = (
                payload.get("chunk_text_display")
                or payload.get("chunk_text")
                or ""
            )
            if not text:
                continue
            key = str(getattr(point, "id", ""))
            if not key:
                continue
            if index.add_chunk(key=key, text=text, payload=payload):
                indexed += 1

        if not offset:
            break

    index.mark_bootstrapped()
    total = index.size()
    logger.info(
        "MinHash index bootstrapped: %s new chunk(s), %s total.",
        indexed,
        total,
    )
    return total
