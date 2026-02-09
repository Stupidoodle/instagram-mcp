"""Pydantic schemas for Instagram Direct Message data structures.

This module defines the data models used throughout the MCP server for
representing Instagram threads, messages, and users.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class MediaType(str, Enum):
    """Types of media that can be sent in direct messages."""

    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    VOICE = "voice"
    LINK = "link"
    MEDIA_SHARE = "media_share"
    PROFILE = "profile"
    REEL_SHARE = "reel_share"
    STORY_SHARE = "story_share"
    LIKE = "like"  # Message reaction/like
    ACTION_LOG = "action_log"  # Thread activity (naming, adding users)
    ANIMATED_MEDIA = "animated_media"  # GIF/sticker
    RAVEN_MEDIA = "raven_media"  # Disappearing photo/video
    PLACEHOLDER = "placeholder"  # Deleted message
    XMA = "xma"  # Cross-media attachment (external links with previews)
    UNKNOWN = "unknown"


class ThreadUser(BaseModel):
    """Represents a user in a direct message thread.

    Attributes:
        user_id: Instagram user ID.
        username: Instagram username.
        full_name: User's display name.
        profile_pic_url: URL to user's profile picture.
        is_verified: Whether the user is verified.
    """

    user_id: str = Field(..., description="Instagram user ID")
    username: str = Field(..., description="Instagram username")
    full_name: str = Field(default="", description="User's display name")
    profile_pic_url: str | None = Field(default=None, description="URL to profile picture")
    is_verified: bool = Field(default=False, description="Whether user is verified")


class MessageContent(BaseModel):
    """Content of a direct message.

    Attributes:
        text: Text content of the message (if any).
        media_url: URL to media content (if any).
        media_type: Type of media content.
    """

    text: str | None = Field(default=None, description="Text content")
    media_url: str | None = Field(default=None, description="Media URL")
    media_type: MediaType = Field(default=MediaType.TEXT, description="Type of media")


class DirectMessage(BaseModel):
    """Represents a single direct message.

    Attributes:
        message_id: Unique message identifier.
        thread_id: ID of the thread this message belongs to.
        sender: User who sent the message.
        content: Message content.
        timestamp: When the message was sent.
        is_sent_by_viewer: Whether the authenticated user sent this message.
    """

    message_id: str = Field(..., description="Unique message ID")
    thread_id: str = Field(..., description="Thread ID")
    sender: ThreadUser = Field(..., description="Message sender")
    content: MessageContent = Field(..., description="Message content")
    timestamp: datetime = Field(..., description="Message timestamp")
    is_sent_by_viewer: bool = Field(default=False, description="Whether sent by authenticated user")
    seen_since: int | None = Field(
        default=None,
        description="Minutes since recipient saw this message (null if not seen yet)",
    )


class DirectThread(BaseModel):
    """Represents a direct message thread (conversation).

    Attributes:
        thread_id: Unique thread identifier.
        thread_title: Thread title (usually other user's name).
        users: List of users in the thread.
        last_activity_at: When the thread was last active.
        is_group: Whether this is a group thread.
        is_muted: Whether notifications are muted.
        unread: Whether there are unread messages.
        message_count: Number of messages in thread.
        messages: List of messages (when fetched with messages).
    """

    thread_id: str = Field(..., description="Unique thread ID")
    thread_title: str = Field(default="", description="Thread title")
    users: list[ThreadUser] = Field(default_factory=list, description="Thread users")
    last_activity_at: datetime | None = Field(default=None, description="Last activity timestamp")
    is_group: bool = Field(default=False, description="Whether this is a group thread")
    is_muted: bool = Field(default=False, description="Whether thread is muted")
    unread: bool = Field(default=False, description="Whether there are unread messages")
    message_count: int = Field(default=0, description="Number of messages")
    messages: list[DirectMessage] = Field(default_factory=list, description="Messages in thread")
