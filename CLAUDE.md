# Instagram MCP Server - Claude Guidelines

## Project Overview
Python MCP server for Instagram DMs using instagrapi. Realtime events arrive over
Instagram's MQTT connection and are pushed into the Claude Code session as a channel
(`notifications/claude/channel`); nothing polls.

## Dependency Management
- NEVER manually edit pyproject.toml for dependencies
- ALWAYS use `uv add <package>` to add dependencies
- ALWAYS use `uv add --dev <package>` for dev dependencies
- Use `uv remove <package>` to remove dependencies
- Use `uv sync` to install dependencies from lockfile

## MCP Server Best Practices
- Never use `print()` - it corrupts JSON-RPC on stdio transport
- Use `logging` module with stderr handler instead
- All tools must have proper docstrings (they become MCP tool descriptions)
- Handle errors gracefully - return error messages, don't crash
- Use the `MCPServer` decorator pattern for tool registration (mcp 2.x; FastMCP is gone)
- Never add a blocking "wait for reply" tool: events are pushed by `channel.py`

## Code Quality
- Run `uv run ruff check .` before committing
- Run `uv run ruff format .` to format code
- Run `uv run mypy src/` with strict mode
- Maintain 100% test coverage
- Use type hints for all functions and methods
- Use Google-style docstrings for all public modules, classes, and functions

## Testing
- Unit tests go in `tests/unit/`
- Integration tests go in `tests/integration/`
- Use fixtures from `tests/conftest.py`
- Mock external APIs (Instagram) in unit tests
- Run tests: `uv run pytest tests/ -v --cov=src --cov-report=term-missing`

## Instagram API (instagrapi)
- Always cache sessions to avoid rate limiting
- Never log credentials or session data
- Handle 2FA gracefully
- Respect Instagram's rate limits
- Session file location: configured via INSTAGRAM_SESSION_FILE env var

## Project Structure
```
src/instagram_mcp/
├── __init__.py
├── server.py           # MCP server entry point, wires client + MQTT + channel
├── channel.py          # Claude Code channel: aliases, event push, idle nudges
├── client.py           # Instagram client wrapper
├── config.py           # Configuration management
├── mqtt/               # MQTToT realtime: connection, parser, manager (listener + watchdog)
├── tools/
│   ├── __init__.py
│   ├── channel.py      # Channel tools (same names as the WhatsApp channel)
│   ├── threads.py      # Thread management tools
│   ├── messages.py     # Message history tools
│   └── media.py        # Sharing tools
└── models/
    ├── __init__.py
    └── schemas.py      # Pydantic models
```

## Environment Variables
```
INSTAGRAM_USERNAME=     # Needed to log in
INSTAGRAM_PASSWORD=     # Needed to log in
INSTAGRAM_SESSION_FILE= # Optional, defaults to .instagram_session
INSTAGRAM_SUBSCRIBE=    # Chats to stream on start: alias=thread_id,...
INSTAGRAM_IDLE_MINUTES= # Idle nudge threshold (5; 0 disables), backs off after 30 min
```
See README.md for the full list.

## Running the Server
```bash
uv run instagram-mcp   # stdio; in Claude Code: --dangerously-load-development-channels server:instagram
```

## Common Commands
```bash
# Add dependency
uv add <package>

# Add dev dependency
uv add --dev <package>

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=src --cov-report=term-missing

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Type check
uv run mypy src/

# Run MCP server
uv run python -m instagram_mcp.server
```
