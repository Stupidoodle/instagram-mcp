"""Shared reels, posts, stories and profiles."""

import json
from types import SimpleNamespace
from typing import Any

from instagram_mcp.shares import Share, describe, share_from_item, share_from_message

REEL_URL = "https://www.instagram.com/reel/ABC123/"


def _reel_card(**extra: Any) -> dict[str, Any]:
    """An xma_clip card as Instagram sends it (2026-09), trimmed to what we read."""
    ref = {
        "action_type": "SHARE",
        "content_type": "CLIP",
        "fetch_params": {
            "media_igid": "111",
            "__typename": "XMSGIgReceiverFetchXmaClipFetchParams",
        },
        "target_url": REEL_URL,
        "username": "someone.cooks",
    }
    return {
        "header_title_text": "someone.cooks",
        "title_text": None,
        "caption_body_text": None,
        "target_url": REEL_URL + "?id=111_222&is_sponsored=false",
        "preview_url_info": {"url": "https://cdn.test/cover.jpg", "width": 480, "height": 853},
        "serialized_content_ref": json.dumps(ref),
        **extra,
    }


def test_a_shared_reel() -> None:
    share = share_from_item({"item_type": "xma_clip", "xma_clip": [_reel_card()]})
    assert share == Share(
        kind="reel",
        url=REEL_URL,
        author="someone.cooks",
        media_id="111",
        preview_url="https://cdn.test/cover.jpg",
    )


def test_a_card_without_content_ref_falls_back_to_its_own_fields() -> None:
    card = _reel_card(serialized_content_ref="not json", caption_body_text="so good")
    item = {"item_type": "xma_clip", "xma_clip": [card], "original_media_igid": "999"}
    share = share_from_item(item)
    assert share is not None
    assert (share.url, share.author, share.caption, share.media_id) == (
        REEL_URL,
        "someone.cooks",
        "so good",
        "999",
    )


def test_post_story_profile_and_link_cards() -> None:
    card = {"target_url": "https://example.com/a?b=c", "title_text": "A page", "preview_url": "p"}
    kinds = {
        "xma_media_share": "post",
        "xma_story_share": "story",
        "xma_profile": "profile",
        "generic_xma": "card",
    }
    for item_type, kind in kinds.items():
        share = share_from_item({"item_type": item_type, item_type: [card]})
        assert share == Share(
            kind=kind, url="https://example.com/a?b=c", caption="A page", preview_url="p"
        )


def _media(**extra: Any) -> dict[str, Any]:
    return {
        "pk": "555",
        "id": "555_1",
        "code": "XYZ",
        "user": {"username": "poster"},
        "caption": {"text": "hello"},
        "image_versions2": {"candidates": [{"url": "https://cdn.test/img.jpg"}]},
        **extra,
    }


def test_older_shares_carry_the_media_itself() -> None:
    clip = share_from_item({"item_type": "clip", "clip": {"clip": _media()}})
    post = share_from_item({"item_type": "media_share", "media_share": _media()})
    story = share_from_item({"item_type": "story_share", "story_share": {"media": _media()}})
    assert clip == Share(
        "reel",
        REEL_URL.replace("ABC123", "XYZ"),
        "poster",
        "hello",
        "555",
        "https://cdn.test/img.jpg",
    )
    assert post is not None and post.url == "https://www.instagram.com/p/XYZ/"
    assert story is not None and (story.kind, story.url, story.author) == ("story", None, "poster")


def test_not_a_share() -> None:
    assert share_from_item({"item_type": "text", "text": "hi"}) is None
    assert share_from_item({"item_type": "xma_profile", "xma_profile": [{}]}) == Share("profile")
    assert share_from_item({"item_type": "xma_clip", "xma_clip": []}) is None
    assert share_from_item({"item_type": "media_share", "media_share": None}) is None


def test_history_messages_use_the_raw_card_or_the_parsed_media() -> None:
    xma = SimpleNamespace(item_type="xma_clip", raw_xma={"xma_clip": [_reel_card()]})
    media = SimpleNamespace(
        pk="7",
        code="QQ",
        user=SimpleNamespace(username="poster"),
        caption_text="",
        thumbnail_url="https://cdn.test/t.jpg",
    )
    clip = SimpleNamespace(item_type="clip", raw_xma=None, clip=media)
    story = SimpleNamespace(item_type="story_share", raw_xma=None, story_share={"media": _media()})
    text = SimpleNamespace(item_type="text", raw_xma=None)
    assert share_from_message(xma) == share_from_item(
        {"item_type": "xma_clip", "xma_clip": [_reel_card()]}
    )
    assert share_from_message(clip) == Share(
        "reel", "https://www.instagram.com/reel/QQ/", "poster", None, "7", "https://cdn.test/t.jpg"
    )
    assert share_from_message(story) is not None
    assert share_from_message(text) is None


def test_dict_round_trip() -> None:
    share = Share(kind="reel", url=REEL_URL, author="someone.cooks")
    assert share.to_dict() == {"kind": "reel", "url": REEL_URL, "author": "someone.cooks"}
    assert Share.from_dict(share.to_dict()) == share
    assert Share.from_dict({"kind": "banana"}) is None
    assert Share.from_dict(None) is None


def test_describe() -> None:
    reel = Share(kind="reel", url=REEL_URL, author="someone.cooks", caption="line one\nline  two")
    assert describe(reel) == f"[reel by @someone.cooks: line one line two — {REEL_URL}]"
    assert describe(Share(kind="profile", author="x")) == "[profile @x]"
    assert describe(Share(kind="card")) == "[card]"
    long = describe(Share(kind="post", caption="a" * 400), limit=10)
    assert long == "[post: aaaaaaaaa…]"
