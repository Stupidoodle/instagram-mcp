"""Unit tests for Instagram client wrapper."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from instagrapi.exceptions import BadPassword, ChallengeRequired, TwoFactorRequired

from instagram_mcp.client import (
    AuthenticationError,
    InstagramClient,
    SessionError,
    _convert_message,
    _convert_thread,
    _convert_user,
    _determine_media_type,
    interactive_login,
)
from instagram_mcp.models.schemas import MediaType


class TestConvertUser:
    def test_convert_user_basic(self, mock_ig_user: MagicMock) -> None:
        user = _convert_user(mock_ig_user)

        assert user.user_id == "123456"
        assert user.username == "test_user"
        assert user.full_name == "Test User"
        assert user.profile_pic_url == "https://example.com/pic.jpg"
        assert user.is_verified is False

    def test_convert_user_no_full_name(self, mock_ig_user: MagicMock) -> None:
        mock_ig_user.full_name = None

        user = _convert_user(mock_ig_user)

        assert user.full_name == ""

    def test_convert_user_no_profile_pic(self, mock_ig_user: MagicMock) -> None:
        mock_ig_user.profile_pic_url = None

        user = _convert_user(mock_ig_user)

        assert user.profile_pic_url is None

    def test_convert_user_verified(self, mock_ig_user: MagicMock) -> None:
        mock_ig_user.is_verified = True

        user = _convert_user(mock_ig_user)

        assert user.is_verified is True

    def test_convert_user_none_verified(self, mock_ig_user: MagicMock) -> None:
        mock_ig_user.is_verified = None

        user = _convert_user(mock_ig_user)

        assert user.is_verified is False


class TestDetermineMediaType:
    @pytest.mark.parametrize(
        ("item_type", "expected"),
        [
            ("text", MediaType.TEXT),
            ("media", MediaType.PHOTO),
            ("video", MediaType.VIDEO),
            ("voice_media", MediaType.VOICE),
            ("link", MediaType.LINK),
            ("media_share", MediaType.MEDIA_SHARE),
            ("profile", MediaType.PROFILE),
            ("clip", MediaType.REEL_SHARE),
            ("story_share", MediaType.STORY_SHARE),
            ("unknown_type", MediaType.UNKNOWN),
        ],
    )
    def test_determine_media_type(self, item_type: str, expected: MediaType) -> None:
        msg = MagicMock()
        msg.item_type = item_type

        result = _determine_media_type(msg)

        assert result == expected

    def test_determine_media_type_no_item_type(self) -> None:
        msg = MagicMock(spec=[])  # No item_type attribute

        result = _determine_media_type(msg)

        assert result == MediaType.TEXT


class TestConvertMessage:
    def test_convert_message_text(
        self, mock_ig_message: MagicMock, mock_ig_user: MagicMock
    ) -> None:
        msg = _convert_message(mock_ig_message, "123456789")

        assert msg.message_id == "111111111"
        assert msg.thread_id == "123456789"
        assert msg.sender.username == "test_user"
        assert msg.content.text == "Hello, this is a test message!"
        assert msg.content.media_type == MediaType.TEXT
        assert msg.is_sent_by_viewer is True

    def test_convert_message_no_text(self, mock_ig_message: MagicMock) -> None:
        mock_ig_message.text = None
        mock_ig_message.item_type = "media"

        msg = _convert_message(mock_ig_message, "123456789")

        assert msg.content.text is None
        assert msg.content.media_type == MediaType.PHOTO

    def test_convert_message_with_media(self, mock_ig_message: MagicMock) -> None:
        mock_ig_message.media = MagicMock()
        mock_ig_message.media.thumbnail_url = "https://example.com/media.jpg"

        msg = _convert_message(mock_ig_message, "123456789")

        assert msg.content.media_url == "https://example.com/media.jpg"

    def test_convert_message_no_user(self, mock_ig_message: MagicMock) -> None:
        mock_ig_message.user = None
        mock_ig_message.user_id = None
        mock_ig_message.is_sent_by_viewer = False

        msg = _convert_message(mock_ig_message, "123456789")

        assert msg.sender.user_id == "0"
        assert msg.sender.username == "unknown"

    def test_convert_message_is_sent_by_viewer_none(self, mock_ig_message: MagicMock) -> None:
        mock_ig_message.is_sent_by_viewer = None

        msg = _convert_message(mock_ig_message, "123456789")

        assert msg.is_sent_by_viewer is False


class TestConvertThread:
    def test_convert_thread_basic(self, mock_ig_thread: MagicMock) -> None:
        thread = _convert_thread(mock_ig_thread)

        assert thread.thread_id == "123456789"
        assert thread.thread_title == "Test Conversation"
        assert len(thread.users) == 1
        assert thread.is_group is False
        assert thread.is_muted is False
        assert thread.unread is True
        assert len(thread.messages) == 0  # Not included by default

    def test_convert_thread_with_messages(self, mock_ig_thread: MagicMock) -> None:
        thread = _convert_thread(mock_ig_thread, include_messages=True)

        assert len(thread.messages) == 1
        assert thread.messages[0].message_id == "111111111"

    def test_convert_thread_no_users(self, mock_ig_thread: MagicMock) -> None:
        mock_ig_thread.users = None

        thread = _convert_thread(mock_ig_thread)

        assert thread.users == []

    def test_convert_thread_no_title(self, mock_ig_thread: MagicMock) -> None:
        mock_ig_thread.thread_title = None

        thread = _convert_thread(mock_ig_thread)

        assert thread.thread_title == ""

    def test_convert_thread_muted(self, mock_ig_thread: MagicMock) -> None:
        mock_ig_thread.muted = True

        thread = _convert_thread(mock_ig_thread)

        assert thread.is_muted is True

    def test_convert_thread_read(self, mock_ig_thread: MagicMock) -> None:
        mock_ig_thread.read_state = 0  # read_state == 0 means read (not unread)

        thread = _convert_thread(mock_ig_thread)

        assert thread.unread is False

    def test_convert_thread_no_messages(self, mock_ig_thread: MagicMock) -> None:
        mock_ig_thread.messages = None

        thread = _convert_thread(mock_ig_thread, include_messages=True)

        assert thread.messages == []
        assert thread.message_count == 0


class TestInstagramClient:
    def test_init(self, tmp_path: Path) -> None:
        with patch("instagram_mcp.client.Client"):
            client = InstagramClient(session_file=tmp_path / "session")

        assert client.session_file == tmp_path / "session"
        assert client.is_logged_in is False

    def test_init_default_session_file(self) -> None:
        with patch("instagram_mcp.client.Client"):
            client = InstagramClient()

        assert client.session_file == Path(".instagram_session")

    def test_is_logged_in(self, instagram_client: InstagramClient) -> None:
        assert instagram_client.is_logged_in is True

        instagram_client._logged_in = False
        assert instagram_client.is_logged_in is False

    def test_load_session_no_file(self, tmp_path: Path) -> None:
        with patch("instagram_mcp.client.Client"):
            client = InstagramClient(session_file=tmp_path / "nonexistent")

        result = client.load_session()

        assert result is False

    def test_load_session_success(self, tmp_path: Path, mock_instagrapi_client: MagicMock) -> None:
        session_file = tmp_path / "session"
        session_data = {"authorization_data": {"sessionid": "test_session"}}
        session_file.write_text(json.dumps(session_data))

        with patch("instagram_mcp.client.Client", return_value=mock_instagrapi_client):
            client = InstagramClient(session_file=session_file)
            result = client.load_session()

        assert result is True
        assert client.is_logged_in is True

    def test_load_session_invalid_json(self, tmp_path: Path) -> None:
        session_file = tmp_path / "session"
        session_file.write_text("not valid json")

        with patch("instagram_mcp.client.Client"):
            client = InstagramClient(session_file=session_file)

        with pytest.raises(SessionError, match="Invalid session file format"):
            client.load_session()

    def test_save_session(self, tmp_path: Path, mock_instagrapi_client: MagicMock) -> None:
        session_file = tmp_path / "session"

        with patch("instagram_mcp.client.Client", return_value=mock_instagrapi_client):
            client = InstagramClient(session_file=session_file)
            client.save_session()

        assert session_file.exists()
        # Check file permissions are restrictive
        assert (session_file.stat().st_mode & 0o777) == 0o600

    def test_login_success(self, tmp_path: Path, mock_instagrapi_client: MagicMock) -> None:
        with patch("instagram_mcp.client.Client", return_value=mock_instagrapi_client):
            client = InstagramClient(session_file=tmp_path / "session")
            client.login("test_user", "test_pass")

        assert client.is_logged_in is True
        mock_instagrapi_client.login.assert_called_once_with("test_user", "test_pass")

    def test_login_bad_password(self, tmp_path: Path, mock_instagrapi_client: MagicMock) -> None:
        mock_instagrapi_client.login.side_effect = BadPassword()

        with patch("instagram_mcp.client.Client", return_value=mock_instagrapi_client):
            client = InstagramClient(session_file=tmp_path / "session")

        with pytest.raises(AuthenticationError, match="Invalid password"):
            client.login("test_user", "wrong_pass")

    def test_login_2fa_required_no_handler(
        self, tmp_path: Path, mock_instagrapi_client: MagicMock
    ) -> None:
        mock_instagrapi_client.login.side_effect = TwoFactorRequired()

        with patch("instagram_mcp.client.Client", return_value=mock_instagrapi_client):
            client = InstagramClient(session_file=tmp_path / "session")

        with pytest.raises(AuthenticationError, match="2FA required"):
            client.login("test_user", "test_pass")

    def test_login_2fa_with_handler(
        self, tmp_path: Path, mock_instagrapi_client: MagicMock
    ) -> None:
        # First call raises TwoFactorRequired, second call with code succeeds
        mock_instagrapi_client.login.side_effect = [TwoFactorRequired(), True]

        with patch("instagram_mcp.client.Client", return_value=mock_instagrapi_client):
            client = InstagramClient(session_file=tmp_path / "session")
            handler = MagicMock(return_value="123456")
            client.login("test_user", "test_pass", verification_code_handler=handler)

        assert client.is_logged_in is True
        # Second login call should have verification_code
        assert mock_instagrapi_client.login.call_count == 2

    def test_login_challenge_required(
        self, tmp_path: Path, mock_instagrapi_client: MagicMock
    ) -> None:
        mock_instagrapi_client.login.side_effect = ChallengeRequired()

        with patch("instagram_mcp.client.Client", return_value=mock_instagrapi_client):
            client = InstagramClient(session_file=tmp_path / "session")

        with pytest.raises(AuthenticationError, match="Challenge required"):
            client.login("test_user", "test_pass")

    def test_login_or_load_session_loads(
        self, tmp_path: Path, mock_instagrapi_client: MagicMock
    ) -> None:
        session_file = tmp_path / "session"
        session_data = {"authorization_data": {"sessionid": "test"}}
        session_file.write_text(json.dumps(session_data))

        with patch("instagram_mcp.client.Client", return_value=mock_instagrapi_client):
            client = InstagramClient(session_file=session_file)
            client.login_or_load_session("test_user", "test_pass")

        # Should not call login since session was loaded
        mock_instagrapi_client.login.assert_not_called()

    def test_login_or_load_session_falls_back(
        self, tmp_path: Path, mock_instagrapi_client: MagicMock
    ) -> None:
        with patch("instagram_mcp.client.Client", return_value=mock_instagrapi_client):
            client = InstagramClient(session_file=tmp_path / "nonexistent")
            client.login_or_load_session("test_user", "test_pass")

        mock_instagrapi_client.login.assert_called_once()


class TestAppVersion:
    """Tests for Instagram app version management."""

    def test_init_with_app_version(self, tmp_path: Path) -> None:
        """Test that passing app_version applies it on init."""
        mock_client = MagicMock()
        mock_client.get_settings.return_value = {
            "device_settings": {"app_version": "269.0.0.18.75"},
            "user_agent": "Instagram 269.0.0.18.75 Android (26/8.0.0; 480dpi; 1080x1920; OnePlus; 6T Dev; devitron; qcom; en_US; 314665256)",
        }
        with patch("instagram_mcp.client.Client", return_value=mock_client):
            client = InstagramClient(
                session_file=tmp_path / "session",
                app_version="415.0.0.36.76",
            )

        assert client._app_version == "415.0.0.36.76"
        # set_settings should have been called with the updated version
        call_args = mock_client.set_settings.call_args[0][0]
        assert call_args["device_settings"]["app_version"] == "415.0.0.36.76"
        assert "415.0.0.36.76" in call_args["user_agent"]
        assert "269.0.0.18.75" not in call_args["user_agent"]

    def test_init_without_app_version(self, tmp_path: Path) -> None:
        """Test that omitting app_version leaves client defaults alone."""
        mock_client = MagicMock()
        with patch("instagram_mcp.client.Client", return_value=mock_client):
            client = InstagramClient(session_file=tmp_path / "session")

        assert client._app_version is None
        # get_settings should NOT be called for version patching
        mock_client.get_settings.assert_not_called()

    def test_load_session_applies_version(
        self, tmp_path: Path, mock_instagrapi_client: MagicMock
    ) -> None:
        """Test that loading a session with old version gets patched."""
        session_file = tmp_path / "session"
        session_data = {"authorization_data": {"sessionid": "test_session"}}
        session_file.write_text(json.dumps(session_data))

        mock_instagrapi_client.get_settings.return_value = {
            "device_settings": {"app_version": "269.0.0.18.75"},
            "user_agent": "Instagram 269.0.0.18.75 Android (...)",
        }

        with patch("instagram_mcp.client.Client", return_value=mock_instagrapi_client):
            client = InstagramClient(
                session_file=session_file,
                app_version="415.0.0.36.76",
            )
            client.load_session()

        # set_settings called during load_session to patch version
        calls = mock_instagrapi_client.set_settings.call_args_list
        # Find the call that has the new version (may be init or load_session)
        patched = any(
            c[0][0].get("device_settings", {}).get("app_version") == "415.0.0.36.76" for c in calls
        )
        assert patched

    def test_apply_app_version_noop_when_same(self, tmp_path: Path) -> None:
        """Test that _apply_app_version is a no-op when version matches."""
        mock_client = MagicMock()
        mock_client.get_settings.return_value = {
            "device_settings": {"app_version": "415.0.0.36.76"},
            "user_agent": "Instagram 415.0.0.36.76 Android (...)",
        }
        with patch("instagram_mcp.client.Client", return_value=mock_client):
            client = InstagramClient(
                session_file=tmp_path / "session",
                app_version="415.0.0.36.76",
            )

        # get_settings called once (in _apply_app_version), but set_settings NOT called
        # because version already matches
        mock_client.set_settings.assert_not_called()


class TestInstagramClientThreadOperations:
    def test_get_threads(
        self, instagram_client: InstagramClient, mock_ig_thread: MagicMock
    ) -> None:
        instagram_client.client.direct_threads.return_value = [mock_ig_thread]

        threads = instagram_client.get_threads(amount=10)

        assert len(threads) == 1
        assert threads[0].thread_id == "123456789"
        instagram_client.client.direct_threads.assert_called_once_with(amount=10)

    def test_get_thread(self, instagram_client: InstagramClient, mock_ig_thread: MagicMock) -> None:
        instagram_client.client.direct_thread.return_value = mock_ig_thread

        thread = instagram_client.get_thread("123456789", amount=20)

        assert thread.thread_id == "123456789"
        assert len(thread.messages) == 1
        instagram_client.client.direct_thread.assert_called_once_with(
            thread_id=int("123456789"), amount=20
        )

    def test_get_pending_threads(
        self, instagram_client: InstagramClient, mock_ig_thread: MagicMock
    ) -> None:
        instagram_client.client.direct_pending_inbox.return_value = [mock_ig_thread]

        threads = instagram_client.get_pending_threads()

        assert len(threads) == 1
        instagram_client.client.direct_pending_inbox.assert_called_once()

    def test_search_threads(
        self, instagram_client: InstagramClient, mock_ig_thread: MagicMock
    ) -> None:
        # search_threads fetches all threads and filters locally by username/title
        instagram_client.client.direct_threads.return_value = [mock_ig_thread]

        threads = instagram_client.search_threads("test")

        assert len(threads) == 1
        instagram_client.client.direct_threads.assert_called_once_with(amount=50)

    def test_hide_thread(self, instagram_client: InstagramClient) -> None:
        instagram_client.client.direct_thread_hide.return_value = True

        result = instagram_client.hide_thread("123456789")

        assert result is True
        instagram_client.client.direct_thread_hide.assert_called_once_with(
            thread_id=int("123456789")
        )

    def test_mark_thread_unread(self, instagram_client: InstagramClient) -> None:
        instagram_client.client.direct_thread_mark_unread.return_value = True

        result = instagram_client.mark_thread_unread("123456789")

        assert result is True

    def test_mute_thread(self, instagram_client: InstagramClient) -> None:
        instagram_client.client.direct_thread_mute.return_value = True

        result = instagram_client.mute_thread("123456789")

        assert result is True

    def test_unmute_thread(self, instagram_client: InstagramClient) -> None:
        instagram_client.client.direct_thread_unmute.return_value = True

        result = instagram_client.unmute_thread("123456789")

        assert result is True


class TestInstagramClientMessageOperations:
    def test_send_message_to_users(
        self, instagram_client: InstagramClient, mock_ig_message: MagicMock
    ) -> None:
        mock_ig_message.thread_id = "123456789"
        instagram_client.client.direct_send.return_value = mock_ig_message

        result = instagram_client.send_message("Hello", user_ids=["123"])

        assert result is not None
        assert result.message_id == "111111111"
        instagram_client.client.direct_send.assert_called_once_with(
            text="Hello", user_ids=[123], thread_ids=None
        )

    def test_send_message_to_threads(
        self, instagram_client: InstagramClient, mock_ig_message: MagicMock
    ) -> None:
        mock_ig_message.thread_id = "123456789"
        instagram_client.client.direct_send.return_value = mock_ig_message

        result = instagram_client.send_message("Hello", thread_ids=["123456789"])

        assert result is not None
        instagram_client.client.direct_send.assert_called_once_with(
            text="Hello", user_ids=None, thread_ids=[int("123456789")]
        )

    def test_send_message_failure(self, instagram_client: InstagramClient) -> None:
        instagram_client.client.direct_send.return_value = None

        result = instagram_client.send_message("Hello", user_ids=["123"])

        assert result is None

    def test_reply_to_thread(
        self, instagram_client: InstagramClient, mock_ig_message: MagicMock
    ) -> None:
        instagram_client.client.direct_answer.return_value = mock_ig_message

        result = instagram_client.reply_to_thread("123456789", "Reply")

        assert result is not None
        instagram_client.client.direct_answer.assert_called_once_with(
            thread_id=int("123456789"), text="Reply"
        )

    def test_get_messages(
        self, instagram_client: InstagramClient, mock_ig_thread: MagicMock
    ) -> None:
        # get_messages fetches the thread first to get user info for sender lookup
        instagram_client.client.direct_thread.return_value = mock_ig_thread

        messages = instagram_client.get_messages("123456789", amount=20)

        assert len(messages) == 1
        instagram_client.client.direct_thread.assert_called_once_with(
            thread_id=int("123456789"), amount=20
        )

    def test_delete_message(self, instagram_client: InstagramClient) -> None:
        instagram_client.client.direct_message_delete.return_value = True

        result = instagram_client.delete_message("123456789", "111111111")

        assert result is True
        instagram_client.client.direct_message_delete.assert_called_once_with(
            thread_id=int("123456789"), message_id=int("111111111")
        )


class TestInstagramClientMediaOperations:
    def test_send_photo(
        self, instagram_client: InstagramClient, mock_ig_message: MagicMock, tmp_path: Path
    ) -> None:
        mock_ig_message.thread_id = "123456789"
        instagram_client.client.direct_send_photo.return_value = mock_ig_message
        photo_path = tmp_path / "photo.jpg"
        photo_path.touch()

        result = instagram_client.send_photo(photo_path, user_ids=["123"])

        assert result is not None
        instagram_client.client.direct_send_photo.assert_called_once()

    def test_send_video(
        self, instagram_client: InstagramClient, mock_ig_message: MagicMock, tmp_path: Path
    ) -> None:
        mock_ig_message.thread_id = "123456789"
        instagram_client.client.direct_send_video.return_value = mock_ig_message
        video_path = tmp_path / "video.mp4"
        video_path.touch()

        result = instagram_client.send_video(video_path, user_ids=["123"])

        assert result is not None
        instagram_client.client.direct_send_video.assert_called_once()

    def test_share_media(self, instagram_client: InstagramClient) -> None:
        instagram_client.client.direct_media_share.return_value = True

        result = instagram_client.share_media("333333333", user_ids=["123"])

        assert result is True
        instagram_client.client.direct_media_share.assert_called_once()

    def test_share_profile(self, instagram_client: InstagramClient) -> None:
        instagram_client.client.direct_profile_share.return_value = True

        result = instagram_client.share_profile("444444444", target_user_ids=["123"])

        assert result is True
        instagram_client.client.direct_profile_share.assert_called_once()


class TestInteractiveLogin:
    def test_interactive_login_success(self, mock_settings: MagicMock) -> None:
        with (
            patch("instagram_mcp.config.get_settings", return_value=mock_settings),
            patch("instagram_mcp.config.setup_logging"),
            patch("instagram_mcp.client.InstagramClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            interactive_login()

            mock_client.login.assert_called_once()

    def test_interactive_login_failure(self, mock_settings: MagicMock) -> None:
        with (
            patch("instagram_mcp.config.get_settings", return_value=mock_settings),
            patch("instagram_mcp.config.setup_logging"),
            patch("instagram_mcp.client.InstagramClient") as mock_client_class,
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_client = MagicMock()
            mock_client.login.side_effect = AuthenticationError("Failed")
            mock_client_class.return_value = mock_client

            interactive_login()

        assert exc_info.value.code == 1


class TestRetryOnRateLimit:
    """Tests for _retry_on_rate_limit exponential backoff."""

    def test_retries_on_467_then_succeeds(self, instagram_client: InstagramClient) -> None:
        """Test that 467 errors trigger retry and eventual success."""
        mock_thread = MagicMock()
        mock_thread.users = []
        mock_thread.messages = []
        mock_thread.last_seen_at = None

        instagram_client.client.direct_thread.side_effect = [
            Exception("467 Client Error: - for url: https://..."),
            Exception("467 Client Error: - for url: https://..."),
            mock_thread,
        ]

        with patch("instagram_mcp.client.time.sleep") as mock_sleep:
            messages = instagram_client.get_messages("123456789", amount=5)

        assert messages == []
        assert instagram_client.client.direct_thread.call_count == 3
        # Backoff: 1s, 2s
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    def test_backoff_caps_at_30_seconds(self, instagram_client: InstagramClient) -> None:
        """Test that backoff delay is capped at 30 seconds."""
        mock_thread = MagicMock()
        mock_thread.users = []
        mock_thread.messages = []
        mock_thread.last_seen_at = None

        # 7 failures: delays = 1, 2, 4, 8, 16, 30, 30 then success
        instagram_client.client.direct_thread.side_effect = [
            *[Exception("467 Client Error") for _ in range(7)],
            mock_thread,
        ]

        with patch("instagram_mcp.client.time.sleep") as mock_sleep:
            instagram_client.get_messages("123456789", amount=5)

        assert mock_sleep.call_count == 7
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1, 2, 4, 8, 16, 30, 30]

    def test_non_rate_limit_errors_raise_immediately(
        self, instagram_client: InstagramClient
    ) -> None:
        """Test that non-467/429 errors are not retried."""
        instagram_client.client.direct_thread.side_effect = Exception("Connection refused")

        with pytest.raises(Exception, match="Connection refused"):
            instagram_client.get_messages("123456789", amount=5)

        assert instagram_client.client.direct_thread.call_count == 1

    def test_retries_on_429(self, instagram_client: InstagramClient) -> None:
        """Test that HTTP 429 also triggers retry."""
        mock_thread = MagicMock()
        mock_thread.users = []
        mock_thread.messages = []
        mock_thread.last_seen_at = None

        instagram_client.client.direct_thread.side_effect = [
            Exception("429 Too Many Requests"),
            mock_thread,
        ]

        with patch("instagram_mcp.client.time.sleep"):
            messages = instagram_client.get_messages("123456789", amount=5)

        assert messages == []
        assert instagram_client.client.direct_thread.call_count == 2
