from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    """Runtime settings read from environment variables."""

    app_name: str = os.getenv("APP_NAME", "Technical Analyst API")
    app_version: str = os.getenv("APP_VERSION", "0.1.0")
    api_v1_prefix: str = os.getenv("API_V1_PREFIX", "/api/v1")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./technical_analyst.db")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
