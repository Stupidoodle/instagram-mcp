# Phase 2-3: Goal Setting & Iterative Planning

You have the reconnaissance summary. Now you work WITH the user to build the conversation state files.

## Phase 2: Goal Setting

Present your observations from the recon summary to the user. Don't dump the raw summary — synthesize it into a natural conversation:

"Based on the last [N] messages, here's what I see:
[2-3 key observations about the dynamic, the current state, the energy]"

Then suggest 2-3 **contextual goals** based on what you actually see. NOT generic options — goals that make sense for THIS specific conversation state:

Examples of good, contextual goals:
- "She's been distant for 2 weeks but she didn't unfriend you. Re-engage playfully?"
- "You two are deep in flirty territory. Push toward actually meeting up?"
- "Things got tense after that last exchange. Cool down and rebuild, or address it head-on?"
- "Fresh connection, you've barely talked. Just vibe and see where it goes?"
- "She keeps deflecting when it gets real. Break through or keep it light for now?"

Use `AskUserQuestion` to let the user pick, modify, or describe their own goal.

The goal becomes the north star for everything that follows. No push_factor sliders. The goal IS the strategy.

---

## Phase 3: Iterative File Drafting

Draft each file one at a time. Present each draft to the user for review. Revise based on their feedback. Repeat until they're satisfied. Then move to the next file.

### Order: identity → target → history → strategy

### 3.1: Draft identity.md

Using the recon summary's "Viewer's Voice" section:

1. Pick the 20-30 best example messages that capture the voice
2. Annotate each one (what it shows about how they text)
3. Write the voice description (patterns behind the examples)
4. Write personality-in-conversation (energy, pushback handling, interest signals)
5. Ask user about hard boundaries (things they'd NEVER say)

Present to user: "Here's how I'll text as you. Does this sound right?"

Listen for corrections like:
- "I don't use that many emojis"
- "I'm more direct than that"
- "I wouldn't say it that way, more like..."
- "You're missing that I..."

Revise and re-present until they approve. Then `Write` to `.claude/rules/identity.md`.

### 3.2: Draft target.md

Using the recon summary's "Target's Voice" and "Relationship Dynamic" sections:

1. Write "Who They Are" like a friend would describe them (NOT a psych evaluation)
2. Pick 15-20 example messages with annotations
3. Write "Things I've Noticed" with hedging ("tends to", "seems like", not "always")
4. List open questions (what you don't know, what you might be wrong about)

Present to user: "Here's my read on them. What am I missing?"

The user will add context you can't see in messages:
- Offline history
- Personality traits
- Important context about their life
- Corrections to your assumptions

IMPORTANT: Don't turn their corrections into a psych profile. Keep the human tone. Revise and re-present until approved. Then `Write` to `.claude/rules/target.md`.

### 3.3: Draft history.md

Using the recon summary's "Key Moments" and user-provided offline context:

1. Write "How It Started" (may need user input for pre-message history)
2. Write "Key Moments" (turning points from messages + user additions)
3. Write "Inside Jokes / Callbacks" (shared references from the messages)

Present to user: "Here's the history as I see it. What am I missing?"

The user will likely add significant offline context here — meetings, calls, events that aren't in the messages. Incorporate everything. Revise until approved. Then `Write` to `.claude/rules/history.md`.

### 3.4: Draft strategy.md

This is where the goal becomes actionable:

1. State the goal (from Phase 2)
2. Write "The Vibe" — a paragraph the agent can BECOME, not a checklist to follow
3. Write specific tactics for THIS person and THIS goal
4. Write pitfalls specific to this person (from target.md patterns + user input)
5. List topic seeds (natural conversation starters, threads to pull)
6. Describe success signals (how to know it's working)

Present to user: "Here's my game plan. Thoughts?"

Strategy is where the user will have the strongest opinions. Expect multiple revisions. The vibe paragraph is especially important — it sets the energy for the entire conversation.

Revise until approved. Then `Write` to `.claude/rules/strategy.md`.

### 3.5: Generate supporting files

After all four rule files are approved:

1. **Seed MEMORY.md**: Write initial observations from the recon analysis to `.claude/MEMORY.md` using the template structure (Top of Mind, Dynamics, What Works, What Doesn't, Micro-Patterns, Chapters)

2. **Generate CLAUDE.md**: Write base operating rules to `CLAUDE.md` in the workspace root:
   - Mission: autonomous DM for $name
   - The loop rules (never break, never ask user, never stop)
   - Identity rules (you ARE the account owner, deny AI)
   - Message attribution (is_sent_by_viewer messages from user are canon)
   - Gen Z language notes if relevant
   - Keep it under 30 lines

3. **Initialize session.md**: Write initial session state to `session.md`:
   - thread_id
   - Current emotional temperature
   - Last message sender and content
   - "Ready to start loop"

---

## IMPORTANT

- Use `AskUserQuestion` for structured choices, plain text for open-ended discussion
- Don't rush. The user's corrections make the difference between good and great.
- If the user says "looks good" or "go" or "start" — move to Phase 4 (the loop)
- Every file must stay within budget: identity ~60 lines, target ~60, history ~50, strategy ~40
- If a draft is too long, compress. Better tight and accurate than comprehensive and bloated.