"""Instagram client wrapper with session management.

This module provides a wrapper around instagrapi's Client class with
session persistence and proper error handling for MCP server usage.
"""

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from instagrapi import Client
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    ClientUnauthorizedError,
    LoginRequired,
    TwoFactorRequired,
)
from instagrapi.types import DirectMessage as IGDirectMessage
from instagrapi.types import DirectThread as IGDirectThread
from instagrapi.types import User as IGUser

from instagram_mcp.models.schemas import (
    DirectMessage,
    DirectThread,
    MediaType,
    MessageContent,
    ThreadUser,
)

logger = logging.getLogger("instagram_mcp")


def _fix_instagrapi_extractors() -> None:
    """Replace instagrapi extractors with fixed versions.

    Fixes:
    1. visual_media.expiring_media_action_summary.timestamp not converted from microseconds
    2. action_log.description not extracted to text field for action_log messages

    This replaces the extractors entirely with fixed versions.
    """
    import datetime as dt

    from instagrapi import extractors
    from instagrapi.extractors import (
        InstagramIdCodec,
        extract_direct_media,
        extract_media_v1,
        extract_media_v1_xma,
    )
    from instagrapi.types import DirectMessage as IGDirectMessage
    from instagrapi.types import ReplyMessage

    def fixed_extract_reply_message(data: dict[str, Any]) -> ReplyMessage:
        """Fixed version that converts all timestamp fields."""
        data["id"] = data.get("item_id")

        if "media_share" in data:
            ms = data["media_share"]
            if not ms.get("code"):
                ms["code"] = InstagramIdCodec.encode(ms["id"])
            data["media_share"] = extract_media_v1(ms)

        if "media" in data:
            data["media"] = extract_direct_media(data["media"])

        clip = data.get("clip", {})
        if clip:
            if "clip" in clip:
                clip = clip.get("clip")
            data["clip"] = extract_media_v1(clip)

        # Convert main timestamp
        data["timestamp"] = datetime.fromtimestamp(int(data["timestamp"]) // 1_000_000)
        data["user_id"] = str(data["user_id"])

        # FIX: Convert visual_media.expiring_media_action_summary.timestamp
        visual_media = data.get("visual_media")
        if visual_media:
            emas = visual_media.get("expiring_media_action_summary")
            if emas and "timestamp" in emas:
                emas["timestamp"] = datetime.fromtimestamp(int(emas["timestamp"]) // 1_000_000)

        return ReplyMessage(**data)

    def fixed_extract_direct_message(data: dict[str, Any]) -> IGDirectMessage:
        """Fixed version that extracts action_log description to text field."""
        data["id"] = data.get("item_id")

        if "replied_to_message" in data:
            data["reply"] = fixed_extract_reply_message(data["replied_to_message"])
        if "media_share" in data:
            ms = data["media_share"]
            if not ms.get("code"):
                ms["code"] = InstagramIdCodec.encode(ms["id"])
            data["media_share"] = extract_media_v1(ms)
        if "media" in data:
            data["media"] = extract_direct_media(data["media"])
        if "voice_media" in data:
            if "media" in data["voice_media"]:
                data["media"] = extract_direct_media(data["voice_media"]["media"])

        clip = data.get("clip", {})
        if clip:
            if "clip" in clip:
                clip = clip.get("clip")
            data["clip"] = extract_media_v1(clip)

        xma_media_share = data.get("xma_media_share", {})
        if xma_media_share:
            data["xma_share"] = extract_media_v1_xma(xma_media_share[0])

        # Convert main timestamp
        data["timestamp"] = dt.datetime.fromtimestamp(int(data["timestamp"]) // 1_000_000)
        data["user_id"] = str(data.get("user_id", ""))
        data["client_context"] = data.get("client_context", "")

        # Convert reaction timestamps
        reactions = data.get("reactions", {})
        if reactions and "emojis" in reactions:
            for emoji_reaction in reactions["emojis"]:
                if "timestamp" in emoji_reaction:
                    emoji_reaction["timestamp"] = dt.datetime.fromtimestamp(
                        int(emoji_reaction["timestamp"]) // 1_000_000
                    )

        # Convert visual media timestamps
        visual_media = data.get("visual_media", {})
        if visual_media and "media" in visual_media:
            media = visual_media["media"]
            emas = media.get("expiring_media_action_summary")
            if emas and emas.get("timestamp"):
                emas["timestamp"] = dt.datetime.fromtimestamp(
                    int(emas["timestamp"]) // 1_000_000
                )
            # Convert image candidates URL expiration timestamps
            img_versions = media.get("image_versions2")
            if img_versions:
                for candidate in img_versions.get("candidates", []):
                    ts = candidate.get("url_expiration_timestamp_us")
                    if ts:
                        candidate["url_expiration_timestamp_us"] = dt.datetime.fromtimestamp(
                            int(ts) // 1_000_000
                        )
            # Convert video versions URL expiration timestamps
            for video_version in media.get("video_versions", []):
                ts = video_version.get("url_expiration_timestamp_us")
                if ts:
                    video_version["url_expiration_timestamp_us"] = dt.datetime.fromtimestamp(
                        int(ts) // 1_000_000
                    )

        # Convert top-level visual media expiring action summary timestamp
        if visual_media:
            emas = visual_media.get("expiring_media_action_summary")
            if emas and emas.get("timestamp"):
                emas["timestamp"] = dt.datetime.fromtimestamp(
                    int(emas["timestamp"]) // 1_000_000
                )

        # FIX: Extract action_log description to text field
        action_log = data.get("action_log")
        if action_log and isinstance(action_log, dict):
            description = action_log.get("description")
            if description and not data.get("text"):
                data["text"] = description

        return IGDirectMessage(**data)

    # Replace the broken extractors
    extractors.extract_reply_message = fixed_extract_reply_message
    extractors.extract_direct_message = fixed_extract_direct_message
    logger.debug("Replaced instagrapi extractors with fixed versions")


# Apply fix when module loads
_fix_instagrapi_extractors()


class InstagramClientError(Exception):
    """Base exception for Instagram client errors."""


class AuthenticationError(InstagramClientError):
    """Raised when authentication fails."""


class SessionError(InstagramClientError):
    """Raised when session operations fail."""


def _convert_user(user: IGUser) -> ThreadUser:
    """Convert instagrapi User/UserShort to our ThreadUser model.

    Args:
        user: Instagrapi User or UserShort object.

    Returns:
        ThreadUser: Converted user model.
    """
    return ThreadUser(
        user_id=str(user.pk),
        username=user.username,
        full_name=user.full_name or "",
        profile_pic_url=str(user.profile_pic_url) if user.profile_pic_url else None,
        is_verified=getattr(user, "is_verified", False) or False,
    )


def _determine_media_type(item: IGDirectMessage) -> MediaType:
    """Determine the media type of a direct message.

    Args:
        item: Instagrapi DirectMessage object.

    Returns:
        MediaType: The determined media type.
    """
    item_type = getattr(item, "item_type", "text")

    type_mapping = {
        "text": MediaType.TEXT,
        "media": MediaType.PHOTO,
        "video": MediaType.VIDEO,
        "voice_media": MediaType.VOICE,
        "link": MediaType.LINK,
        "media_share": MediaType.MEDIA_SHARE,
        "profile": MediaType.PROFILE,
        "clip": MediaType.REEL_SHARE,
        "reel_share": MediaType.REEL_SHARE,
        "story_share": MediaType.STORY_SHARE,
        "like": MediaType.LIKE,
        "action_log": MediaType.ACTION_LOG,
        "animated_media": MediaType.ANIMATED_MEDIA,
        "raven_media": MediaType.RAVEN_MEDIA,
        "placeholder": MediaType.PLACEHOLDER,
        "xma_share": MediaType.XMA,
    }

    return type_mapping.get(item_type, MediaType.UNKNOWN)


def _convert_message(
    msg: IGDirectMessage,
    thread_id: str,
    users_by_id: dict[str, ThreadUser] | None = None,
    last_seen_at: dict | None = None,
    viewer_id: str | None = None,
) -> DirectMessage:
    """Convert instagrapi DirectMessage to our DirectMessage model.

    Args:
        msg: Instagrapi DirectMessage object.
        thread_id: ID of the thread this message belongs to.
        users_by_id: Optional dict mapping user IDs to ThreadUser objects.
        last_seen_at: Optional dict mapping user IDs to LastSeenInfo objects.
        viewer_id: ID of the authenticated user (to exclude from seen calculation).

    Returns:
        DirectMessage: Converted message model.
    """
    media_type = _determine_media_type(msg)

    media_url = None
    if hasattr(msg, "media") and msg.media and hasattr(msg.media, "thumbnail_url"):
        media_url = str(msg.media.thumbnail_url)

    content = MessageContent(
        text=msg.text if msg.text else None,
        media_url=media_url,
        media_type=media_type,
    )

    # Look up user from thread's users, fall back to message's user info
    user_id = str(msg.user_id) if msg.user_id else "0"
    if users_by_id and user_id in users_by_id:
        sender = users_by_id[user_id]
        # Prefer full_name over username for display
        if sender.full_name:
            sender = ThreadUser(
                user_id=sender.user_id,
                username=sender.full_name,
                full_name=sender.full_name,
                profile_pic_url=sender.profile_pic_url,
                is_verified=sender.is_verified,
            )
    elif hasattr(msg, "user") and msg.user is not None:
        # Use user info from the message itself
        sender = _convert_user(msg.user)
    elif msg.is_sent_by_viewer:
        sender = ThreadUser(user_id=user_id, username="you")
    else:
        sender = ThreadUser(user_id=user_id, username="unknown")

    # Calculate seen_since for messages sent by viewer
    seen_since: int | None = None
    is_sent = msg.is_sent_by_viewer or False
    if is_sent and last_seen_at:
        msg_id = int(msg.id)
        # Use local time since instagrapi timestamps are naive local time
        now = datetime.now()

        # Check if any other user has seen this message
        for uid, seen_info in last_seen_at.items():
            if viewer_id and str(uid) == str(viewer_id):
                continue  # Skip viewer's own seen info
            if hasattr(seen_info, "item_id") and seen_info.item_id:
                their_last_seen_id = int(seen_info.item_id)
                if their_last_seen_id >= msg_id:
                    # They've seen this message
                    seen_time = seen_info.timestamp
                    if seen_time:
                        # Strip timezone if present to compare naive datetimes
                        if seen_time.tzinfo is not None:
                            seen_time = seen_time.replace(tzinfo=None)
                        delta = now - seen_time
                        seen_since = int(delta.total_seconds() / 60)
                    break

    return DirectMessage(
        message_id=str(msg.id),
        thread_id=thread_id,
        sender=sender,
        content=content,
        timestamp=msg.timestamp,
        is_sent_by_viewer=is_sent,
        seen_since=seen_since,
    )


def _convert_thread(
    thread: IGDirectThread,
    include_messages: bool = False,
    viewer_id: str | None = None,
) -> DirectThread:
    """Convert instagrapi DirectThread to our DirectThread model.

    Args:
        thread: Instagrapi DirectThread object.
        include_messages: Whether to include messages in the conversion.
        viewer_id: ID of the authenticated user (for seen calculation).

    Returns:
        DirectThread: Converted thread model.
    """
    users = [_convert_user(user) for user in thread.users] if thread.users else []

    # Build lookup dict for message sender resolution
    users_by_id = {u.user_id: u for u in users}

    # Get last_seen_at for seen status calculation
    last_seen_at = getattr(thread, "last_seen_at", None)

    messages: list[DirectMessage] = []
    if include_messages and thread.messages:
        messages = [
            _convert_message(msg, str(thread.id), users_by_id, last_seen_at, viewer_id)
            for msg in thread.messages
        ]

    return DirectThread(
        thread_id=str(thread.id),
        thread_title=thread.thread_title or "",
        users=users,
        last_activity_at=thread.last_activity_at,
        is_group=thread.is_group or False,
        is_muted=thread.muted or False,
        unread=bool(thread.read_state != 0) if hasattr(thread, "read_state") else False,
        message_count=len(thread.messages) if thread.messages else 0,
        messages=messages,
    )


class InstagramClient:
    """Wrapper around instagrapi Client with session management.

    This class provides a simplified interface to Instagram's Direct Message
    functionality with automatic session persistence.

    Attributes:
        client: The underlying instagrapi Client instance.
        session_file: Path to the session file for persistence.
    """

    def __init__(self, session_file: Path | None = None) -> None:
        """Initialize the Instagram client.

        Args:
            session_file: Path to store/load session data.
        """
        self.client = Client()
        # Override default challenge_code_handler which calls input() —
        # that would corrupt JSON-RPC on the MCP server's stdio transport.
        self.client.challenge_code_handler = self._challenge_code_handler
        self.session_file = session_file or Path(".instagram_session")
        self._logged_in = False

    @staticmethod
    def _challenge_code_handler(username: str, choice: Any = None) -> str:
        """No-op challenge handler that prevents stdin reads.

        The default instagrapi handler calls input() which blocks and corrupts
        the MCP server's JSON-RPC stdio transport. This raises immediately.
        """
        raise ChallengeRequired(
            f"Challenge required for {username} (method: {choice}). "
            "Run 'instagram-mcp-login' to resolve interactively."
        )

    def _retry_on_rate_limit(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute operation with exponential backoff on rate limit errors.

        Retries infinitely with delay doubling each attempt, capped at 30 seconds.
        Catches HTTP 467 (Instagram-specific) and 429 (standard) rate limit errors.
        """
        max_delay = 30.0
        attempt = 0
        while True:
            try:
                return operation(*args, **kwargs)
            except Exception as e:
                error_str = str(e)
                if "467" not in error_str and "429" not in error_str:
                    raise
                attempt += 1
                backoff = min(2 ** (attempt - 1), max_delay)
                logger.warning(
                    "Rate limited, retry in %.0fs (attempt %d): %s",
                    backoff,
                    attempt,
                    e,
                )
                time.sleep(backoff)

    @property
    def is_logged_in(self) -> bool:
        """Check if the client is logged in.

        Returns:
            bool: True if logged in, False otherwise.
        """
        return self._logged_in

    def load_session(self) -> bool:
        """Load session from file if it exists.

        Returns:
            bool: True if session was loaded successfully.

        Raises:
            SessionError: If session file exists but cannot be loaded.
        """
        if not self.session_file.exists():
            logger.debug("No session file found at %s", self.session_file)
            return False

        try:
            session_data = json.loads(self.session_file.read_text())
            self.client.set_settings(session_data)
            auth_data = session_data.get("authorization_data", {})
            session_id = auth_data.get("sessionid", "")
            self.client.login_by_sessionid(session_id)
            self._logged_in = True
            logger.info("Session loaded successfully")
            return True
        except (json.JSONDecodeError, KeyError) as e:
            raise SessionError(f"Invalid session file format: {e}") from e
        except LoginRequired as e:
            logger.warning("Session expired, need to re-login")
            raise SessionError("Session expired") from e
        except (ChallengeRequired, ClientUnauthorizedError) as e:
            logger.warning(
                "Session challenged/unauthorized by Instagram, deleting stale session"
            )
            self.session_file.unlink(missing_ok=True)
            raise SessionError(
                "Session challenged by Instagram. Stale session deleted. "
                "Resolve any 'Was this you?' prompts in the Instagram app, "
                "then run 'instagram-mcp-login' to re-authenticate."
            ) from e

    def save_session(self) -> None:
        """Save current session to file.

        Raises:
            SessionError: If session cannot be saved.
        """
        try:
            settings = self.client.get_settings()
            self.session_file.write_text(json.dumps(settings, indent=2, default=str))
            self.session_file.chmod(0o600)  # Secure permissions
            logger.info("Session saved to %s", self.session_file)
        except (OSError, TypeError) as e:
            raise SessionError(f"Failed to save session: {e}") from e

    def login(
        self,
        username: str,
        password: str,
        verification_code_handler: Any | None = None,
    ) -> None:
        """Login to Instagram with credentials.

        Args:
            username: Instagram username.
            password: Instagram password.
            verification_code_handler: Optional callback for 2FA code input.

        Raises:
            AuthenticationError: If login fails.
        """
        try:
            self.client.login(username, password)
            self._logged_in = True
            self.save_session()
            logger.info("Login successful for user %s", username)
        except BadPassword as e:
            raise AuthenticationError("Invalid password") from e
        except TwoFactorRequired as e:
            if verification_code_handler:
                code = verification_code_handler()
                try:
                    self.client.login(username, password, verification_code=code)
                    self._logged_in = True
                    self.save_session()
                except ChallengeRequired as ce:
                    # Auth succeeded but login_flow() got challenged (e.g. get_reels_tray_feed).
                    # Try saving the session anyway — the auth token may still be valid.
                    try:
                        self.save_session()
                        logger.warning(
                            "Challenge during login_flow() after 2FA — session saved, "
                            "but may need app confirmation"
                        )
                    except SessionError:
                        pass
                    raise AuthenticationError(
                        "Login succeeded but Instagram challenged a post-login request. "
                        "Check your Instagram app for 'Was this you?' prompts, approve it, "
                        "then try again. Session was saved and may work on next startup."
                    ) from ce
            else:
                raise AuthenticationError(
                    "2FA required. Run 'instagram-mcp-login' to authenticate interactively."
                ) from e
        except ChallengeRequired as e:
            raise AuthenticationError(
                f"Challenge required: {e}. Check your Instagram app for 'Was this you?' "
                "prompts, approve it, wait a minute, then try again."
            ) from e

    def login_or_load_session(self, username: str, password: str) -> None:
        """Try to load session, fall back to login if needed.

        Args:
            username: Instagram username (used if session load fails).
            password: Instagram password (used if session load fails).

        Raises:
            AuthenticationError: If both session load and login fail.
        """
        try:
            if self.load_session():
                return
        except SessionError:
            logger.debug("Session load failed, attempting login")

        self.login(username, password)

    # Thread operations
    def get_threads(self, amount: int = 20) -> list[DirectThread]:
        """Get direct message threads from inbox.

        Args:
            amount: Maximum number of threads to fetch.

        Returns:
            list[DirectThread]: List of thread models.
        """
        threads = self.client.direct_threads(amount=amount)
        return [_convert_thread(t) for t in threads]

    def get_thread(self, thread_id: str, amount: int = 20) -> DirectThread:
        """Get a specific thread with messages.

        Args:
            thread_id: ID of the thread to fetch.
            amount: Maximum number of messages to fetch.

        Returns:
            DirectThread: Thread model with messages.
        """
        thread = self._retry_on_rate_limit(
            self.client.direct_thread, thread_id=int(thread_id), amount=amount
        )
        viewer_id = str(self.client.user_id) if self.client.user_id else None
        return _convert_thread(thread, include_messages=True, viewer_id=viewer_id)

    def get_pending_threads(self) -> list[DirectThread]:
        """Get pending message request threads.

        Returns:
            list[DirectThread]: List of pending thread models.
        """
        threads = self.client.direct_pending_inbox()
        return [_convert_thread(t) for t in threads]

    def search_threads(self, query: str) -> list[DirectThread]:
        """Search threads by username or title.

        Fetches all threads and filters locally by username/title match.

        Args:
            query: Search query string (case-insensitive).

        Returns:
            list[DirectThread]: List of matching thread models.
        """
        query_lower = query.lower()
        all_threads = self.client.direct_threads(amount=50)
        matching = []
        for t in all_threads:
            # Check thread title
            if t.thread_title and query_lower in t.thread_title.lower():
                matching.append(_convert_thread(t))
                continue
            # Check usernames
            for user in t.users or []:
                if query_lower in user.username.lower():
                    matching.append(_convert_thread(t))
                    break
        return matching

    def hide_thread(self, thread_id: str) -> bool:
        """Hide/delete a thread.

        Args:
            thread_id: ID of the thread to hide.

        Returns:
            bool: True if successful.
        """
        return bool(self.client.direct_thread_hide(thread_id=int(thread_id)))

    def mark_thread_unread(self, thread_id: str) -> bool:
        """Mark a thread as unread.

        Args:
            thread_id: ID of the thread to mark.

        Returns:
            bool: True if successful.
        """
        return bool(self.client.direct_thread_mark_unread(thread_id=int(thread_id)))

    def mute_thread(self, thread_id: str) -> bool:
        """Mute notifications for a thread.

        Args:
            thread_id: ID of the thread to mute.

        Returns:
            bool: True if successful.
        """
        return bool(self.client.direct_thread_mute(thread_id=int(thread_id)))

    def unmute_thread(self, thread_id: str) -> bool:
        """Unmute notifications for a thread.

        Args:
            thread_id: ID of the thread to unmute.

        Returns:
            bool: True if successful.
        """
        return bool(self.client.direct_thread_unmute(thread_id=int(thread_id)))

    # Message operations
    def send_message(
        self,
        text: str,
        user_ids: list[str] | None = None,
        thread_ids: list[str] | None = None,
    ) -> DirectMessage | None:
        """Send a text message to users or threads.

        Args:
            text: Message text to send.
            user_ids: List of user IDs to send to (creates new threads).
            thread_ids: List of thread IDs to send to (existing threads).

        Returns:
            DirectMessage: The sent message, or None if failed.
        """
        user_ids_int = [int(uid) for uid in user_ids] if user_ids else None
        thread_ids_int = [int(tid) for tid in thread_ids] if thread_ids else None

        result = self.client.direct_send(
            text=text,
            user_ids=user_ids_int,
            thread_ids=thread_ids_int,
        )
        if result:
            tid = str(result.thread_id) if hasattr(result, "thread_id") else ""
            return _convert_message(result, tid)
        return None

    def reply_to_thread(self, thread_id: str, text: str) -> DirectMessage | None:
        """Reply to an existing thread.

        Args:
            thread_id: ID of the thread to reply to.
            text: Message text.

        Returns:
            DirectMessage: The sent message, or None if failed.
        """
        result = self.client.direct_answer(thread_id=int(thread_id), text=text)
        if result:
            return _convert_message(result, thread_id)
        return None

    def get_messages(self, thread_id: str, amount: int = 20) -> list[DirectMessage]:
        """Get messages from a thread.

        Args:
            thread_id: ID of the thread.
            amount: Maximum number of messages to fetch.

        Returns:
            list[DirectMessage]: List of message models.
        """
        # Fetch thread to get user info for sender lookup and seen status
        thread = self._retry_on_rate_limit(
            self.client.direct_thread, thread_id=int(thread_id), amount=amount
        )
        users = [_convert_user(user) for user in thread.users] if thread.users else []
        users_by_id = {u.user_id: u for u in users}
        last_seen_at = getattr(thread, "last_seen_at", None)
        viewer_id = str(self.client.user_id) if self.client.user_id else None

        messages = thread.messages or []
        return [
            _convert_message(msg, thread_id, users_by_id, last_seen_at, viewer_id)
            for msg in messages
        ]

    def delete_message(self, thread_id: str, message_id: str) -> bool:
        """Delete a message from a thread.

        Args:
            thread_id: ID of the thread.
            message_id: ID of the message to delete.

        Returns:
            bool: True if successful.
        """
        return bool(
            self.client.direct_message_delete(thread_id=int(thread_id), message_id=int(message_id))
        )

    # Media operations
    def send_photo(
        self,
        path: Path,
        user_ids: list[str] | None = None,
        thread_ids: list[str] | None = None,
    ) -> DirectMessage | None:
        """Send a photo to users or threads.

        Args:
            path: Path to the photo file.
            user_ids: List of user IDs to send to.
            thread_ids: List of thread IDs to send to.

        Returns:
            DirectMessage: The sent message, or None if failed.
        """
        user_ids_int = [int(uid) for uid in user_ids] if user_ids else None
        thread_ids_int = [int(tid) for tid in thread_ids] if thread_ids else None

        result = self.client.direct_send_photo(
            path=path,
            user_ids=user_ids_int,
            thread_ids=thread_ids_int,
        )
        if result:
            tid = str(result.thread_id) if hasattr(result, "thread_id") else ""
            return _convert_message(result, tid)
        return None

    def send_video(
        self,
        path: Path,
        user_ids: list[str] | None = None,
        thread_ids: list[str] | None = None,
    ) -> DirectMessage | None:
        """Send a video to users or threads.

        Args:
            path: Path to the video file.
            user_ids: List of user IDs to send to.
            thread_ids: List of thread IDs to send to.

        Returns:
            DirectMessage: The sent message, or None if failed.
        """
        user_ids_int = [int(uid) for uid in user_ids] if user_ids else None
        thread_ids_int = [int(tid) for tid in thread_ids] if thread_ids else None

        result = self.client.direct_send_video(
            path=path,
            user_ids=user_ids_int,
            thread_ids=thread_ids_int,
        )
        if result:
            tid = str(result.thread_id) if hasattr(result, "thread_id") else ""
            return _convert_message(result, tid)
        return None

    def share_media(
        self,
        media_id: str,
        user_ids: list[str] | None = None,
        thread_ids: list[str] | None = None,
    ) -> bool:
        """Share a media post to users or threads.

        Args:
            media_id: ID of the media to share.
            user_ids: List of user IDs to send to.
            thread_ids: List of thread IDs to send to.

        Returns:
            bool: True if successful.
        """
        user_ids_int = [int(uid) for uid in user_ids] if user_ids else []
        thread_ids_int = [int(tid) for tid in thread_ids] if thread_ids else None

        return bool(
            self.client.direct_media_share(
                media_id=media_id,
                user_ids=user_ids_int,
                thread_ids=thread_ids_int,
            )
        )

    def share_profile(
        self,
        user_id: str,
        target_user_ids: list[str] | None = None,
        thread_ids: list[str] | None = None,
    ) -> bool:
        """Share a user profile to users or threads.

        Args:
            user_id: ID of the user profile to share.
            target_user_ids: List of user IDs to send to.
            thread_ids: List of thread IDs to send to.

        Returns:
            bool: True if successful.
        """
        target_ids_int = [int(uid) for uid in target_user_ids] if target_user_ids else []
        thread_ids_int = [int(tid) for tid in thread_ids] if thread_ids else None

        return bool(
            self.client.direct_profile_share(
                user_id=int(user_id),
                user_ids=target_ids_int,
                thread_ids=thread_ids_int,
            )
        )


def interactive_login() -> None:
    """Interactive login command for initial authentication.

    This function handles 2FA and challenge codes interactively via
    stdin/stdout and saves the session for later use by the MCP server.
    """
    from instagram_mcp.config import get_settings, setup_logging

    settings = get_settings()
    setup_logging(settings.log_level)

    client = InstagramClient(session_file=settings.instagram_session_file)

    # Delete stale session to avoid ChallengeRequired from old session data
    if settings.instagram_session_file.exists():
        print(
            f"Removing old session file: {settings.instagram_session_file}",
            file=sys.stderr,
        )
        settings.instagram_session_file.unlink()

    print("Instagram MCP - Interactive Login", file=sys.stderr)
    print("=" * 40, file=sys.stderr)

    def get_2fa_code() -> str:
        """Prompt for 2FA code via stdin."""
        print("2FA code required. Enter code: ", end="", file=sys.stderr)
        sys.stderr.flush()
        return input().strip()

    def get_challenge_code(username: str, choice: Any = None) -> str:
        """Prompt for Instagram challenge code via stdin."""
        print(
            f"\nChallenge required for {username}!",
            file=sys.stderr,
        )
        print(
            f"Instagram sent a security code via {choice or 'email/SMS'}.",
            file=sys.stderr,
        )
        print("Enter challenge code: ", end="", file=sys.stderr)
        sys.stderr.flush()
        return input().strip()

    # Override challenge handler for interactive use
    client.client.challenge_code_handler = get_challenge_code

    try:
        client.login(
            username=settings.instagram_username,
            password=settings.instagram_password.get_secret_value(),
            verification_code_handler=get_2fa_code,
        )
        print(
            f"\nLogin successful! Session saved to {settings.instagram_session_file}",
            file=sys.stderr,
        )
    except AuthenticationError as e:
        print(f"\nLogin failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    interactive_login()
