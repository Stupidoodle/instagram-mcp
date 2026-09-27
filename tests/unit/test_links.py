"""Unit tests for link unwrapping."""

from instagram_mcp.links import unwrap_link


def test_instagram_redirect_is_unwrapped() -> None:
    wrapped = "https://l.instagram.com/?u=https%3A%2F%2Fexample.com%2Fsurvey%3Fid%3D42&e=AUBf"
    assert unwrap_link(wrapped) == "https://example.com/survey?id=42"


def test_other_urls_are_left_alone() -> None:
    assert unwrap_link("https://example.com/a?u=x") == "https://example.com/a?u=x"


def test_a_redirect_without_a_target_is_left_alone() -> None:
    assert unwrap_link("https://l.instagram.com/?e=abc") == "https://l.instagram.com/?e=abc"
