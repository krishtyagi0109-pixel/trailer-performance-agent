"""
Configuration module for the Trailer Performance Agent.

Loads all required environment variables using pydantic-settings.
ClickHouse credentials and Gemini API key must be set in a .env file
or as system environment variables. See .env.example for the template.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # -------------------------------------------------------------------------
    # ClickHouse Cloud connection settings
    # These are passed to the mcp-clickhouse subprocess as environment vars.
    # -------------------------------------------------------------------------
    clickhouse_host: str = Field(
        ...,
        description="ClickHouse Cloud hostname (e.g., xxx.clickhouse.cloud)",
    )
    clickhouse_port: str = Field(
        default="8443",
        description="ClickHouse native port (8443 for ClickHouse Cloud with TLS)",
    )
    clickhouse_user: str = Field(
        default="default",
        description="ClickHouse username",
    )
    clickhouse_password: str = Field(
        ...,
        description="ClickHouse password",
    )
    clickhouse_secure: str = Field(
        default="true",
        description="Whether to use TLS for ClickHouse connection",
    )
    clickhouse_database: str = Field(
        default="default",
        description="Default database containing the trailer_stats table",
    )

    # -------------------------------------------------------------------------
    # Google Gemini API
    # -------------------------------------------------------------------------
    gemini_api_key: str = Field(
        ...,
        description="Google Gemini API key for the generative AI model",
    )
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description="Gemini model to use for agent reasoning",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }

    def get_clickhouse_env(self) -> dict[str, str]:
        """
        Returns ClickHouse credentials as a dict of environment variables,
        formatted for passing to the mcp-clickhouse subprocess.
        """
        return {
            "CLICKHOUSE_HOST": self.clickhouse_host,
            "CLICKHOUSE_PORT": self.clickhouse_port,
            "CLICKHOUSE_USER": self.clickhouse_user,
            "CLICKHOUSE_PASSWORD": self.clickhouse_password,
            "CLICKHOUSE_SECURE": self.clickhouse_secure,
            "CLICKHOUSE_VERIFY": "true",
            "CLICKHOUSE_CONNECT_TIMEOUT": "30",
        }


# Singleton instance — import this throughout the app
settings = Settings()
