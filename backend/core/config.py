import logging

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


logger = logging.getLogger(__name__)


# Default values that MUST be overridden in production. Their hashes are
# in the public repo so anyone who reaches a deployment using them owns
# the admin account.
_DEFAULT_JWT_SECRET = "change-me-in-production-please-this-is-not-a-secret"
_DEFAULT_ADMIN_PASSWORD_HASH = (
    "pbkdf2_sha256$200000$HlkXzApwLG35DLpjmR6eAw==$"
    "i2pAJeDJ8V9gaHCZsA1Ro5o6H0RvKEOKIGO7onIFM5o="
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""

    APP_NAME: str = "SIA API"
    APP_VERSION: str = "1.0.0"
    # "development" : tous les défauts permis.
    # "production"  : le démarrage échoue si un secret est resté à sa
    #                 valeur par défaut publique.
    ENV: str = Field(default="development", validation_alias="SIA_ENV")
    DEBUG: bool = Field(default=True, validation_alias="SIA_DEBUG")
    MONGODB_URL: str = "mongodb://127.0.0.1:27017/sia"
    MONGO_DB_NAME: str = "sia"
    MONGODB_DATABASE: str = "sia"
    QDRANT_URL: str = "http://localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION_NAME: str = "scenario_chunks"
    EMBEDDING_MODEL_NAME: str = Field(
        default="intfloat/multilingual-e5-base",
        validation_alias="SIA_EMBEDDING_MODEL",
    )
    # Default 768 = multilingual-e5-base. MiniLM L12 = 384, bge-m3 = 1024.
    # Must match the Qdrant collection size — switching the model without
    # running ``scripts/reindex_qdrant.py`` will break vector search.
    EMBEDDING_VECTOR_SIZE: int = Field(
        default=768,
        validation_alias="SIA_EMBEDDING_VECTOR_SIZE",
    )
    PLAGIARISM_CHUNK_SIZE: int = Field(default=220, validation_alias="SIA_PLAGIARISM_CHUNK_SIZE")
    PLAGIARISM_CHUNK_OVERLAP: int = Field(default=40, validation_alias="SIA_PLAGIARISM_CHUNK_OVERLAP")
    PLAGIARISM_MIN_CHUNK_SIZE: int = Field(default=50, validation_alias="SIA_PLAGIARISM_MIN_CHUNK_SIZE")
    PLAGIARISM_MAX_SOURCES_DISPLAYED: int = Field(default=5, validation_alias="SIA_PLAGIARISM_MAX_SOURCES_DISPLAYED")
    PLAGIARISM_MAX_MATCHES_PER_SOURCE: int = Field(default=5, validation_alias="SIA_PLAGIARISM_MAX_MATCHES_PER_SOURCE")
    PLAGIARISM_MAX_TOTAL_MATCHES_DISPLAYED: int = Field(default=25, validation_alias="SIA_PLAGIARISM_MAX_TOTAL_MATCHES_DISPLAYED")
    PLAGIARISM_SIMILARITY_THRESHOLD: float = Field(default=0.60, validation_alias="SIA_PLAGIARISM_SIMILARITY_THRESHOLD")
    PLAGIARISM_MIN_MATCH_SCORE: float = Field(default=0.3, validation_alias="SIA_PLAGIARISM_MIN_MATCH_SCORE")
    PLAGIARISM_TOP_K: int = Field(default=15, validation_alias="SIA_PLAGIARISM_TOP_K")
    PLAGIARISM_DIAGNOSTICS_ENABLED: bool = Field(default=False, validation_alias="SIA_PLAGIARISM_DIAGNOSTICS_ENABLED")
    BOILERPLATE_REPEATED_LINE_MIN_COUNT: int = Field(default=3, validation_alias="SIA_BOILERPLATE_REPEATED_LINE_MIN_COUNT")
    BOILERPLATE_REPEATED_LINE_MIN_LENGTH: int = Field(default=20, validation_alias="SIA_BOILERPLATE_REPEATED_LINE_MIN_LENGTH")
    BOILERPLATE_REPEATED_LINE_MAX_LENGTH: int = Field(default=250, validation_alias="SIA_BOILERPLATE_REPEATED_LINE_MAX_LENGTH")

    # ---- Authentication (single-admin JWT, optional) ----
    AUTH_ENABLED: bool = Field(default=True, validation_alias="SIA_AUTH_ENABLED")
    AUTH_ADMIN_USERNAME: str = Field(default="admin", validation_alias="SIA_ADMIN_USERNAME")
    # Default password = "admin" (PBKDF2-SHA256, 200k iterations).
    # Generate your own with:
    #     python -c "from backend.core.auth import hash_password; print(hash_password('MON_MOT_DE_PASSE'))"
    # Default = password "admin" (PBKDF2-SHA256, 200k iters). Override in
    # production by generating your own with:
    #   python -m backend.core.auth "VotreMotDePasse"
    AUTH_ADMIN_PASSWORD_HASH: str = Field(
        default=_DEFAULT_ADMIN_PASSWORD_HASH,
        validation_alias="SIA_ADMIN_PASSWORD_HASH",
    )
    AUTH_JWT_SECRET: str = Field(
        default=_DEFAULT_JWT_SECRET,
        validation_alias="SIA_JWT_SECRET",
    )
    AUTH_JWT_EXPIRY_MINUTES: int = Field(
        default=480,  # 8h workday
        validation_alias="SIA_JWT_EXPIRY_MINUTES",
    )
    # ---- Two-factor authentication (TOTP, RFC 6238) ----
    AUTH_OTP_ENABLED: bool = Field(default=False, validation_alias="SIA_OTP_ENABLED")
    # Base32-encoded secret. Generate one with:
    #   python -m backend.core.totp generate
    AUTH_OTP_SECRET: str = Field(default="", validation_alias="SIA_OTP_SECRET")
    AUTH_OTP_ISSUER: str = Field(default="SIA-CCM", validation_alias="SIA_OTP_ISSUER")

    # ---- Advanced RAG layer (additive, optional) ----
    # provider = auto | openai | anthropic | ollama | none
    # When provider is "auto" we pick the first one whose API key/URL is set,
    # else we fall back to a deterministic enriched template renderer.
    ADVANCED_RAG_ENABLED: bool = Field(default=True, validation_alias="SIA_ADVANCED_RAG_ENABLED")
    ADVANCED_RAG_PROVIDER: str = Field(default="auto", validation_alias="SIA_RAG_LLM_PROVIDER")
    ADVANCED_RAG_MODEL: str = Field(default="", validation_alias="SIA_RAG_LLM_MODEL")
    ADVANCED_RAG_API_KEY: str = Field(default="", validation_alias="SIA_RAG_LLM_API_KEY")
    ADVANCED_RAG_BASE_URL: str = Field(default="", validation_alias="SIA_RAG_LLM_BASE_URL")
    ADVANCED_RAG_MAX_TOKENS: int = Field(default=1200, validation_alias="SIA_RAG_LLM_MAX_TOKENS")
    ADVANCED_RAG_MAX_PASSAGES: int = Field(default=6, validation_alias="SIA_RAG_MAX_PASSAGES")
    ADVANCED_RAG_TEMPERATURE: float = Field(default=0.2, validation_alias="SIA_RAG_LLM_TEMPERATURE")
    ADVANCED_RAG_TIMEOUT_SECONDS: int = Field(default=45, validation_alias="SIA_RAG_LLM_TIMEOUT_SECONDS")
    # Multi-query retrieval: LLM rewrites the document into N semantic queries
    # and we search Qdrant once per query. Off by default so behaviour matches
    # the current pipeline until the new path is validated in shadow mode.
    ADVANCED_RAG_MULTI_QUERY_ENABLED: bool = Field(
        default=False, validation_alias="SIA_RAG_MULTI_QUERY_ENABLED"
    )
    ADVANCED_RAG_MULTI_QUERY_COUNT: int = Field(
        default=4, validation_alias="SIA_RAG_MULTI_QUERY_COUNT"
    )
    # LLM reranking: a wider candidate pool is collected (cosine top-N),
    # then an LLM scores each candidate for editorial relevance to the
    # uploaded document; the top ``ADVANCED_RAG_MAX_PASSAGES`` are kept.
    # Off by default — enable once you have a model that produces stable
    # JSON (Qwen 2.5 14B or larger, Mistral Small, Claude Haiku, etc.).
    ADVANCED_RAG_RERANK_ENABLED: bool = Field(
        default=False, validation_alias="SIA_RAG_RERANK_ENABLED"
    )
    ADVANCED_RAG_RERANK_POOL_SIZE: int = Field(
        default=20, validation_alias="SIA_RAG_RERANK_POOL_SIZE"
    )

    # ---- LLM contextual review (optional second-reader layer) ----
    # Additive layer over the deterministic pipelines. When enabled, a small
    # subset of chunks (royal mentions, sensitive lexicon, neighbours of
    # already-flagged passages, edges of the screenplay) is sent to the LLM
    # for a contextual second read. Validated alerts are merged into the
    # final result under ``llm_contextual_alerts`` — existing fields stay
    # unchanged. Off by default to avoid extra LLM cost.
    LLM_CONTEXTUAL_REVIEW_ENABLED: bool = Field(
        default=False,
        validation_alias="LLM_CONTEXTUAL_REVIEW_ENABLED",
    )
    LLM_CONTEXTUAL_MAX_CHUNKS: int = Field(
        default=10,
        validation_alias="MAX_LLM_CONTEXT_CHUNKS",
    )
    LLM_CONTEXTUAL_MAX_CHARS_PER_CHUNK: int = Field(
        default=2000,
        validation_alias="MAX_CHARS_PER_CHUNK",
    )
    LLM_CONTEXTUAL_MAX_TOTAL_CHARS: int = Field(
        default=12000,
        validation_alias="MAX_TOTAL_LLM_CONTEXT_CHARS",
    )
    LLM_ALERTS_MAX: int = Field(
        default=8,
        validation_alias="LLM_ALERTS_MAX",
    )

    # ---- Uploads ----
    # Taille maximale d'un PDF accepté par /uploads/analyze (Mo). 0 désactive.
    UPLOAD_MAX_MB: int = Field(default=20, validation_alias="SIA_UPLOAD_MAX_MB")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def _enforce_production_secrets(self) -> "Settings":
        """Refuse de démarrer en prod si un secret est resté par défaut.

        Un défaut public dans un binaire de prod est équivalent à une
        absence de secret : n'importe qui qui lit le code du repo peut
        forger un JWT ou se logger en admin. On préfère un crash dur au
        démarrage qu'une exposition silencieuse.
        """
        if self.ENV.strip().lower() != "production":
            return self

        offenders: list[str] = []
        if self.AUTH_JWT_SECRET == _DEFAULT_JWT_SECRET:
            offenders.append("SIA_JWT_SECRET")
        if self.AUTH_ADMIN_PASSWORD_HASH == _DEFAULT_ADMIN_PASSWORD_HASH:
            offenders.append("SIA_ADMIN_PASSWORD_HASH")
        if self.AUTH_OTP_ENABLED and not self.AUTH_OTP_SECRET.strip():
            offenders.append("SIA_OTP_SECRET")

        if offenders:
            raise RuntimeError(
                "SIA_ENV=production mais des secrets utilisent toujours leur "
                "valeur par défaut publique : "
                + ", ".join(offenders)
                + ". Générez-en des frais avec `python scripts/rotate_secrets.py` "
                "et exportez-les via votre gestionnaire de secrets avant de "
                "démarrer l'API."
            )
        return self


settings = Settings()
