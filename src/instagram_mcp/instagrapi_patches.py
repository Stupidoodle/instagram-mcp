"""Small fixes on top of instagrapi's extractors, applied once at import.

Each patch wraps instagrapi's own function and only adds what it still lacks, so
upstream fixes keep reaching us with every upgrade. The canary tests in
``tests/unit/test_instagrapi_patches.py`` call the unpatched function and fail once
instagrapi handles the case itself; that is the signal to delete the patch.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

from instagrapi import extractors
from instagrapi.mixins import direct as direct_mixin
from instagrapi.mixins import media as media_mixin
from instagrapi.mixins import user as user_mixin

if TYPE_CHECKING:
    from collections.abc import Callable

# Profile fields Instagram leaves out of some user payloads but instagrapi's User requires.
_USER_DEFAULTS: dict[str, Any] = {
    "is_private": False,
    "is_verified": False,
    "media_count": 0,
    "follower_count": 0,
    "following_count": 0,
    "is_business": False,
}


def _with_action_log_text(extract: Callable[[dict[str, Any]], Any]) -> Callable[..., Any]:
    """A notice ("liked a message", "named the group") carries its words in action_log."""

    @functools.wraps(extract)
    def extract_direct_message(data: dict[str, Any]) -> Any:
        action_log = data.get("action_log")
        if isinstance(action_log, dict) and action_log.get("description") and not data.get("text"):
            data["text"] = action_log["description"]
        return extract(data)

    return extract_direct_message


def _with_user_defaults(extract: Callable[[dict[str, Any]], Any]) -> Callable[..., Any]:
    """Fill the profile fields Instagram omitted, and drop a null pinned-channels block."""

    @functools.wraps(extract)
    def extract_user_v1(data: dict[str, Any]) -> Any:
        if not isinstance(data.get("pinned_channels_info"), dict):
            data.pop("pinned_channels_info", None)
        data.setdefault("full_name", data.get("username", ""))
        for field, default in _USER_DEFAULTS.items():
            data.setdefault(field, default)
        return extract(data)

    return extract_user_v1


def apply() -> None:
    """Install the patches (idempotent), including where instagrapi imported them by name."""
    if getattr(extractors.extract_direct_message, "__wrapped__", None) is None:
        message = _with_action_log_text(extractors.extract_direct_message)
        for module in (extractors, direct_mixin, media_mixin):
            module.extract_direct_message = message
    if getattr(extractors.extract_user_v1, "__wrapped__", None) is None:
        user = _with_user_defaults(extractors.extract_user_v1)
        for module in (extractors, user_mixin):
            module.extract_user_v1 = user
