"""Disappearing photos and videos in direct messages: view once or allow replay.

The Instagram app uploads the file to Messenger's rupload with an
``ephemeral_media_view_mode`` header, then sends it through
``direct_v2/threads/broadcast/raven_attachment/`` (``?video=1`` for videos) with the
matching ``view_mode``. Worked out against the live API on 2026-09-28: instagrapi
only sends videos this way, and always as ``permanent``.
"""

from __future__ import annotations

import json
import random
import secrets
import time
import uuid
from typing import TYPE_CHECKING, Any, Literal

import httpx2
from instagrapi.image_util import prepare_image

if TYPE_CHECKING:
    from pathlib import Path

    from instagrapi import Client

ViewMode = Literal["once", "replayable"]
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v"})
_HEADER_MODE = {"once": "0", "replayable": "1"}  # "permanent" is "2"
_RUPLOAD = "https://rupload.facebook.com"
_DEVICE = {
    "manufacturer": "Google",
    "model": "sdk_gphone_arm64",
    "android_version": 30,
    "android_release": "11",
}


def send_disappearing(
    ig: Client,
    thread_id: str,
    path: Path,
    view_mode: ViewMode,
    *,
    transport: httpx2.BaseTransport | None = None,
) -> dict[str, Any]:
    """Send a photo or video that disappears after one view (or a replay).

    Args:
        ig: A logged-in instagrapi client.
        thread_id: The direct thread.
        path: A photo (jpg/png/webp) or an H.264 mp4 video.
        view_mode: ``"once"`` (view once) or ``"replayable"`` (allow replay).
        transport: For tests.

    Returns:
        The raven_attachment response.
    """
    if view_mode not in _HEADER_MODE:
        msg = f"view_mode must be 'once' or 'replayable', not {view_mode!r}"
        raise ValueError(msg)
    raven = {"ephemeral_media_view_mode": _HEADER_MODE[view_mode], "ig_raven_metadata": "{}"}
    with httpx2.Client(
        timeout=300, transport=transport, verify=getattr(ig, "tls_verify", True)
    ) as http:
        if path.suffix.lower() in VIDEO_SUFFIXES:
            return _send_video(
                ig, http, thread_id=thread_id, path=path, view_mode=view_mode, raven=raven
            )
        return _send_photo(
            ig, http, thread_id=thread_id, path=path, view_mode=view_mode, raven=raven
        )


def _send_photo(
    ig: Client,
    http: httpx2.Client,
    *,
    thread_id: str,
    path: Path,
    view_mode: ViewMode,
    raven: dict[str, str],
) -> dict[str, Any]:
    photo, (width, height) = prepare_image(str(path), max_side=1080)
    entity = f"fb_uploader_{int(time.time() * 1000)}"
    headers = ig._messenger_rupload_headers({"image_type": "FILE_ATTACHMENT", **raven})
    media_id = _upload(
        http,
        f"{_RUPLOAD}/messenger_image/{entity}",
        content=photo,
        kind="image/jpeg",
        entity=entity,
        headers=headers,
    )
    data = _body(
        ig, thread_id=thread_id, media_id=media_id, view_mode=view_mode, width=width, height=height
    ) | {
        "original_media_type": "1",
        "upload_id": str(media_id),
    }
    return _broadcast(ig, "direct_v2/threads/broadcast/raven_attachment/", data)


def _send_video(
    ig: Client,
    http: httpx2.Client,
    *,
    thread_id: str,
    path: Path,
    view_mode: ViewMode,
    raven: dict[str, str],
) -> dict[str, Any]:
    video = path.read_bytes()
    width, height, duration = ig._direct_video_metadata(path)
    hex_id, ms = secrets.token_hex(16), int(time.time() * 1000)
    entity = f"{hex_id}-0-{len(video)}-{ms}-{ms}"
    upload_id = str(random.randint(10**11, 10**12 - 1))  # noqa: S311 - an id, not a secret
    headers = ig._messenger_rupload_headers(
        {
            "video_type": "FILE_ATTACHMENT",
            "segment-start-offset": "0",
            "segment-type": "3",
            "x_fb_video_waterfall_id": f"{upload_id}_{hex_id[:12].upper()}_Mixed_0",
            **raven,
        }
    )
    url = f"{_RUPLOAD}/messenger_video/{entity}"
    offset = http.get(url, headers=headers).raise_for_status().json().get("offset", 0)
    media_id = _upload(
        http,
        url,
        content=video[int(offset) :],
        kind="video/mp4",
        entity=entity,
        headers=headers,
        offset=int(offset),
    )
    data = _body(
        ig, thread_id=thread_id, media_id=media_id, view_mode=view_mode, width=width, height=height
    ) | {
        "original_media_type": "2",
        "video_result": str(media_id),
        "upload_id": upload_id,
        "clips": [{"length": duration, "source_type": "3", "camera_position": "back"}],
        "poster_frame_index": 0,
        "length": duration,
        "audio_muted": False,
    }
    return _broadcast(ig, "direct_v2/threads/broadcast/raven_attachment/?video=1", data)


def _upload(
    http: httpx2.Client,
    url: str,
    *,
    content: bytes,
    kind: str,
    entity: str,
    headers: dict[str, str],
    offset: int = 0,
) -> int:
    """POST the bytes to rupload; returns the media id for the broadcast."""
    response = http.post(
        url,
        content=content,
        headers=headers
        | {
            "content-type": "application/octet-stream",
            "offset": str(offset),
            "x-entity-length": str(len(content) + offset),
            "x-entity-name": entity,
            "x-entity-type": kind,
        },
    )
    response.raise_for_status()
    return int(response.json()["media_id"])


def _body(
    ig: Client, *, thread_id: str, media_id: int, view_mode: ViewMode, width: int, height: int
) -> dict[str, Any]:
    """The raven_attachment fields shared by photos and videos."""
    token = ig.generate_mutation_token()
    now = str(int(time.time()))
    return {
        "recipient_users": "[]",
        "view_mode": view_mode,
        "has_camera_metadata": "1",
        "camera_entry_point": "3",
        "thread_ids": json.dumps([str(thread_id)]),
        "reshare_mode": "allow_reshare",
        "send_attribution": "direct_composer",
        "client_context": token,
        "camera_session_id": str(uuid.uuid4()),
        "attachment_fbid": str(media_id),
        "include_e2ee_mentioned_user_list": "1",
        "hide_from_profile_grid": "false",
        "timezone_offset": "0",
        "client_shared_at": now,
        "configure_mode": "2",
        "source_type": "3",
        "camera_position": "back",
        "_uid": str(ig.user_id),
        "device_id": ig.android_device_id,
        "composition_id": str(uuid.uuid4()),
        "mutation_token": token,
        "_uuid": ig.uuid,
        "creation_surface": "camera",
        "has_ig_camera_edits": "false",
        "capture_type": "normal",
        "audience": "default",
        "client_timestamp": now,
        "media_transformation_info": json.dumps(
            {
                "width": str(width),
                "height": str(height),
                "x_transform": "0",
                "y_transform": "0",
                "zoom": "1.0",
                "rotation": "0.0",
                "background_coverage": "0.0",
            }
        ),
        "edits": {"filter_type": 0, "filter_strength": 1.0},
        "extra": {"source_width": width, "source_height": height},
        "device": _DEVICE,
    }


def _broadcast(ig: Client, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = ig.private_request(
        endpoint, data=ig.with_default_data(data), with_signature=True
    )
    return result
