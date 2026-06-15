import logging
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import DESCENDING
from pymongo.database import Database
from pymongo.errors import PyMongoError

from backend.core.config import settings
from backend.db.mongodb import get_database


logger = logging.getLogger(__name__)

RESULTS_COLLECTION_NAME = "analyses"


class AnalysisRepository:
    """Read saved full-pipeline analysis results from MongoDB."""

    def __init__(
        self,
        mongodb_url: str | None = None,
        database_name: str | None = None,
        collection_name: str = RESULTS_COLLECTION_NAME,
        database: Database | None = None,
    ) -> None:
        self.mongodb_url = mongodb_url or settings.MONGODB_URL
        self.database_name = database_name or settings.MONGO_DB_NAME
        self.collection_name = collection_name
        self.database = database

    def list_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent analysis results, newest first when timestamps exist."""
        try:
            database = self._get_database()
            database.client.admin.command("ping")
            collection = database[self.collection_name]
            self._ensure_missing_timestamps(collection)
            cursor = (
                collection.find({})
                .sort(
                    [
                        ("created_at", DESCENDING),
                        ("analysis_timestamp", DESCENDING),
                        ("_id", DESCENDING),
                    ]
                )
                .limit(limit)
            )

            items = [self._normalize_document(document) for document in cursor]
            logger.info(
                "Loaded %s analysis history item(s) from %s.%s.",
                len(items),
                self.database_name,
                self.collection_name,
            )
            return items
        except PyMongoError:
            logger.exception(
                "Failed to read analysis history from %s.%s.",
                self.database_name,
                self.collection_name,
            )
            raise

    def save_result(self, result: dict[str, Any]) -> str:
        """Save an analysis result and ensure it has an analysis timestamp."""
        document = dict(result)
        if not document.get("analysis_timestamp"):
            document["analysis_timestamp"] = datetime.now(UTC).isoformat()
        if not document.get("created_at"):
            document["created_at"] = document["analysis_timestamp"]
        if not document.get("status"):
            document["status"] = "completed"

        try:
            database = self._get_database()
            database.client.admin.command("ping")
            collection = database[self.collection_name]
            inserted = collection.insert_one(document)
            logger.info(
                "Saved analysis result in %s.%s with _id=%s.",
                self.database_name,
                self.collection_name,
                inserted.inserted_id,
            )
            return str(inserted.inserted_id)
        except PyMongoError:
            logger.exception(
                "Failed to save analysis result in %s.%s.",
                self.database_name,
                self.collection_name,
            )
            raise

    def get_statistics(self) -> dict[str, Any]:
        """Compute global analytics from saved analysis results."""
        try:
            database = self._get_database()
            database.client.admin.command("ping")
            collection = database[self.collection_name]
            self._ensure_missing_timestamps(collection)
            documents = [self._normalize_document(document) for document in collection.find({})]
            statistics = self._build_statistics(documents)
            logger.info(
                "Computed analysis statistics from %s document(s) in %s.%s.",
                len(documents),
                self.database_name,
                self.collection_name,
            )
            return statistics
        except PyMongoError:
            logger.exception(
                "Failed to compute analysis statistics from %s.%s.",
                self.database_name,
                self.collection_name,
            )
            raise

    def find_by_scenario_id(self, scenario_id: str) -> dict[str, Any] | None:
        """Return the saved analysis document for a given ``scenario_id``.

        The returned shape matches what ``list_history`` produces (normalised
        public API shape) so the frontend can consume it identically.
        """
        if not scenario_id or not scenario_id.strip():
            return None
        try:
            database = self._get_database()
            database.client.admin.command("ping")
            document = database[self.collection_name].find_one(
                {"scenario_id": scenario_id}
            )
            return self._normalize_document(document) if document else None
        except PyMongoError:
            logger.exception(
                "Failed to fetch analysis by scenario_id=%s.", scenario_id
            )
            raise

    def find_by_file_hash(
        self,
        file_hash: str,
        exclude_scenario_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Return analyses already saved for the same file hash."""
        if not file_hash:
            return []

        query: dict[str, Any] = {"file_hash": file_hash}
        if exclude_scenario_id:
            query["scenario_id"] = {"$ne": exclude_scenario_id}

        try:
            database = self._get_database()
            database.client.admin.command("ping")
            cursor = (
                database[self.collection_name]
                .find(query)
                .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
                .limit(limit)
            )
            documents = [self._serialize(document) for document in cursor]
            logger.info(
                "Found %s MongoDB duplicate(s) for file_hash=%s.",
                len(documents),
                file_hash,
            )
            return documents
        except PyMongoError:
            logger.exception("Failed to search analyses by file_hash=%s.", file_hash)
            raise

    def find_exact_duplicates(
        self,
        file_hash: str | None = None,
        text_hash: str | None = None,
        exclude_scenario_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return saved analyses with the same file hash or cleaned text hash."""
        conditions: list[dict[str, Any]] = []
        if file_hash:
            conditions.append({"file_hash": file_hash})
        if text_hash:
            conditions.append({"text_hash": text_hash})
        if not conditions:
            return []

        query: dict[str, Any] = (
            conditions[0] if len(conditions) == 1 else {"$or": conditions}
        )
        if exclude_scenario_id:
            query["scenario_id"] = {"$ne": exclude_scenario_id}

        try:
            database = self._get_database()
            database.client.admin.command("ping")
            cursor = (
                database[self.collection_name]
                .find(query)
                .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
                .limit(limit)
            )
            documents = [self._serialize(document) for document in cursor]
            logger.info(
                "Found %s exact duplicate analysis/analyses for file_hash=%s text_hash=%s.",
                len(documents),
                file_hash,
                text_hash,
            )
            return documents
        except PyMongoError:
            logger.exception(
                "Failed to search exact duplicates by file_hash=%s text_hash=%s.",
                file_hash,
                text_hash,
            )
            raise

    def ping(self) -> None:
        """Raise PyMongoError if MongoDB is unavailable."""
        self._get_database().client.admin.command("ping")

    def _get_database(self) -> Database:
        """Return the injected database or the shared configured database."""
        if self.database is not None:
            return self.database
        return get_database()

    def _ensure_missing_timestamps(self, collection: Any) -> None:
        """Backfill missing timestamps so history rows can be dated and sorted."""
        fallback_timestamp = datetime.now(UTC).isoformat()
        result = collection.update_many(
            {
                "$or": [
                    {"analysis_timestamp": {"$exists": False}},
                    {"analysis_timestamp": None},
                    {"analysis_timestamp": ""},
                ]
            },
            {"$set": {"analysis_timestamp": fallback_timestamp}},
        )

        if result.modified_count:
            logger.info(
                "Backfilled analysis_timestamp on %s history document(s).",
                result.modified_count,
            )

    def _normalize_document(self, document: dict[str, Any]) -> dict[str, Any]:
        """Normalize MongoDB documents to the public API response shape."""
        # History docs store the full payload under ``result``. Older docs
        # used ``analysis``; some legacy docs used neither and stored the
        # fields at the top level. We fall back through all three shapes.
        analysis = (
            document.get("analysis")
            if isinstance(document.get("analysis"), dict)
            else None
        )
        if analysis is None and isinstance(document.get("result"), dict):
            analysis = document["result"]
        if analysis is None:
            analysis = document
        plagiarism = (
            analysis.get("plagiarism")
            or analysis.get("plagiarism_result")
            or document.get("plagiarism_result")
            or {}
        )
        profanity = (
            analysis.get("profanity")
            or analysis.get("profanity_result")
            or document.get("profanity_result")
            or {}
        )
        adult_content = (
            analysis.get("adult_content")
            or analysis.get("adult_content_result")
            or document.get("adult_content_result")
            or {}
        )
        rag_report = analysis.get("rag_report") or document.get("rag_report") or {}
        moroccan_constants = (
            analysis.get("moroccan_constants")
            or document.get("moroccan_constants")
            or {}
        )
        document_stats = (
            analysis.get("document_stats") or document.get("document_stats") or {}
        )
        created_at = (
            document.get("created_at")
            or analysis.get("analysis_timestamp")
            or document.get("analysis_timestamp")
            or self._get_object_id_timestamp(document.get("_id"))
        )
        filename = (
            document.get("filename")
            or document_stats.get("file_name")
            or document_stats.get("filename")
            or ""
        )
        score = document.get("score")
        if score is None and isinstance(plagiarism, dict):
            score = plagiarism.get("global_similarity_score", 0.0)

        return {
            "id": self._serialize(document.get("_id", "")),
            "filename": self._serialize(filename),
            "file_hash": self._serialize(document.get("file_hash", "")),
            "text_hash": self._serialize(document.get("text_hash", "")),
            "word_count": self._serialize(document.get("word_count") or document_stats.get("words_count", 0)),
            "chunk_count": self._serialize(document.get("chunk_count") or document_stats.get("chunks_count", 0)),
            "similarity_score": self._serialize(document.get("similarity_score", score)),
            "risk_level": self._serialize(document.get("risk_level") or rag_report.get("risk_level", "unknown")),
            "score": self._serialize(score),
            "status": self._serialize(document.get("status", "completed")),
            "created_at": self._serialize(created_at),
            "result": self._serialize(document.get("result") or analysis),
            "scenario_id": self._serialize(
                analysis.get("scenario_id") or document.get("scenario_id") or ""
            ),
            "analysis_timestamp": self._serialize(
                analysis.get("analysis_timestamp")
                or document.get("analysis_timestamp")
                or document.get("created_at")
                or self._get_object_id_timestamp(document.get("_id"))
            ),
            "document_stats": self._serialize(document_stats),
            "plagiarism": self._serialize(plagiarism),
            "profanity": self._serialize(profanity),
            "adult_content": self._serialize(adult_content),
            "rag_report": self._serialize(rag_report),
            "moroccan_constants": self._serialize(moroccan_constants),
        }

    def _build_statistics(self, documents: list[dict[str, Any]]) -> dict[str, Any]:
        """Build aggregate analytics from normalized analysis documents."""
        risk_counts = {"low": 0, "medium": 0, "high": 0, "very_high": 0}
        analyses_by_date: dict[str, int] = {}
        similarity_values: list[float] = []
        profanity_values: list[float] = []
        adult_values: list[float] = []
        risky_rows: list[dict[str, Any]] = []

        for document in documents:
            similarity = self._to_float(
                self._get_nested(document, "plagiarism", "global_similarity_score")
            )
            profanity = self._to_float(
                self._get_nested(document, "profanity", "profanity_score")
            )
            adult = self._to_float(
                self._get_nested(document, "adult_content", "adult_content_score")
            )
            risk = str(
                self._get_nested(document, "rag_report", "risk_level") or "unknown"
            ).lower().strip()
            # Normalize the French/English variants of the very-high tier
            # introduced by the composite-scoring pipeline so the histogram
            # below sees a single canonical token.
            if risk in {"tres_eleve", "tres eleve", "très élevé"}:
                risk = "very_high"
            date_key = self._date_key(document.get("analysis_timestamp"))

            similarity_values.append(similarity)
            profanity_values.append(profanity)
            adult_values.append(adult)

            if risk in risk_counts:
                risk_counts[risk] += 1

            if date_key:
                analyses_by_date[date_key] = analyses_by_date.get(date_key, 0) + 1

            # Per-dimension flags. A scenario must be surfaced if ANY of the
            # three priority axes (exact duplicate, moroccan constants,
            # plagiarism similarity) is flagged high or very high.
            plagiarism_risk = self._normalize_risk(
                self._get_nested(document, "plagiarism", "risk")
            )
            exact_duplicate = bool(
                self._get_nested(document, "plagiarism", "exact_duplicate")
                or self._get_nested(document, "plagiarism", "duplicate")
            )
            moroccan_risk = self._normalize_risk(
                self._get_nested(document, "moroccan_constants", "risk_level")
            )

            plagiarism_flagged = plagiarism_risk in {"high", "very_high"}
            moroccan_flagged = moroccan_risk in {"high", "very_high"}
            if exact_duplicate or plagiarism_flagged or moroccan_flagged:
                source = self._extract_matched_source(document)
                # Priority order for the headline badge: exact duplicate
                # trumps everything, then moroccan constants (compliance),
                # then plagiarism similarity.
                if exact_duplicate:
                    primary = "exact_duplicate"
                elif moroccan_flagged:
                    primary = "moroccan_constants"
                else:
                    primary = "plagiarism"
                risky_rows.append(
                    {
                        "scenario_id": document.get("scenario_id", ""),
                        "original_filename": self._extract_filename(document),
                        "matched_filename": source["filename"],
                        "matched_scenario_id": source["scenario_id"],
                        "analysis_timestamp": document.get("analysis_timestamp"),
                        "risk_level": risk,
                        "primary_signal": primary,
                        "plagiarism_risk": plagiarism_risk,
                        "moroccan_risk": moroccan_risk,
                        "exact_duplicate": exact_duplicate,
                        "similarity_score": similarity,
                        "profanity_score": profanity,
                        "adult_content_score": adult,
                        "summary": self._get_nested(document, "rag_report", "summary")
                        or "",
                    }
                )

        def _top_entry(doc: dict[str, Any]) -> dict[str, Any]:
            source = self._extract_matched_source(doc)
            return {
                "scenario_id": doc.get("scenario_id", ""),
                "original_filename": self._extract_filename(doc),
                "matched_filename": source["filename"],
                "matched_scenario_id": source["scenario_id"],
                "analysis_timestamp": doc.get("analysis_timestamp"),
                "similarity_score": self._to_float(
                    self._get_nested(
                        doc, "plagiarism", "global_similarity_score"
                    )
                ),
                "risk_level": self._get_nested(
                    doc, "rag_report", "risk_level"
                )
                or "unknown",
            }

        top_similar = sorted(
            (_top_entry(document) for document in documents),
            key=lambda item: item["similarity_score"],
            reverse=True,
        )[:10]

        _risk_order = {"very_high": 3, "high": 2, "medium": 1}
        # Exact duplicate is the most severe finding (literal copy), then
        # we rank by the highest of plagiarism/moroccan/global risks, and
        # finally by the raw similarity to break ties.
        risky_rows.sort(
            key=lambda item: (
                1 if item.get("exact_duplicate") else 0,
                max(
                    _risk_order.get(item.get("plagiarism_risk", "low"), 0),
                    _risk_order.get(item.get("moroccan_risk", "low"), 0),
                    _risk_order.get(item.get("risk_level", "low"), 0),
                ),
                item["similarity_score"],
                item["profanity_score"],
                item["adult_content_score"],
            ),
            reverse=True,
        )

        return {
            "total_analyses": len(documents),
            "average_similarity_score": self._average(similarity_values),
            "average_profanity_score": self._average(profanity_values),
            "average_adult_content_score": self._average(adult_values),
            "risk_counts": risk_counts,
            "top_similar_scenarios": top_similar,
            "analyses_by_date": [
                {"date": date, "count": analyses_by_date[date]}
                for date in sorted(analyses_by_date)
            ],
            "risky_analyses": risky_rows,
        }

    def _serialize(self, value: Any) -> Any:
        """Convert MongoDB-specific values into JSON-safe values."""
        if isinstance(value, ObjectId):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, list):
            return [self._serialize(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._serialize(item) for key, item in value.items()}
        return value

    def _get_object_id_timestamp(self, value: Any) -> str | None:
        """Use ObjectId generation time as a fallback timestamp."""
        if isinstance(value, ObjectId):
            return value.generation_time.isoformat()
        return None

    def _get_nested(self, data: dict[str, Any], *keys: str) -> Any:
        """Safely read a nested dictionary value."""
        current: Any = data
        for key in keys:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    def _normalize_risk(self, value: Any) -> str:
        """Map any risk-level string variant to the canonical English token.

        Accepts the French scale (faible/moyen/élevé/très élevé) and the
        English one (low/medium/high/very_high), returning a lowercase
        English token. Unknown values become ``"unknown"``.
        """
        if not value:
            return "unknown"
        key = str(value).lower().strip()
        if key in {"very_high", "veryhigh", "tres_eleve", "tres eleve", "très élevé"}:
            return "very_high"
        if key in {"high", "eleve", "élevé"}:
            return "high"
        if key in {"medium", "moyen"}:
            return "medium"
        if key in {"low", "faible"}:
            return "low"
        return "unknown"

    def _extract_filename(self, document: dict[str, Any]) -> str:
        """Return the original PDF filename stored alongside the analysis.

        Checks the same fallback chain the frontend uses so the displayed
        name stays consistent between History and Analytics pages.
        """
        for candidate in (
            self._get_nested(document, "document_stats", "original_filename"),
            document.get("original_filename"),
            document.get("filename"),
            self._get_nested(document, "document_stats", "file_name"),
        ):
            if candidate:
                return str(candidate)
        return ""

    def _extract_matched_source(self, document: dict[str, Any]) -> dict[str, str]:
        """Return ``(filename, scenario_id)`` of the top plagiarism source.

        Picks the highest-scoring source from the ``plagiarism.sources`` or
        ``plagiarism.matches`` arrays. Returns empty strings when no match
        is recorded (e.g. low-risk analyses without any flagged source).
        """
        plagiarism = document.get("plagiarism") or {}
        if not isinstance(plagiarism, dict):
            return {"filename": "", "scenario_id": ""}

        # 1. Prefer ``plagiarism_sources``: already grouped by source and
        # sorted by best_score descending in the pipeline.
        sources = plagiarism.get("plagiarism_sources") or plagiarism.get("sources")
        if isinstance(sources, list):
            for source in sources:
                if not isinstance(source, dict):
                    continue
                name = (
                    source.get("original_filename")
                    or source.get("stored_filename")
                    or ""
                )
                sid = source.get("source_scenario_id") or ""
                if name or sid:
                    return {"filename": str(name), "scenario_id": str(sid)}

        # 2. Fall back to the flat match list and pick the best score.
        matches = plagiarism.get("matches")
        if isinstance(matches, list):
            best: dict[str, Any] | None = None
            best_score = -1.0
            for match in matches:
                if not isinstance(match, dict):
                    continue
                score = self._to_float(
                    match.get("final_score")
                    or match.get("similarity_score")
                    or match.get("score")
                )
                if score > best_score:
                    best_score = score
                    best = match
            if best is not None:
                return {
                    "filename": str(
                        best.get("original_filename")
                        or best.get("stored_filename")
                        or ""
                    ),
                    "scenario_id": str(best.get("matched_scenario_id") or ""),
                }
        return {"filename": "", "scenario_id": ""}

    def _to_float(self, value: Any) -> float:
        """Convert numeric-like values to float."""
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _average(self, values: list[float]) -> float:
        """Return a rounded average for chart-friendly API output."""
        if not values:
            return 0.0
        return round(sum(values) / len(values), 4)

    def _date_key(self, value: Any) -> str | None:
        """Extract YYYY-MM-DD from an analysis timestamp."""
        if not value:
            return None

        text = str(value)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text[:10] if len(text) >= 10 else None

        return parsed.date().isoformat()
