"""Links in Instagram messages."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def unwrap_link(url: str) -> str:
    """Return the real target of Instagram's l.instagram.com redirect, or the URL as is.

    Link previews carry ``https://l.instagram.com/?u=<encoded target>&e=...``; the
    target is what the sender actually shared.
    """
    parsed = urlparse(url)
    if parsed.netloc == "l.instagram.com":
        target = parse_qs(parsed.query).get("u", [""])[0]
        if target:
            return target
    return url
