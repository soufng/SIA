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
        risk_counts = {"low": 0, "medium": 0, "high": 0}
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
            ).lower()
            date_key = self._date_key(document.get("analysis_timestamp"))

            similarity_values.append(similarity)
            profanity_values.append(profanity)
            adult_values.append(adult)

            if risk in risk_counts:
                risk_counts[risk] += 1

            if date_key:
                analyses_by_date[date_key] = analyses_by_date.get(date_key, 0) + 1

            if risk in {"medium", "high"}:
                risky_rows.append(
                    {
                        "scenario_id": document.get("scenario_id", ""),
                        "analysis_timestamp": document.get("analysis_timestamp"),
                        "risk_level": risk,
                        "similarity_score": similarity,
                        "profanity_score": profanity,
                        "adult_content_score": adult,
                        "summary": self._get_nested(document, "rag_report", "summary")
                        or "",
                    }
                )

        top_similar = sorted(
            (
                {
                    "scenario_id": document.get("scenario_id", ""),
                    "analysis_timestamp": document.get("analysis_timestamp"),
                    "similarity_score": self._to_float(
                        self._get_nested(
                            document,
                            "plagiarism",
                            "global_similarity_score",
                        )
                    ),
                    "risk_level": self._get_nested(
                        document,
                        "rag_report",
                        "risk_level",
                    )
                    or "unknown",
                }
                for document in documents
            ),
            key=lambda item: item["similarity_score"],
            reverse=True,
        )[:10]

        risky_rows.sort(
            key=lambda item: (
                item["risk_level"] == "high",
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
