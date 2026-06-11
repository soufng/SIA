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

    APP_NAME: str = "SPM API"
    APP_VERSION: str = "1.0.0"
    # "development" : tous les défauts permis.
    # "production"  : le démarrage échoue si un secret est resté à sa
    #                 valeur par défaut publique.
    ENV: str = Field(default="development", validation_alias="SPM_ENV")
    DEBUG: bool = Field(default=True, validation_alias="SPM_DEBUG")
    MONGODB_URL: str = "mongodb://127.0.0.1:27017/spm"
    MONGO_DB_NAME: str = "spm"
    MONGODB_DATABASE: str = "spm"
    QDRANT_URL: str = "http://localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION_NAME: str = "scenario_chunks"
    EMBEDDING_MODEL_NAME: str = Field(
        default="intfloat/multilingual-e5-base",
        validation_alias="SPM_EMBEDDING_MODEL",
    )
    # Default 768 = multilingual-e5-base. MiniLM L12 = 384, bge-m3 = 1024.
    # Must match the Qdrant collection size — switching the model without
    # running ``scripts/reindex_qdrant.py`` will break vector search.
    EMBEDDING_VECTOR_SIZE: int = Field(
        default=768,
        validation_alias="SPM_EMBEDDING_VECTOR_SIZE",
    )
    PLAGIARISM_CHUNK_SIZE: int = Field(default=220, validation_alias="SPM_PLAGIARISM_CHUNK_SIZE")
    PLAGIARISM_CHUNK_OVERLAP: int = Field(default=40, validation_alias="SPM_PLAGIARISM_CHUNK_OVERLAP")
    PLAGIARISM_MIN_CHUNK_SIZE: int = Field(default=50, validation_alias="SPM_PLAGIARISM_MIN_CHUNK_SIZE")
    PLAGIARISM_MAX_SOURCES_DISPLAYED: int = Field(default=5, validation_alias="SPM_PLAGIARISM_MAX_SOURCES_DISPLAYED")
    PLAGIARISM_MAX_MATCHES_PER_SOURCE: int = Field(default=5, validation_alias="SPM_PLAGIARISM_MAX_MATCHES_PER_SOURCE")
    PLAGIARISM_MAX_TOTAL_MATCHES_DISPLAYED: int = Field(default=25, validation_alias="SPM_PLAGIARISM_MAX_TOTAL_MATCHES_DISPLAYED")
    PLAGIARISM_SIMILARITY_THRESHOLD: float = Field(default=0.60, validation_alias="SPM_PLAGIARISM_SIMILARITY_THRESHOLD")
    PLAGIARISM_MIN_MATCH_SCORE: float = Field(default=0.3, validation_alias="SPM_PLAGIARISM_MIN_MATCH_SCORE")
    PLAGIARISM_TOP_K: int = Field(default=15, validation_alias="SPM_PLAGIARISM_TOP_K")
    PLAGIARISM_DIAGNOSTICS_ENABLED: bool = Field(default=False, validation_alias="SPM_PLAGIARISM_DIAGNOSTICS_ENABLED")
    BOILERPLATE_REPEATED_LINE_MIN_COUNT: int = Field(default=3, validation_alias="SPM_BOILERPLATE_REPEATED_LINE_MIN_COUNT")
    BOILERPLATE_REPEATED_LINE_MIN_LENGTH: int = Field(default=20, validation_alias="SPM_BOILERPLATE_REPEATED_LINE_MIN_LENGTH")
    BOILERPLATE_REPEATED_LINE_MAX_LENGTH: int = Field(default=250, validation_alias="SPM_BOILERPLATE_REPEATED_LINE_MAX_LENGTH")

    # ---- Authentication (single-admin JWT, optional) ----
    AUTH_ENABLED: bool = Field(default=True, validation_alias="SPM_AUTH_ENABLED")
    AUTH_ADMIN_USERNAME: str = Field(default="admin", validation_alias="SPM_ADMIN_USERNAME")
    # Default password = "admin" (PBKDF2-SHA256, 200k iterations).
    # Generate your own with:
    #     python -c "from backend.core.auth import hash_password; print(hash_password('MON_MOT_DE_PASSE'))"
    # Default = password "admin" (PBKDF2-SHA256, 200k iters). Override in
    # production by generating your own with:
    #   python -m backend.core.auth "VotreMotDePasse"
    AUTH_ADMIN_PASSWORD_HASH: str = Field(
        default=_DEFAULT_ADMIN_PASSWORD_HASH,
        validation_alias="SPM_ADMIN_PASSWORD_HASH",
    )
    AUTH_JWT_SECRET: str = Field(
        default=_DEFAULT_JWT_SECRET,
        validation_alias="SPM_JWT_SECRET",
    )
    AUTH_JWT_EXPIRY_MINUTES: int = Field(
        default=480,  # 8h workday
        validation_alias="SPM_JWT_EXPIRY_MINUTES",
    )
    # ---- Two-factor authentication (TOTP, RFC 6238) ----
    AUTH_OTP_ENABLED: bool = Field(default=False, validation_alias="SPM_OTP_ENABLED")
    # Base32-encoded secret. Generate one with:
    #   python -m backend.core.totp generate
    AUTH_OTP_SECRET: str = Field(default="", validation_alias="SPM_OTP_SECRET")
    AUTH_OTP_ISSUER: str = Field(default="SPM-CCM", validation_alias="SPM_OTP_ISSUER")

    # ---- Advanced RAG layer (additive, optional) ----
    # provider = auto | openai | anthropic | ollama | none
    # When provider is "auto" we pick the first one whose API key/URL is set,
    # else we fall back to a deterministic enriched template renderer.
    ADVANCED_RAG_ENABLED: bool = Field(default=True, validation_alias="SPM_ADVANCED_RAG_ENABLED")
    ADVANCED_RAG_PROVIDER: str = Field(default="auto", validation_alias="SPM_RAG_LLM_PROVIDER")
    ADVANCED_RAG_MODEL: str = Field(default="", validation_alias="SPM_RAG_LLM_MODEL")
    ADVANCED_RAG_API_KEY: str = Field(default="", validation_alias="SPM_RAG_LLM_API_KEY")
    ADVANCED_RAG_BASE_URL: str = Field(default="", validation_alias="SPM_RAG_LLM_BASE_URL")
    ADVANCED_RAG_MAX_TOKENS: int = Field(default=1200, validation_alias="SPM_RAG_LLM_MAX_TOKENS")
    ADVANCED_RAG_MAX_PASSAGES: int = Field(default=6, validation_alias="SPM_RAG_MAX_PASSAGES")
    ADVANCED_RAG_TEMPERATURE: float = Field(default=0.2, validation_alias="SPM_RAG_LLM_TEMPERATURE")
    ADVANCED_RAG_TIMEOUT_SECONDS: int = Field(default=45, validation_alias="SPM_RAG_LLM_TIMEOUT_SECONDS")

    # ---- Uploads ----
    # Taille maximale d'un PDF accepté par /uploads/analyze (Mo). 0 désactive.
    UPLOAD_MAX_MB: int = Field(default=20, validation_alias="SPM_UPLOAD_MAX_MB")

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
            offenders.append("SPM_JWT_SECRET")
        if self.AUTH_ADMIN_PASSWORD_HASH == _DEFAULT_ADMIN_PASSWORD_HASH:
            offenders.append("SPM_ADMIN_PASSWORD_HASH")
        if self.AUTH_OTP_ENABLED and not self.AUTH_OTP_SECRET.strip():
            offenders.append("SPM_OTP_SECRET")

        if offenders:
            raise RuntimeError(
                "SPM_ENV=production mais des secrets utilisent toujours leur "
                "valeur par défaut publique : "
                + ", ".join(offenders)
                + ". Générez-en des frais avec `python scripts/rotate_secrets.py` "
                "et exportez-les via votre gestionnaire de secrets avant de "
                "démarrer l'API."
            )
        return self


settings = Settings()
