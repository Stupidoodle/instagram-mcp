"""A channel-only MCP server on stdio, for the wire-level channel test.

Builds the real MCPServer + Channel wiring without an Instagram login and
feeds one fake MQTT event from another thread, like the reader thread does.
With ``--claude-code`` it serves through ChannelMCPServer, as the real server does.
"""

import sys
import threading

from mcp.server.mcpserver import MCPServer

from instagram_mcp.channel import INSTRUCTIONS, Channel, ChannelMCPServer
from instagram_mcp.mqtt.events import MessageEvent
from instagram_mcp.tools import register_channel_tools

channel = Channel(self_user_id="1", describe_thread=lambda _t: ("Alex", {"2": "Alex"}))
channel.subscribe("111111", "alex")
server_cls = ChannelMCPServer if "--claude-code" in sys.argv else MCPServer
mcp = server_cls("instagram-mcp", instructions=INSTRUCTIONS, middleware=[channel.middleware])
register_channel_tools(mcp, channel)

event = MessageEvent("111111", "i1", 2, "hey from mqtt", "text", 0)
threading.Timer(0.5, channel.handle, args=(event,)).start()
mcp.run("stdio")
