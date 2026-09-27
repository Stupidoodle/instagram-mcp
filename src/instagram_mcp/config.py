"""Configuration management for Instagram MCP Server.

This module handles loading and validating configuration from environment
variables using pydantic-settings.
"""

import logging
import tempfile
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
        extra="ignore",
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
    instagram_app_version: str | None = Field(
        default=None,
        description=(
            "Pin an Instagram app version instagrapi knows. Default: instagrapi's newest, "
            "so upgrading instagrapi upgrades the emulated app (sessions follow on load)."
        ),
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Logging level",
    )
    instagram_bridge_host: str = Field(
        default="127.0.0.1",
        description="Host the instagram-bridge daemon binds to",
    )
    instagram_bridge_port: int = Field(
        default=8082,
        description="Port the instagram-bridge daemon listens on (WhatsApp uses 8080/8081)",
    )
    instagram_bridge_url: str = Field(
        default="http://127.0.0.1:8082",
        description="Base URL the thin instagram-mcp client uses to reach the bridge",
    )
    instagram_media_dir: Path = Field(
        default=Path("media"),
        description="Where inbound and requested photos, videos and voice clips are saved",
    )
    instagram_ephemeral_dir: Path = Field(
        default=Path(tempfile.gettempdir()) / "instagram-ephemeral",
        description="Owner-only temporary folder for view-once and replayable photos",
    )
    instagram_ephemeral_ttl_minutes: int = Field(
        default=15,
        ge=1,
        description="View-once and replayable photos are deleted this long after download",
    )
    instagram_transcriber_url: str = Field(
        default="http://127.0.0.1:8090",
        description="The transcriber service that turns voice notes into text",
    )
    instagram_subscribe: str = Field(
        default="",
        description="Threads to stream on startup: 'alias=thread_id,alias2=thread_id2'",
    )
    instagram_idle_minutes: float = Field(
        default=5,
        ge=0,
        description="Quiet minutes before an idle nudge (0 disables)",
    )
    instagram_idle_backoff_after_minutes: float = Field(
        default=30,
        ge=0,
        description="After this many quiet minutes, each idle nudge doubles the gap to the next",
    )
    instagram_idle_max_minutes: float = Field(
        default=240,
        gt=0,
        description="Longest gap between idle nudges once they back off",
    )
    instagram_control_thread: str = Field(
        default="",
        description="Thread id of an operator control chat (its messages become commands)",
    )
    instagram_debug_prefix: str = Field(
        default="debug:",
        description="Own messages starting with this prefix are operator commands",
    )
    instagram_tz: str | None = Field(
        default=None,
        description="IANA time zone for the idle event's clock, e.g. Europe/Zurich",
    )

    def subscriptions(self) -> list[tuple[str | None, str]]:
        """Parse ``instagram_subscribe`` into (alias or None, thread_id) pairs."""
        pairs: list[tuple[str | None, str]] = []
        for raw in self.instagram_subscribe.split(","):
            item = raw.strip()
            if not item:
                continue
            alias, sep, thread_id = item.partition("=")
            pairs.append((alias.strip(), thread_id.strip()) if sep else (None, item))
        return pairs


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
