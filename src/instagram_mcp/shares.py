"""Reels, posts, stories and profiles shared into a chat.

Instagram sends a share as an XMA card (``xma_clip``, ``xma_media_share``,
``xma_story_share``, ``xma_profile``, ``generic_xma``) or, in older threads, as the
media itself (``clip``, ``media_share``, ``story_share``). Both become one Share: what
it is, whose it is, its caption and where it lives, so a persona can talk about it
without opening Instagram. The media id resolves the caption and the video later.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

type ShareKind = Literal["reel", "post", "story", "profile", "card"]

_XMA_KINDS: dict[str, ShareKind] = {
    "xma_clip": "reel",
    "xma_media_share": "post",
    "xma_story_share": "story",
    "xma_profile": "profile",
    "generic_xma": "card",
}
_MEDIA_KINDS: dict[str, ShareKind] = {
    "clip": "reel",
    "felix_share": "reel",
    "media_share": "post",
    "story_share": "story",
}
_KINDS = frozenset(_XMA_KINDS.values())
SHARE_ITEM_TYPES = frozenset(_XMA_KINDS) | frozenset(_MEDIA_KINDS)


@dataclass(frozen=True)
class Share:
    """Something shared into a chat."""

    kind: ShareKind
    url: str | None = None  # where it lives on instagram.com
    author: str | None = None  # username of whoever posted it
    caption: str | None = None  # caption, or a card's title
    media_id: str | None = None  # resolves the caption and the video
    preview_url: str | None = None  # cover image

    def to_dict(self) -> dict[str, str]:
        """JSON-ready, without empty fields."""
        return {k: v for k, v in dataclasses.asdict(self).items() if v}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Share | None:
        """Inverse of to_dict; None for anything that isn't a share."""
        if not isinstance(data, dict) or data.get("kind") not in _KINDS:
            return None

        def text(key: str) -> str | None:
            return str(data[key]) if data.get(key) else None

        return cls(
            kind=data["kind"],
            url=text("url"),
            author=text("author"),
            caption=text("caption"),
            media_id=text("media_id"),
            preview_url=text("preview_url"),
        )


def share_from_item(item: dict[str, Any]) -> Share | None:
    """The share in a raw direct item (MQTT or REST), or None if it isn't one."""
    item_type = str(item.get("item_type") or "")
    if item_type in _XMA_KINDS:
        cards = item.get(item_type)
        card = cards[0] if isinstance(cards, list) and cards else None
        if isinstance(card, dict):
            share = _from_card(_XMA_KINDS[item_type], card)
            if share.media_id is None and item.get("original_media_igid"):
                share = dataclasses.replace(share, media_id=str(item["original_media_igid"]))
            return share
    if item_type in _MEDIA_KINDS:
        media = item.get(item_type)
        if isinstance(media, dict) and isinstance(media.get(item_type), dict):
            media = media[item_type]  # a clip nests its media under "clip" again
        if isinstance(media, dict) and isinstance(media.get("media"), dict):
            media = media["media"]  # a story share nests it under "media"
        if isinstance(media, dict):
            return _from_media(_MEDIA_KINDS[item_type], media)
    return None


def share_from_message(message: Any) -> Share | None:
    """The share in an instagrapi DirectMessage (history), or None."""
    item_type = str(getattr(message, "item_type", "") or "")
    raw = getattr(message, "raw_xma", None) or {}
    if item_type in _XMA_KINDS:
        return share_from_item({"item_type": item_type, item_type: raw.get(item_type)})
    media = getattr(message, item_type, None) if item_type in _MEDIA_KINDS else None
    if media is None or isinstance(media, dict):  # story_share and felix_share stay raw
        return share_from_item({"item_type": item_type, item_type: media})
    user = getattr(media, "user", None)
    return Share(
        kind=_MEDIA_KINDS[item_type],
        url=_media_url(_MEDIA_KINDS[item_type], getattr(media, "code", None)),
        author=getattr(user, "username", None),
        caption=getattr(media, "caption_text", None) or None,
        media_id=str(media.pk) if getattr(media, "pk", None) else None,
        preview_url=str(media.thumbnail_url) if getattr(media, "thumbnail_url", None) else None,
    )


def describe(share: Share, *, limit: int = 280) -> str:
    """How a persona sees a share: ``[reel by @x: caption … https://…]``."""
    if share.kind == "profile":
        head = f"profile @{share.author}" if share.author else "profile"
    else:
        head = f"{share.kind} by @{share.author}" if share.author else share.kind
    caption = " ".join((share.caption or "").split())
    if len(caption) > limit:
        caption = caption[: limit - 1].rstrip() + "…"
    parts = [head + (f": {caption}" if caption else "")]
    if share.url:
        parts.append(share.url)
    return f"[{' — '.join(parts)}]"


def _from_card(kind: ShareKind, card: dict[str, Any]) -> Share:
    ref = _content_ref(card)
    media_id = (ref.get("fetch_params") or {}).get("media_igid")
    preview = card.get("preview_url_info") or {}
    return Share(
        kind=kind,
        url=_clean_url(ref.get("target_url") or card.get("target_url")),
        author=ref.get("username") or card.get("header_title_text") or None,
        caption=card.get("caption_body_text") or card.get("title_text") or None,
        media_id=str(media_id) if media_id else None,
        preview_url=preview.get("url") or card.get("preview_url") or None,
    )


def _from_media(kind: ShareKind, media: dict[str, Any]) -> Share:
    user = media.get("user") or {}
    caption = media.get("caption") or {}
    candidates = (media.get("image_versions2") or {}).get("candidates") or [{}]
    pk = str(media.get("pk") or media.get("id") or "").split("_")[0]
    return Share(
        kind=kind,
        url=_media_url(kind, media.get("code")),
        author=user.get("username"),
        caption=caption.get("text") if isinstance(caption, dict) else None,
        media_id=pk or None,
        preview_url=candidates[0].get("url"),
    )


def _content_ref(card: dict[str, Any]) -> dict[str, Any]:
    try:
        ref = json.loads(card.get("serialized_content_ref") or "{}")
    except json.JSONDecodeError, TypeError:
        return {}
    return ref if isinstance(ref, dict) else {}


def _media_url(kind: ShareKind, code: str | None) -> str | None:
    if not code or kind == "story":
        return None
    return f"https://www.instagram.com/{'reel' if kind == 'reel' else 'p'}/{code}/"


def _clean_url(url: Any) -> str | None:
    """Drop Instagram's tracking query from an instagram.com link."""
    if not isinstance(url, str) or not url:
        return None
    parts = urlsplit(url)
    if parts.hostname and parts.hostname.endswith("instagram.com"):
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return url
