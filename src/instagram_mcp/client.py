"""Instagram client wrapper with session management.

This module provides a wrapper around instagrapi's Client class with
session persistence and proper error handling for MCP server usage.
"""

import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2
from instagrapi import Client
from instagrapi import config as ig_config
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    ClientUnauthorizedError,
    LoginRequired,
    TwoFactorRequired,
)

from instagram_mcp import instagrapi_patches
from instagram_mcp.links import unwrap_link
from instagram_mcp.models.schemas import (
    DirectMessage,
    DirectThread,
    MediaType,
    MessageContent,
    Reaction,
    ThreadUser,
)
from instagram_mcp.raven import ViewMode, send_disappearing
from instagram_mcp.shares import share_from_message

if TYPE_CHECKING:
    from instagrapi.types import DirectMessage as IGDirectMessage
    from instagrapi.types import DirectThread as IGDirectThread
    from instagrapi.types import User as IGUser

logger = logging.getLogger("instagram_mcp")

instagrapi_patches.apply()


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
        "xma_clip": MediaType.REEL_SHARE,
        "felix_share": MediaType.REEL_SHARE,
        "xma_media_share": MediaType.MEDIA_SHARE,
        "xma_story_share": MediaType.STORY_SHARE,
        "xma_profile": MediaType.PROFILE,
        "generic_xma": MediaType.XMA,
    }

    return type_mapping.get(item_type, MediaType.UNKNOWN)


def _message_content(msg: IGDirectMessage) -> MessageContent:
    """What a message says or shows: text, media, a link or a share."""
    media_url = None
    if hasattr(msg, "media") and msg.media and hasattr(msg.media, "thumbnail_url"):
        media_url = str(msg.media.thumbnail_url)

    text = msg.text if msg.text else None
    link_url, link_title = None, None
    link = getattr(msg, "link", None)
    if link is not None:
        # A link message keeps its text and preview under `link`.
        text = text or link.text or None
        context = link.link_context
        if context is not None and context.link_url:
            link_url = unwrap_link(str(context.link_url))
            link_title = context.link_title or None

    share = share_from_message(msg)
    return MessageContent(
        text=text,
        media_url=media_url or (share.preview_url if share else None),
        media_type=_determine_media_type(msg),
        link_url=link_url,
        link_title=link_title,
        share=share,
    )


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
    content = _message_content(msg)

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
        reactions=_convert_reactions(getattr(msg, "reactions", None)),
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


# Instagram checks these together; only triples instagrapi knows are consistent.
APP_PROFILE_KEYS = ("app_version", "version_code", "bloks_versioning_id")


def resolve_app_version(pinned: str | None = None) -> str:
    """The app version to emulate: a pinned one, or instagrapi's newest known version.

    Args:
        pinned: A version to pin. Must be one instagrapi knows, since Instagram checks
            app_version, version_code and bloks_versioning_id together.

    Raises:
        ValueError: If the pinned version is unknown to instagrapi.
    """
    version = pinned or ig_config.DEFAULT_APP_VERSION
    if version not in ig_config.APP_SETTINGS:
        known = ", ".join(ig_config.APP_SETTINGS)
        msg = f"Unknown Instagram app version {version!r}; instagrapi knows: {known}"
        raise ValueError(msg)
    return version


def _version_tuple(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return ()


class InstagramClient:
    """Wrapper around instagrapi Client with session management.

    This class provides a simplified interface to Instagram's Direct Message
    functionality with automatic session persistence.

    Attributes:
        client: The underlying instagrapi Client instance.
        session_file: Path to the session file for persistence.
    """

    def __init__(
        self,
        session_file: Path | None = None,
        app_version: str | None = None,
    ) -> None:
        """Initialize the Instagram client.

        Args:
            session_file: Path to store/load session data.
            app_version: Pin an app version instagrapi knows. Default: its newest.
                Fresh clients and loaded sessions both run it (see _apply_app_version).
        """
        self.client = Client()
        # Override default challenge_code_handler which calls input() —
        # that would corrupt JSON-RPC on the MCP server's stdio transport.
        self.client.challenge_code_handler = self._challenge_code_handler
        self.session_file = session_file or Path(".instagram_session")
        self._logged_in = False
        self._app_version = resolve_app_version(app_version)
        self._app_version_pinned = app_version is not None
        # Patch instagrapi's requests Session with a default 30s timeout.
        # Without this, HTTP calls can block forever if Instagram stalls
        # the connection (silent rate limit after rapid-fire calls).
        self._patch_request_timeout(30)
        self._apply_app_version()
        self.PERMANENT_MEDIA = True

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

    def _patch_request_timeout(self, timeout: int) -> None:
        """Patch the instagrapi HTTP session with a default socket timeout.

        instagrapi's ``private.post()``/``private.get()`` calls never pass a
        ``timeout`` to the underlying ``requests`` library, so they default to
        ``None`` (wait forever).  After rapid-fire calls Instagram may stall the
        TCP connection instead of returning 429, causing a permanent hang.

        This patches ``Session.request`` so every HTTP call has a ceiling.
        """
        session = self.client.private
        original_request = session.request

        def _request_with_timeout(*args: Any, **kwargs: Any) -> Any:
            kwargs.setdefault("timeout", timeout)
            return original_request(*args, **kwargs)

        session.request = _request_with_timeout  # type: ignore[assignment]

    def _apply_app_version(self) -> bool:
        """Run the target app version, the way the real app updates itself.

        Applies instagrapi's whole known profile (app_version, version_code,
        bloks_versioning_id) and rebuilds the User-Agent; the device identity stays.
        An unpinned client never downgrades a session that is already newer.

        Returns:
            bool: True if the version changed.
        """
        device = self.client.device_settings
        target = ig_config.APP_SETTINGS[self._app_version]
        if all(device.get(key) == target[key] for key in APP_PROFILE_KEYS):
            return False
        current = str(device.get("app_version", ""))
        if not self._app_version_pinned and _version_tuple(current) > _version_tuple(
            self._app_version
        ):
            return False
        self.client.set_app(self._app_version)
        self.client.set_user_agent()
        logger.info("Instagram app version %s -> %s", current or "(none)", self._app_version)
        return True

    def _retry_on_rate_limit(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute operation with exponential backoff on rate limit errors.

        Retries up to 5 times with delay doubling each attempt, capped at 30s.
        Catches HTTP 467 (Instagram-specific), 429 (standard), and request
        timeouts (from the 30s socket ceiling).  After max retries, raises.
        """
        max_delay = 30.0
        max_retries = 5
        attempt = 0
        while True:
            try:
                return operation(*args, **kwargs)
            except Exception as e:
                error_str = str(e)
                is_rate_limit = "467" in error_str or "429" in error_str
                is_timeout = "timeout" in error_str.lower() or "timed out" in error_str.lower()
                if not is_rate_limit and not is_timeout:
                    raise
                attempt += 1
                if attempt > max_retries:
                    logger.error(
                        "Max retries (%d) exceeded: %s",
                        max_retries,
                        e,
                    )
                    raise
                backoff = min(2 ** (attempt - 1), max_delay)
                logger.warning(
                    "Rate limited/timeout, retry in %.0fs (attempt %d/%d): %s",
                    backoff,
                    attempt,
                    max_retries,
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
            upgraded = self._apply_app_version()
            auth_data = session_data.get("authorization_data", {})
            session_id = auth_data.get("sessionid", "")
            self.client.login_by_sessionid(session_id)
            self._logged_in = True
            if upgraded:
                self.save_session()
            logger.info("Session loaded successfully")
            return True
        except (json.JSONDecodeError, KeyError) as e:
            raise SessionError(f"Invalid session file format: {e}") from e
        except LoginRequired as e:
            logger.warning("Session expired, need to re-login")
            raise SessionError("Session expired") from e
        except (ChallengeRequired, ClientUnauthorizedError) as e:
            logger.warning("Session challenged/unauthorized by Instagram, deleting stale session")
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

    def get_seq_id(self) -> int:
        """Get the Iris sequence ID for MQTT subscription.

        Returns:
            The current seq_id from Instagram's direct_v2/inbox/ endpoint.
        """
        result = self._retry_on_rate_limit(
            self.client.private_request, "direct_v2/inbox/", params={"limit": "1"}
        )
        return int(result.get("seq_id", 0))

    def get_iris_info(self) -> dict:
        """Get Iris subscription info from the inbox endpoint.

        Returns:
            Dict with 'seq_id', 'snapshot_at_ms', and 'app_version'.
        """
        result = self._retry_on_rate_limit(
            self.client.private_request, "direct_v2/inbox/", params={"limit": "1"}
        )
        return {
            "seq_id": int(result.get("seq_id", 0)),
            "snapshot_at_ms": int(result.get("snapshot_at_ms", 0)),
            "app_version": self.client.device_settings.get("app_version", self._app_version),
        }

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

    def react(self, thread_id: str, message_id: str, emoji: str, *, remove: bool = False) -> bool:
        """Add or remove the viewer's emoji reaction on a message.

        Args:
            thread_id: ID of the thread.
            message_id: ID of the message to react to.
            emoji: The emoji (for a removal: the one being removed).
            remove: Remove the reaction instead of adding it.

        Returns:
            bool: True if successful.
        """
        action = self.client.direct_delete_reaction if remove else self.client.direct_send_reaction
        return bool(self._retry_on_rate_limit(action, int(thread_id), int(message_id), emoji=emoji))

    def mark_seen(self, thread_id: str, message_id: str) -> bool:
        """Send a read receipt up to a message.

        Args:
            thread_id: ID of the thread.
            message_id: ID of the newest message being marked seen.

        Returns:
            bool: True if successful.
        """
        return bool(
            self._retry_on_rate_limit(
                self.client.direct_message_seen, int(thread_id), int(message_id)
            )
        )

    def download_message_media(
        self,
        thread_id: str,
        message_id: str,
        folder: Path,
        ephemeral_folder: Path | None = None,
    ) -> Path:
        """Download the photo, video or voice clip of a message.

        Disappearing photos: "keep in chat" ones are saved like any photo. View-once and
        replayable ones are only saved into ``ephemeral_folder`` (owner-only, swept by
        the caller); without one they are refused.

        Args:
            thread_id: ID of the thread.
            message_id: ID of the message with the media.
            folder: Where to save the file.
            ephemeral_folder: Where view-once/replayable media may go, temporarily.

        Returns:
            Path: The downloaded file.

        Raises:
            InstagramClientError: If the message isn't found, is view-once without an
                ephemeral folder, or has no media.
        """
        thread = self._retry_on_rate_limit(
            self.client.direct_thread, thread_id=int(thread_id), amount=50
        )
        item = next((m for m in thread.messages or [] if str(m.id) == message_id), None)
        if item is None:
            msg = f"message {message_id} not in the latest 50 of this thread"
            raise InstagramClientError(msg)
        ephemeral = False
        if item.item_type == "raven_media":
            visual = item.visual_media
            if (
                getattr(visual, "view_mode", None) != "permanent"
                and not self.PERMANENT_MEDIA
            ):
                if ephemeral_folder is None:
                    msg = "view-once media is only downloaded into a temporary folder"
                    raise InstagramClientError(msg)
                folder, ephemeral = ephemeral_folder, True
            url = _visual_media_url(visual)
        else:
            media = item.media
            url = media and (media.video_url or media.audio_url or media.thumbnail_url)
        if not url:
            msg = f"message {message_id} ({item.item_type}) has no downloadable media"
            raise InstagramClientError(msg)
        response = httpx2.get(str(url), timeout=60, follow_redirects=True)
        response.raise_for_status()
        suffix = _suffix_for(response.headers.get("content-type", ""), str(url))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{thread_id[-6:]}-{message_id}{suffix}"
        path.write_bytes(response.content)
        if ephemeral:
            folder.chmod(0o700)
            path.chmod(0o600)
        return path

    def send_voice(self, path: Path, thread_id: str) -> DirectMessage | None:
        """Send an audio file as a voice message.

        Instagram only accepts AAC in an MP4 container, so anything else is
        converted with ffmpeg first.

        Args:
            path: Path to the audio file.
            thread_id: ID of the thread.

        Returns:
            DirectMessage: The sent message, or None if failed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            clip = path if path.suffix.lower() == ".m4a" else _to_m4a(path, Path(tmp))
            result = self.client.direct_send_voice(path=clip, thread_ids=[int(thread_id)])
        if result:
            return _convert_message(result, thread_id)
        return None

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

    def send_disappearing(self, path: Path, thread_id: str, view_mode: ViewMode) -> str | None:
        """Send a photo or video as view once or allow replay.

        Args:
            path: The photo or mp4 video.
            thread_id: The thread to send it to.
            view_mode: ``"once"`` or ``"replayable"``.

        Returns:
            The new message's id, when Instagram returns one.
        """
        result = send_disappearing(self.client, thread_id, path, view_mode)
        payload = result.get("payload")
        item_id = payload.get("item_id") if isinstance(payload, dict) else None
        return str(item_id) if item_id else None

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


_SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "video/mp4": ".mp4",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
}


def _suffix_for(content_type: str, url: str) -> str:
    """File suffix from the response type, else from the URL path."""
    known = _SUFFIXES.get(content_type.split(";", 1)[0].strip())
    if known:
        return known
    return Path(httpx2.URL(url).path).suffix or ".bin"


def _convert_reactions(reactions: Any) -> list[Reaction]:
    """Emoji reactions on a message; a plain double-tap like counts as ❤️."""
    if reactions is None:
        return []
    found = [Reaction(user_id=str(r.sender_id), emoji=r.emoji) for r in reactions.emojis or []]
    for like in reactions.likes or []:
        sender = like.get("sender_id") if isinstance(like, dict) else None
        if sender is not None:
            found.append(Reaction(user_id=str(sender), emoji="❤️"))
    return found


def _visual_media_url(visual: Any) -> str | None:
    """Best URL of a disappearing photo or video: the first video version, else image."""
    content = getattr(visual, "media", None)
    if content is None:
        return None
    if content.video_versions:
        return str(content.video_versions[0].url)
    images = content.image_versions2
    if images and images.candidates:
        return str(images.candidates[0].url)
    return None


def _to_m4a(source: Path, folder: Path) -> Path:
    """Convert an audio file to AAC in an MP4 container with ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        msg = "ffmpeg is needed to convert voice messages to .m4a"
        raise InstagramClientError(msg)
    target = folder / f"{source.stem}.m4a"
    argv = [ffmpeg, "-y", "-loglevel", "error", "-i", str(source)]
    argv += ["-c:a", "aac", "-b:a", "64k", str(target)]
    subprocess.run(argv, check=True, capture_output=True)  # noqa: S603 - fixed argv, file paths

    return target


def interactive_login() -> None:
    """Interactive login command for initial authentication.

    This function handles 2FA and challenge codes interactively via
    stdin/stdout and saves the session for later use by the MCP server.
    """
    from instagram_mcp.config import get_settings, setup_logging

    settings = get_settings()
    setup_logging(settings.log_level)

    client = InstagramClient(
        session_file=settings.instagram_session_file,
        app_version=settings.instagram_app_version,
    )

    # Delete stale session to avoid ChallengeRequired from old session data
    if settings.instagram_session_file.exists():
        print(
            f"Removing old session file: {settings.instagram_session_file}",
            file=sys.stderr,
        )
        settings.instagram_session_file.unlink()

    print("Instagram MCP - Interactive Login", file=sys.stderr)
    print(f"App version: {client.client.device_settings['app_version']}", file=sys.stderr)
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
