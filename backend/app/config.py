from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "backend" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    gemini_api_key: str = ""

    # Model routing: a cheap model for bulk extraction, a stronger one for reasoning.
    # Comma-separated fallbacks: the first one that works is used.
    model_cheap: str = "gemini-3.5-flash-lite,gemini-flash-lite-latest"
    model_strong: str = "gemini-3.5-flash,gemini-flash-latest"

    frontend_origin: str = "http://localhost:3000"

    # Logging: INFO shows every step with timings; DEBUG adds cache hits.
    log_level: str = "INFO"
    slow_seconds: float = 2.0


settings = Settings()
