from pydantic_settings import BaseSettings
import os


class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv("DATABASE_URL")
    ASYNC_DATABASE_URL: str = os.getenv("ASYNC_DATABASE_URL")
    REDIS_URL: str = os.getenv("REDIS_URL")
    MASTER_USER_ID: int = 1

    # Frontend
    FRONTEND_URL: str = "http://localhost:3000"

    # Discord
    DISCORD_BOT_TOKEN: str = ""
    DISCORD_APPLICATION_ID: str = ""
    DISCORD_CLIENT_ID: str = ""
    DISCORD_CLIENT_SECRET: str = ""
    DISCORD_REDIRECT_URI: str = "https://cochat-for-buisness-backend.onrender.com/api/v1/integrations/discord/callback"

    # Google
    GOOGLE_API_KEY: str = ""
    GEMINI_MODEL_NAME: str = "gemini-3.6-flash"
    GROQ_API_KEY: str = ""
    # Groq가 2026-08-16자로 llama-3.3-70b-versatile를 폐기(#54) → 공식 권장 대체 모델로 교체.
    # Groq도 모델을 예고 후 종료하므로 env var로 즉시 교체 가능하게 관리.
    GROQ_MODEL_NAME: str = "openai/gpt-oss-120b"

    # Slack
    SLACK_BOT_TOKEN: str = ""
    SLACK_SIGNING_SECRET: str = ""
    SLACK_CLIENT_ID: str = ""
    SLACK_CLIENT_SECRET: str = ""
    SLACK_REDIRECT_URI: str = "https://cochat-for-buisness-backend.onrender.com/api/v1/integrations/slack/callback"

    # Google Calendar
    GOOGLE_CALENDAR_CLIENT_ID: str = ""
    GOOGLE_CALENDAR_CLIENT_SECRET: str = ""
    GOOGLE_CALENDAR_REDIRECT_URI: str = "https://cochat-for-buisness-backend.onrender.com/api/v1/integrations/google-calendar/callback"

    class Config:
        env_file = ".env"


settings = Settings()
