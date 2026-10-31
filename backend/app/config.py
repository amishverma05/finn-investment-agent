from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "backend" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    # One key, or several separated by commas (GEMINI_API_KEY=key1,key2,...). GEMINI_API_KEYS works too.
    # With several, each call uses a random one, and a rate-limited key is swapped for another.
    gemini_api_key: str = ""
    gemini_api_keys: str = ""

    # Model routing: a cheap model for bulk extraction, a stronger one for reasoning.
    # Comma-separated fallbacks: the first one that works is used.
    model_cheap: str = "gemini-3.5-flash-lite,gemini-flash-lite-latest"
    model_strong: str = "gemini-3.5-flash,gemini-flash-latest"

    # Groq: a different provider, used only when both Gemini models of a tier are busy or silent.
    # Optional: without a key the app simply doesn't use it.
    groq_api_key: str = ""
    groq_model_cheap: str = "openai/gpt-oss-20b"
    groq_model_strong: str = "openai/gpt-oss-120b"

    frontend_origin: str = "http://localhost:3000"

    # Background jobs (filings, prices, alerts, push notifications). Turn off with SCHEDULER_ENABLED=false.
    scheduler_enabled: bool = True
    # Contact sent with push notifications (push services require one): a mailto: address
    # or an https:// site. Empty = the frontend's https address, or a placeholder locally.
    push_contact: str = ""

    # Logging: INFO shows every step with timings; DEBUG adds cache hits.
    log_level: str = "INFO"
    slow_seconds: float = 2.0


settings = Settings()
