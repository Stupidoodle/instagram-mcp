"""Configuration management for Instagram MCP Server.

This module handles loading and validating configuration from environment
variables using pydantic-settings.
"""

import logging
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Attributes:
        instagram_username: Instagram account username.
        instagram_password: Instagram account password (stored securely).
        instagram_2fa_code: Optional 2FA code for login.
        instagram_session_file: Path to store session data for persistence.
        log_level: Logging verbosity level.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    instagram_username: str = Field(
        ...,
        description="Instagram account username",
    )
    instagram_password: SecretStr = Field(
        ...,
        description="Instagram account password",
    )
    instagram_session_file: Path = Field(
        default=Path(".instagram_session"),
        description="Path to session file for persistence",
    )
    instagram_app_version: str = Field(
        default="415.0.0.36.76",
        description="Instagram app version to emulate (bump when Instagram blocks old versions)",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Logging level",
    )


def get_settings() -> Settings:
    """Load and return application settings.

    Returns:
        Settings: Validated application settings from environment.

    Raises:
        ValidationError: If required environment variables are missing.
    """
    return Settings()


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure logging to write to stderr (required for MCP servers).

    MCP servers communicate over stdio, so all logging must go to stderr
    to avoid corrupting the JSON-RPC protocol.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR).

    Returns:
        logging.Logger: Configured logger instance for the package.
    """
    logger = logging.getLogger("instagram_mcp")
    logger.setLevel(getattr(logging, level))

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(getattr(logging, level))
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
