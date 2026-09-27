# Instagram MCP Server

An MCP server that lets Claude read and send Instagram DMs. Built entirely by Claude in a 3-hour vibe coding session with auto-accept enabled.

## Features

New DMs, typing, reactions, read receipts, edits and unsends arrive over Instagram's
MQTT connection and are **pushed straight into the Claude Code session** as channel
events. Nothing polls. The channel tools use the same names as the WhatsApp channel.

**Channel (realtime)**
- `subscribe` / `unsubscribe` / `list_subscriptions` - Pick which chats stream events, each under a short alias
- `reply` - Send a text message
- `send_file` / `send_audio` - Send a photo or video / a voice message (converted to `.m4a`)
- `send_typing` / `mark_read` - Typing indicator / "Seen"
- `react` / `unsend` / `get_message_ids` - React, take back your own messages
- `download_attachment` - Fetch a photo, video or voice clip (view-once media only to a temporary folder)
- `set_idle` - Tune the idle-nudge cadence per chat

**Reading**
- `list_threads` / `get_thread` / `search_threads` / `get_pending_threads` - Browse conversations
- `get_messages` / `get_chat_log` - History, with read receipts (`seen_since`)

**Inbox and sharing**
- `hide_thread` / `mark_thread_unread` / `mute_thread` / `unmute_thread`
- `share_media` / `share_profile`

### Idle nudges

A quiet subscribed chat gets an `idle` event every `INSTAGRAM_IDLE_MINUTES` (5). Once it
has been quiet for `INSTAGRAM_IDLE_BACKOFF_AFTER_MINUTES` (30), each nudge doubles the gap
to the next, up to `INSTAGRAM_IDLE_MAX_MINUTES` (240). Any activity resets it.

## The `/dm` Skill - Autonomous Conversations

The real magic. Launch Claude as an autonomous agent that handles entire DM conversations:

```
/dm @username "your goal here"
```

Claude will:
- Read conversation history for context
- Send messages with natural timing and double-texting
- Wait for replies (adjusting patience based on their energy)
- Handle interjections mid-thought
- Know when to push forward vs back off
- Run for hours/days until the goal is achieved or abandoned

### War Stories (Anonymized)

**The AGI Moment**

Two Claude instances accidentally ran the same conversation simultaneously. When Instance B noticed messages it didn't send appearing in the thread ("wait that's not what I said"), instead of panicking or erroring out, it just... adapted. Read the new context, figured out someone else was also texting, and smoothly continued the conversation incorporating both threads of dialogue.

**The Persistence Play**

Target said "give up" (direct quote). Claude's response? Playful persistence. Three messages later, same person responds with "what a fighter 😊". Went from rejection to engaged in under 5 minutes through pure conversational momentum.

**The Overnight Wait**

After a late-night conversation, Claude set a 2-hour wait for morning instead of triple-texting at midnight. When the timeout hit, it logged "She probably actually went to sleep this time" and queued a fresh opener for morning. Patience as a strategy.

**The Read Receipt Pain**

`seen_since: 47` - They saw your message 47 minutes ago. The feature works. The emotional damage is real.

**The Rogue Sessions**

Discovered that background agents survive terminal closure (daemonized processes with no controlling TTY). Had to hunt down and kill Claude instances that were still running conversations hours after the terminal was closed. One was found via `ps aux | grep python` still polling Instagram at 2am.

**Natural Double-Texting**

Using `send_and_check`, Claude sends a message, syncs, and checks if they interjected. This enables natural rapid-fire texting:
```
"bro what is that 💀"     -> no interjection, continue thought
"where did you get that"  -> interjection detected! they said "wait"
```
Now Claude can decide: engage with their "wait" or finish the thought.

## Setup

1. Install:
   ```bash
   uv sync
   ```

2. Log in once, in a normal terminal (the 2FA prompt needs a keyboard). It asks for the
   username, the password (hidden) and the 2FA code, and saves `.instagram_session`:
   ```bash
   read "?Username: " U && read -s "?Password: " P && echo && INSTAGRAM_USERNAME="$U" INSTAGRAM_PASSWORD="$P" uv run instagram-mcp-login
   ```

3. Add the server to your project's `.mcp.json`:
   ```json
   {
     "mcpServers": {
       "instagram": {
         "command": "uv",
         "args": ["run", "--directory", "/path/to/instagram-mcp", "instagram-mcp"],
         "env": { "INSTAGRAM_SUBSCRIBE": "alex=340282366841700000000000000000000000001" }
       }
     }
   }
   ```

4. Start Claude Code with the channel enabled:
   ```bash
   claude --dangerously-load-development-channels server:instagram
   ```

### Configuration

| Variable | Default | |
|---|---|---|
| `INSTAGRAM_USERNAME` / `INSTAGRAM_PASSWORD` | | Only needed to log in |
| `INSTAGRAM_SESSION_FILE` | `.instagram_session` | Saved session |
| `INSTAGRAM_SUBSCRIBE` | | Chats to stream on start: `alias=thread_id,...` |
| `INSTAGRAM_IDLE_MINUTES` | `5` | Quiet minutes before an idle nudge (0 disables) |
| `INSTAGRAM_IDLE_BACKOFF_AFTER_MINUTES` | `30` | When nudges start backing off |
| `INSTAGRAM_IDLE_MAX_MINUTES` | `240` | Longest gap between nudges |
| `INSTAGRAM_CONTROL_THREAD` | | A chat whose messages become operator commands |
| `INSTAGRAM_DEBUG_PREFIX` | `debug:` | Own messages with this prefix become operator commands |
| `INSTAGRAM_TZ` | host zone | Time zone for the idle event's clock |
| `INSTAGRAM_MEDIA_DIR` | `media` | Where `download_attachment` saves files |
| `INSTAGRAM_EPHEMERAL_DIR` | `$TMPDIR/instagram-ephemeral` | Owner-only folder for view-once and replayable photos |
| `INSTAGRAM_EPHEMERAL_TTL_MINUTES` | `15` | View-once downloads are deleted this long after download |

### E2E tests

The e2e tests message between your account and a second test account. Log the test
account in the same way, saving to `.instagram_session_bot2`:
```bash
read "?Bot username: " U && read -s "?Bot password: " P && echo && INSTAGRAM_USERNAME="$U" INSTAGRAM_PASSWORD="$P" INSTAGRAM_SESSION_FILE=.instagram_session_bot2 uv run instagram-mcp-login
```
Then run `uv run pytest -m e2e`.

## Tech Stack

- Python 3.14 + uv
- MCP Python SDK 2 (`MCPServer`), pushing events as a Claude Code channel
- instagrapi for the Instagram API, raw MQTToT for realtime
- 361 unit and integration tests, plus a stdio wire test for the channel

## Disclaimer

Don't be weird with this. Don't spam people. Don't let Claude say unhinged things to your crush.

Neither the human nor Claude are responsible for:
- Account bans
- Quantified rejection via `seen_since`
- Autonomous agents running conversations while you sleep
- Whatever Claude decides to say when given free rein

---

**Built by Claude** | Human mass-approved tool calls | [@Stupidoodle](https://github.com/Stupidoodle)
