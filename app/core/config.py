from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_SECRET_KEYS = {
    "",
    "change-this-min-32-chars-in-production!!",
    "change-this-to-a-random-32-char-secret-key!!",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    APP_NAME: str = "OCR Platform"
    APP_ENV: str  = "development"
    DEBUG: bool   = False
    API_V1_PREFIX: str = "/api/v1"

    SECRET_KEY: str = "change-this-min-32-chars-in-production!!"
    ALGORITHM: str  = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    DATABASE_URL: str = "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb"
    DATABASE_POOL_SIZE: int   = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_ECHO: bool        = False

    UPLOAD_DIR: str      = "uploads"
    OUTPUT_DIR: str      = "output"
    TEMPLATES_DIR: str   = "templates_store"
    MAX_FILE_SIZE_MB: int = 50
    MAX_PDF_PAGES: int = 50
    OCR_TIMEOUT_SECONDS: int = 60

    LOG_LEVEL: str = "INFO"
    CORS_ALLOWED_ORIGINS: str = "*"
    ALLOW_PUBLIC_REGISTRATION: bool = False

    # ── AI Fallback (Gemini) — disabled by default, no key required ──────────
    GEMINI_API_KEY: str = ""
    AI_FALLBACK_ENABLED: bool = False
    AI_FALLBACK_MODEL: str = "gemini-1.5-flash"
    AI_FALLBACK_TIMEOUT_SECONDS: int = 10
    AI_FALLBACK_MIN_CONFIDENCE: float = 0.75
    AI_FALLBACK_REGEX_PROTECT_CONFIDENCE: float = 0.90

    BATCH_ZIP_RETENTION_DAYS: int = 7
    BATCH_ZIP_MAX_MB: int = 500

    @property
    def upload_path(self) -> Path:
        p = Path(self.UPLOAD_DIR); p.mkdir(parents=True, exist_ok=True); return p

    @property
    def output_path(self) -> Path:
        p = Path(self.OUTPUT_DIR); p.mkdir(parents=True, exist_ok=True); return p

    @property
    def templates_path(self) -> Path:
        p = Path(self.TEMPLATES_DIR); p.mkdir(parents=True, exist_ok=True); return p


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()
    if settings.APP_ENV == "production":
        secret_key = (settings.SECRET_KEY or "").strip()
        if secret_key in PLACEHOLDER_SECRET_KEYS or len(secret_key) < 32:
            raise ValueError("SECRET_KEY must be a non-placeholder value of at least 32 characters in production!")
        if settings.CORS_ALLOWED_ORIGINS.strip() in {"", "*"}:
            raise ValueError("CORS_ALLOWED_ORIGINS must list explicit origins in production!")
    return settings
