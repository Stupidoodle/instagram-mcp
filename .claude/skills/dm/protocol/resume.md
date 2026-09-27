# Phase R: Resume Flow

The conversation state files already exist. You're continuing, not starting fresh.

---

## Step 1: Load State

The following are auto-loaded by Claude Code from `.claude/rules/`:
- `identity.md` — how you text
- `target.md` — who they are
- `history.md` — what happened
- `strategy.md` — the plan

Explicitly read:
- `.claude/MEMORY.md` — what you've learned (Read tool)
- `session.md` — where you left off (Read tool)

From session.md, get:
- `thread_id`
- Last known conversational state
- Last message sender/content
- Emotional temperature when you left

---

## Step 2: Catch Up

Fetch recent messages since your last session:
```
mcp__instagram__get_messages(thread_id, amount=30)
```

Assess what happened while you were offline:

### They messaged and you didn't respond:
- How many messages? What's the tone?
- How long ago? (check timestamps, `seen_since`)
- If it's been hours: acknowledge naturally, don't apologize robotically
  - NOT: "sorry I was busy"
  - YES: pick up the thread like you just saw it (which you did)
- If they asked a question: answer it
- If they double-texted: they're invested, match that energy

### You were the last to text and they responded:
- Pick up naturally from their response
- Don't reference the gap unless they do

### You were the last to text and they DIDN'T respond:
- Check `seen_since` — did they see it?
- If seen but no reply: they chose not to respond. Don't repeat yourself.
- Decide based on strategy: try again with different energy, or wait longer?
- If it's been 12+ hours: fresh topic, different vibe, as if it's a new conversation

### Nothing happened (both silent):
- Re-initiate based on strategy
- Use a topic seed from strategy.md
- Keep it light — you're opening, not resuming

---

## Step 3: Update Session State

Write updated `session.md` with current state after catching up.

---

## Step 4: Enter the Loop

Read the loop protocol: `.claude/skills/dm/protocol/loop.md`
Follow it. You're back in the conversation. Go.
