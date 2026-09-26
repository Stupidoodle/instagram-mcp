"""Unit tests for the channel tools."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from instagram_mcp.channel import Channel
from instagram_mcp.tools.channel import register_channel_tools

T1 = "340282366841710300949128531777654287254"


class TestSubscriptionTools:
    def setup_method(self) -> None:
        self.mcp = MCPServer("test")
        self.channel = Channel(self_user_id="1", describe_thread=lambda _t: ("Ly", {}))
        register_channel_tools(self.mcp, self.channel)

    def _tool(self, name: str) -> Any:
        return self.mcp._tool_manager._tools[name].fn

    def test_subscribe_list_unsubscribe(self) -> None:
        assert self._tool("subscribe")(chat_id=T1, alias="ly")["subscribed"] == "ly"
        assert self._tool("list_subscriptions")()["subscriptions"] == [f"ly → …{T1[-4:]}"]
        assert self._tool("unsubscribe")(to="ly") == {"success": True, "unsubscribed": "ly"}

    def test_refusals(self) -> None:
        self._tool("subscribe")(chat_id=T1, alias="ly")
        assert "refused" in self._tool("subscribe")(chat_id="1234567", alias="ly")["error"]
        assert "refused" in self._tool("unsubscribe")(to="nope")["error"]
        assert "refused" in self._tool("set_idle")(minutes=-1)["error"]
        assert "refused" in self._tool("set_idle")(minutes=10, to="nope")["error"]

    def test_set_idle(self) -> None:
        self._tool("subscribe")(chat_id=T1, alias="ly")
        assert (
            self._tool("set_idle")(minutes=60)["idle"]
            == "60m for ly (Ly) (resets to 5m when they write)"
        )
        assert self._tool("set_idle")(minutes=0, to="ly")["idle"].startswith("paused")
