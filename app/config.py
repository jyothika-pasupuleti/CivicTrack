from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    database_url: str = "mysql+pymysql://civictrack:change-this-local-password@127.0.0.1:3306/civictrack"
    secret_key: str = ""
    upload_dir: str = "data/uploads"
    cookie_secure: bool = False
    max_upload_bytes: int = 5 * 1024 * 1024
    token_expiry_minutes: int = 60 * 12
    app_name: str = "CivicTrack"

    model_config = SettingsConfigDict(
        env_prefix="CIVICTRACK_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


def load_settings() -> Settings:
    return Settings(_env_file=ENV_FILE)
