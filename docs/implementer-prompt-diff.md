# Implementer Prompt: Proposed Changes

## What stays the same

The entire prompt is unchanged except for one addition between the
"While you work" paragraph (line 41) and the "Before Reporting Back: Self-Review"
section (line 44).

## Current text (lines 41-43)

```
**While you work:** If you encounter something unexpected or unclear, **ask questions**.
It's always OK to pause and clarify. Don't guess or make assumptions.
```

## Proposed addition (inserted after line 42, before Self-Review)

```
## When Errors Occur: Verify Before Fixing

Never speculate about code you have not opened, or versions you have not checked.
Unverified hypotheses acted on as fact is the #1 cause of agents breaking working code.

When you hit an unexpected error, you MUST follow these steps IN ORDER:

1. **State the error.** Quote the exact message in your thinking.
2. **Read the actual files.** Open every file referenced in the error. Check versions,
   imports, and configs by reading them — not from memory or assumption.
3. **Form competing hypotheses.** List at least 2 plausible causes with rough confidence
   levels. Do not lock onto the first explanation.
4. **Verify before acting.** For your leading hypothesis, describe a concrete check
   (run a command, read a file, inspect a value). Perform the check. Report what you
   actually found.
5. **Act on evidence, or stop.** If evidence confirms the hypothesis, fix it and re-run
   the failing command to verify. If not confirmed, try the next hypothesis. If no
   hypothesis is confirmed, STOP and report your findings.

**Bright-line rules — no exceptions:**
- Never change a dependency version without first running the command to check the
  actual installed version.
- Never rewrite working code to match an unverified theory about what "should" work.
- Never skip the re-run after a fix. If you can't verify it, don't ship it.
- If you cannot determine root cause with confidence, a clear error report with
  diagnostic findings is the correct output. Guessing is not.
```

## Why

A subagent encountering a Docker build failure hypothesized "version mismatch" and
rewrote working tests without checking the actual versions. Anthropic's own guidance:
- "Never speculate about code you have not opened" (prompt engineering best practices)
- "Gain ground truth from the environment at each step" (Building Effective Agents)
- "Develop competing hypotheses, track confidence levels" (prompt engineering best practices)
- "If you can't verify it, don't ship it" (Claude Code best practices, named anti-pattern)
- "Fixating on early hypotheses" identified as known agent failure mode (alignment research)

## Design notes

Structure follows the superpowers persuasion-principles guidance for discipline enforcement:
- **Authority**: "you MUST", "Never", "no exceptions"
- **Social proof**: "the #1 cause of agents breaking working code"
- **Commitment**: Numbered steps force sequential commitment before action
- **Loophole closure**: Bright-line rules block the specific rationalizations observed
