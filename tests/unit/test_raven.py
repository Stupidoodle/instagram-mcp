"""Disappearing photos and videos: the view-mode header on upload and the raven send."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import httpx2
import pytest
from PIL import Image

from instagram_mcp.raven import send_disappearing

if TYPE_CHECKING:
    from pathlib import Path


def _ig() -> MagicMock:
    ig = MagicMock(user_id=1000, android_device_id="android-x", uuid="u-1", tls_verify=True)
    ig._messenger_rupload_headers.side_effect = lambda extra: {"authorization": "Bearer t"} | extra
    ig.generate_mutation_token.return_value = "tok"
    ig.with_default_data.side_effect = lambda data: data
    ig.private_request.return_value = {"status": "ok", "payload": {"item_id": "i9"}}
    ig._direct_video_metadata.return_value = (720, 1280, 2.0)
    return ig


def _transport(seen: list[httpx2.Request]) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx2.Response(200, json={"offset": 0})
        return httpx2.Response(200, json={"media_id": 555})

    return httpx2.MockTransport(handler)


def test_a_view_once_photo(tmp_path: Path) -> None:
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (40, 50), (10, 20, 30)).save(photo, "JPEG")
    ig, seen = _ig(), []
    result = send_disappearing(ig, "123", photo, "once", transport=_transport(seen))

    (upload,) = seen
    assert upload.url.host == "rupload.facebook.com"
    assert upload.url.path.startswith("/messenger_image/fb_uploader_")
    assert upload.headers["ephemeral_media_view_mode"] == "0"
    assert upload.headers["x-entity-type"] == "image/jpeg"
    endpoint = ig.private_request.call_args.args[0]
    data: dict[str, Any] = ig.private_request.call_args.kwargs["data"]
    assert endpoint == "direct_v2/threads/broadcast/raven_attachment/"
    assert ig.private_request.call_args.kwargs["with_signature"] is True
    assert (data["view_mode"], data["original_media_type"], data["attachment_fbid"]) == (
        "once",
        "1",
        "555",
    )
    assert json.loads(data["thread_ids"]) == ["123"]
    assert result["payload"]["item_id"] == "i9"


def test_an_allow_replay_video(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 64)
    ig, seen = _ig(), []
    send_disappearing(ig, "123", video, "replayable", transport=_transport(seen))

    offset, upload = seen
    assert offset.method == "GET"
    assert upload.url.path.startswith("/messenger_video/")
    assert upload.headers["ephemeral_media_view_mode"] == "1"
    assert upload.headers["x-entity-length"] == "64"
    endpoint = ig.private_request.call_args.args[0]
    data = ig.private_request.call_args.kwargs["data"]
    assert endpoint == "direct_v2/threads/broadcast/raven_attachment/?video=1"
    assert (data["view_mode"], data["original_media_type"], data["video_result"]) == (
        "replayable",
        "2",
        "555",
    )
    assert data["length"] == 2.0


def test_only_the_two_disappearing_modes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="view_mode"):
        send_disappearing(_ig(), "1", tmp_path / "p.jpg", "permanent")  # type: ignore[arg-type]
