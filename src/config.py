import os
from dotenv import load_dotenv

load_dotenv()

VERSION = "1.0.0"


class Settings:
    # App version — read by /about command
    VERSION: str = VERSION

    # LLM — Claude Agents SDK
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    # LLM via OpenRouter (legacy prototype — kept for backward compat)
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")

    # Slack
    SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
    SLACK_APP_TOKEN: str = os.getenv("SLACK_APP_TOKEN", "")
    SLACK_PROSPECTING_CHANNEL: str = os.getenv("SLACK_PROSPECTING_CHANNEL", "")

    # HubSpot (Phase 7+)
    HUBSPOT_ACCESS_TOKEN: str = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
    HUBSPOT_PORTAL_ID: str = os.getenv("HUBSPOT_PORTAL_ID", "")

    # Salesforce (Phase 9+)
    SF_CLIENT_ID: str = os.getenv("SF_CLIENT_ID", "")
    SF_CLIENT_SECRET: str = os.getenv("SF_CLIENT_SECRET", "")
    SF_USERNAME: str = os.getenv("SF_USERNAME", "")
    SF_PASSWORD: str = os.getenv("SF_PASSWORD", "")
    SF_INSTANCE_URL: str = os.getenv("SF_INSTANCE_URL", "")

    # Outreach (V2+)
    APOLLO_SEQUENCE_ID: str = os.getenv("APOLLO_SEQUENCE_ID", "")
    HEYREACH_SESSION: str = os.getenv("HEYREACH_SESSION", "")

    # Apollo
    APOLLO_API_KEY: str = os.getenv("APOLLO_API_KEY", "")

    # Clay
    CLAY_API_KEY: str = os.getenv("CLAY_API_KEY", "")

    # Gong
    GONG_API_KEY: str = os.getenv("GONG_API_KEY", "")
    GONG_API_SECRET: str = os.getenv("GONG_API_SECRET", "")

    # Google Drive
    GOOGLE_SERVICE_ACCOUNT_JSON_PATH: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_PATH", "")
    GOOGLE_DRIVE_ACCOUNT_PLANS_FOLDER_ID: str = os.getenv("GOOGLE_DRIVE_ACCOUNT_PLANS_FOLDER_ID", "")

    # Exa (account research)
    EXA_API_KEY: str = os.getenv("EXA_API_KEY", "")

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # Observability
    SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")

    # App
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info")
    MOCK_PERSONAS: bool = os.getenv("MOCK_PERSONAS", "false").lower() == "true"


settings = Settings()
