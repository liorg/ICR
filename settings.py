"""
db/settings.py
הגדרות סביבה — נטענות מ-.env
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase / Postgres
    DATABASE_URL:    str = ""           # postgresql://user:pass@host/db
    DB_POOL_MIN:     int = 5
    DB_POOL_MAX:     int = 20

    # DOTNET Agent
    DOTNET_AGENT_URL: str = ""          # http://agent:5000
    WEBHOOK_SECRET:   str = "change-me"

    # FastAPI
    FASTAPI_HOST:     str = "0.0.0.0"
    FASTAPI_PORT:     int = 8000
    CALLBACK_BASE_URL: str = ""         # https://fastapi.myapp.com

    # Scheduler
    SCHEDULER_INTERVAL_SECONDS: int = 10
    SCHEDULER_BATCH_SIZE:       int = 10


settings = Settings()
