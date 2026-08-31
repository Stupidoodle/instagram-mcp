"""Unit tests for media messaging tools."""

from pathlib import Path
from unittest.mock import MagicMock

from mcp.server.fastmcp import FastMCP

from instagram_mcp.client import InstagramClient
from instagram_mcp.models.schemas import DirectMessage
from instagram_mcp.tools.media import register_media_tools


class TestMediaTools:
    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.mcp = FastMCP("test")
        self.mock_client = MagicMock(spec=InstagramClient)
        register_media_tools(self.mcp, self.mock_client)

    def _get_tool_fn(self, name: str):
        """Get tool function by name."""
        for tool in self.mcp._tool_manager._tools.values():
            if tool.name == name:
                return tool.fn
        return None

    def test_send_photo_success(self, sample_message: DirectMessage, tmp_path: Path) -> None:
        self.mock_client.send_photo.return_value = sample_message
        photo_path = tmp_path / "test.jpg"
        photo_path.touch()

        tool_fn = self._get_tool_fn("send_photo")
        assert tool_fn is not None
        result = tool_fn(path=str(photo_path), user_ids=["123"], thread_ids=None)

        assert result["success"] is True
        assert result["message_id"] == "111111111"
        self.mock_client.send_photo.assert_called_once()

    def test_send_photo_no_target(self, tmp_path: Path) -> None:
        photo_path = tmp_path / "test.jpg"
        photo_path.touch()

        tool_fn = self._get_tool_fn("send_photo")
        result = tool_fn(path=str(photo_path), user_ids=None, thread_ids=None)

        assert "error" in result
        assert "Must specify either user_ids or thread_ids" in result["error"]

    def test_send_photo_file_not_found(self) -> None:
        tool_fn = self._get_tool_fn("send_photo")
        result = tool_fn(path="/nonexistent/photo.jpg", user_ids=["123"], thread_ids=None)

        assert "error" in result
        assert "Photo file not found" in result["error"]

    def test_send_photo_unsupported_format(self, tmp_path: Path) -> None:
        photo_path = tmp_path / "test.txt"
        photo_path.touch()

        tool_fn = self._get_tool_fn("send_photo")
        result = tool_fn(path=str(photo_path), user_ids=["123"], thread_ids=None)

        assert "error" in result
        assert "Unsupported image format" in result["error"]

    def test_send_photo_failure(self, tmp_path: Path) -> None:
        self.mock_client.send_photo.return_value = None
        photo_path = tmp_path / "test.jpg"
        photo_path.touch()

        tool_fn = self._get_tool_fn("send_photo")
        result = tool_fn(path=str(photo_path), user_ids=["123"], thread_ids=None)

        assert result["success"] is False
        assert "error" in result

    def test_send_photo_error(self, tmp_path: Path) -> None:
        self.mock_client.send_photo.side_effect = Exception("API Error")
        photo_path = tmp_path / "test.jpg"
        photo_path.touch()

        tool_fn = self._get_tool_fn("send_photo")
        result = tool_fn(path=str(photo_path), user_ids=["123"], thread_ids=None)

        assert "error" in result
        assert "API Error" in result["error"]

    def test_send_video_success(self, sample_message: DirectMessage, tmp_path: Path) -> None:
        self.mock_client.send_video.return_value = sample_message
        video_path = tmp_path / "test.mp4"
        video_path.touch()

        tool_fn = self._get_tool_fn("send_video")
        assert tool_fn is not None
        result = tool_fn(path=str(video_path), user_ids=["123"], thread_ids=None)

        assert result["success"] is True
        assert result["message_id"] == "111111111"
        self.mock_client.send_video.assert_called_once()

    def test_send_video_no_target(self, tmp_path: Path) -> None:
        video_path = tmp_path / "test.mp4"
        video_path.touch()

        tool_fn = self._get_tool_fn("send_video")
        result = tool_fn(path=str(video_path), user_ids=None, thread_ids=None)

        assert "error" in result
        assert "Must specify either user_ids or thread_ids" in result["error"]

    def test_send_video_file_not_found(self) -> None:
        tool_fn = self._get_tool_fn("send_video")
        result = tool_fn(path="/nonexistent/video.mp4", user_ids=["123"], thread_ids=None)

        assert "error" in result
        assert "Video file not found" in result["error"]

    def test_send_video_unsupported_format(self, tmp_path: Path) -> None:
        video_path = tmp_path / "test.txt"
        video_path.touch()

        tool_fn = self._get_tool_fn("send_video")
        result = tool_fn(path=str(video_path), user_ids=["123"], thread_ids=None)

        assert "error" in result
        assert "Unsupported video format" in result["error"]

    def test_send_video_failure(self, tmp_path: Path) -> None:
        self.mock_client.send_video.return_value = None
        video_path = tmp_path / "test.mp4"
        video_path.touch()

        tool_fn = self._get_tool_fn("send_video")
        result = tool_fn(path=str(video_path), user_ids=["123"], thread_ids=None)

        assert result["success"] is False
        assert "error" in result

    def test_share_media_success(self) -> None:
        self.mock_client.share_media.return_value = True

        tool_fn = self._get_tool_fn("share_media")
        assert tool_fn is not None
        result = tool_fn(media_id="333333333", user_ids=["123"], thread_ids=None)

        assert result["success"] is True
        assert result["media_id"] == "333333333"
        self.mock_client.share_media.assert_called_once_with(
            media_id="333333333", user_ids=["123"], thread_ids=None
        )

    def test_share_media_no_target(self) -> None:
        tool_fn = self._get_tool_fn("share_media")
        result = tool_fn(media_id="333333333", user_ids=None, thread_ids=None)

        assert "error" in result
        assert "Must specify either user_ids or thread_ids" in result["error"]

    def test_share_media_failure(self) -> None:
        self.mock_client.share_media.return_value = False

        tool_fn = self._get_tool_fn("share_media")
        result = tool_fn(media_id="333333333", user_ids=["123"], thread_ids=None)

        assert result["success"] is False

    def test_share_media_error(self) -> None:
        self.mock_client.share_media.side_effect = Exception("API Error")

        tool_fn = self._get_tool_fn("share_media")
        result = tool_fn(media_id="333333333", user_ids=["123"], thread_ids=None)

        assert "error" in result
        assert "API Error" in result["error"]

    def test_share_profile_success(self) -> None:
        self.mock_client.share_profile.return_value = True

        tool_fn = self._get_tool_fn("share_profile")
        assert tool_fn is not None
        result = tool_fn(user_id="444444444", target_user_ids=["123"], thread_ids=None)

        assert result["success"] is True
        assert result["user_id"] == "444444444"
        self.mock_client.share_profile.assert_called_once_with(
            user_id="444444444", target_user_ids=["123"], thread_ids=None
        )

    def test_share_profile_no_target(self) -> None:
        tool_fn = self._get_tool_fn("share_profile")
        result = tool_fn(user_id="444444444", target_user_ids=None, thread_ids=None)

        assert "error" in result
        assert "Must specify either target_user_ids or thread_ids" in result["error"]

    def test_share_profile_failure(self) -> None:
        self.mock_client.share_profile.return_value = False

        tool_fn = self._get_tool_fn("share_profile")
        result = tool_fn(user_id="444444444", target_user_ids=["123"], thread_ids=None)

        assert result["success"] is False

    def test_share_profile_error(self) -> None:
        self.mock_client.share_profile.side_effect = Exception("API Error")

        tool_fn = self._get_tool_fn("share_profile")
        result = tool_fn(user_id="444444444", target_user_ids=["123"], thread_ids=None)

        assert "error" in result
        assert "API Error" in result["error"]
