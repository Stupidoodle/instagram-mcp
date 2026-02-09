"""Unit tests for configuration management."""

import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from instagram_mcp.config import Settings, get_settings, setup_logging


class TestSettings:
    def test_settings_from_env(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "INSTAGRAM_USERNAME": "test_user",
                "INSTAGRAM_PASSWORD": "test_pass",
            },
        ):
            settings = Settings()

        assert settings.instagram_username == "test_user"
        assert settings.instagram_password.get_secret_value() == "test_pass"

    def test_settings_defaults(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "INSTAGRAM_USERNAME": "test_user",
                "INSTAGRAM_PASSWORD": "test_pass",
            },
        ):
            settings = Settings()

        assert settings.instagram_session_file == Path(".instagram_session")
        assert settings.log_level == "INFO"
        assert settings.instagram_app_version == "415.0.0.36.76"

    def test_settings_custom_values(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "INSTAGRAM_USERNAME": "test_user",
                "INSTAGRAM_PASSWORD": "test_pass",
                "INSTAGRAM_SESSION_FILE": "/custom/path",
                "INSTAGRAM_APP_VERSION": "420.0.0.1.2",
                "LOG_LEVEL": "DEBUG",
            },
        ):
            settings = Settings()

        assert settings.instagram_session_file == Path("/custom/path")
        assert settings.log_level == "DEBUG"
        assert settings.instagram_app_version == "420.0.0.1.2"

    def test_settings_missing_username(self) -> None:
        # Use _env_file=None to prevent loading from .env file
        with (
            patch.dict(
                "os.environ",
                {"INSTAGRAM_PASSWORD": "test_pass"},
                clear=True,
            ),
            pytest.raises(ValidationError),
        ):
            Settings(_env_file=None)

    def test_settings_missing_password(self) -> None:
        # Use _env_file=None to prevent loading from .env file
        with (
            patch.dict(
                "os.environ",
                {"INSTAGRAM_USERNAME": "test_user"},
                clear=True,
            ),
            pytest.raises(ValidationError),
        ):
            Settings(_env_file=None)

    def test_settings_invalid_log_level(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "INSTAGRAM_USERNAME": "test_user",
                    "INSTAGRAM_PASSWORD": "test_pass",
                    "LOG_LEVEL": "INVALID",
                },
            ),
            pytest.raises(ValidationError),
        ):
            Settings()


class TestGetSettings:
    def test_get_settings(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "INSTAGRAM_USERNAME": "test_user",
                "INSTAGRAM_PASSWORD": "test_pass",
            },
        ):
            settings = get_settings()

        assert settings.instagram_username == "test_user"


class TestSetupLogging:
    def test_setup_logging_default(self) -> None:
        logger = setup_logging()

        assert logger.name == "instagram_mcp"
        assert logger.level == logging.INFO

    def test_setup_logging_debug(self) -> None:
        logger = setup_logging("DEBUG")

        assert logger.level == logging.DEBUG

    def test_setup_logging_warning(self) -> None:
        logger = setup_logging("WARNING")

        assert logger.level == logging.WARNING

    def test_setup_logging_error(self) -> None:
        logger = setup_logging("ERROR")

        assert logger.level == logging.ERROR

    def test_setup_logging_has_handler(self) -> None:
        logger = setup_logging()

        assert len(logger.handlers) >= 1
        # Handler should write to stderr, not stdout
        handler = logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
