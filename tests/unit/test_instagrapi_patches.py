"""The instagrapi patches, and canaries that fail once instagrapi fixes the case itself.

When a canary fails, instagrapi handles it now: delete that patch and its canary.
"""

from typing import Any

import pytest
from instagrapi import extractors
from instagrapi.mixins import direct as direct_mixin
from instagrapi.mixins import media as media_mixin
from instagrapi.mixins import user as user_mixin
from pydantic import ValidationError

from instagram_mcp import instagrapi_patches

instagrapi_patches.apply()

TS = 1_790_000_000_000_000


def _notice() -> dict[str, Any]:
    return {
        "item_id": "1",
        "item_type": "action_log",
        "timestamp": TS,
        "user_id": 1,
        "action_log": {"description": "Liked a message"},
    }


def _sparse_user(**extra: Any) -> dict[str, Any]:
    return {"pk": "1", "username": "someone", "profile_pic_url": "https://x.test/p.jpg", **extra}


def test_patches_are_installed_everywhere_instagrapi_imported_them() -> None:
    for module in (extractors, direct_mixin, media_mixin):
        assert module.extract_direct_message is extractors.extract_direct_message
    assert user_mixin.extract_user_v1 is extractors.extract_user_v1
    assert extractors.extract_direct_message.__wrapped__ is not None


def test_apply_is_idempotent() -> None:
    before = extractors.extract_direct_message
    instagrapi_patches.apply()
    assert extractors.extract_direct_message is before


def test_a_notice_gets_its_words_as_text() -> None:
    assert extractors.extract_direct_message(_notice()).text == "Liked a message"


def test_a_sparse_user_gets_defaults() -> None:
    user = extractors.extract_user_v1(_sparse_user(pinned_channels_info=None))
    assert (user.full_name, user.follower_count, user.broadcast_channel) == ("someone", 0, [])


# ── canaries: instagrapi still lacks these ──────────────────────────────────


def test_canary_instagrapi_drops_notice_text() -> None:
    assert extractors.extract_direct_message.__wrapped__(_notice()).text is None


def test_canary_instagrapi_requires_every_profile_field() -> None:
    with pytest.raises(ValidationError):
        extractors.extract_user_v1.__wrapped__(_sparse_user())


def test_canary_instagrapi_trips_over_null_pinned_channels() -> None:
    full = extractors.extract_user_v1(_sparse_user()).model_dump()
    with pytest.raises(AttributeError):
        extractors.extract_user_v1.__wrapped__(full | {"pinned_channels_info": None})


# ── handled by instagrapi itself, so no patch: fails if it regresses ─────────


def test_instagrapi_skips_incomplete_share_cards() -> None:
    item = {
        "item_id": "2",
        "item_type": "generic_xma",
        "timestamp": TS,
        "user_id": 1,
        "generic_xma": [{"title_text": "no target"}],
    }
    assert extractors.extract_direct_message(item).generic_xma == []


def test_instagrapi_converts_reply_view_once_timestamps() -> None:
    summary = {"timestamp": TS, "type": "raven_opened", "count": 1}
    replied = {
        "item_id": "3",
        "item_type": "raven_media",
        "timestamp": TS,
        "user_id": 1,
        "visual_media": {
            "media": {"media_type": 1},
            "view_mode": "once",
            "expiring_media_action_summary": summary,
        },
    }
    item = {"item_id": "4", "item_type": "text", "timestamp": TS, "user_id": 1}
    message = extractors.extract_direct_message(item | {"replied_to_message": replied})
    assert message.reply.visual_media.expiring_media_action_summary.timestamp.year == 2026
