# Design: Surface Graph API Error Details + Recurring Event Guidance

**Date:** 2026-04-06
**Bead:** pa-73vq
**Status:** Draft

## Problem

When the Graph API returns a 4xx error, `graph.py` calls `response.raise_for_status()` which throws `httpx.HTTPStatusError` with just the status code (e.g., "400 Bad Request"). The response body — which contains the actual error code and message from the Graph API — is discarded.

This caused Janet to spend her entire turn budget on trial-and-error debugging when `update_event` returned 400 for a recurring event instance. She misdiagnosed it as a Content-Type header bug and gave up. Investigation revealed the tool actually works fine for recurring instances (validated with real API calls) — the 400 was caused by something in her payload, but without error details she couldn't tell what.

This affects ALL microsoft-mcp tools, not just `update_event`. Any 4xx from the Graph API loses its diagnostic detail.

## Actor Capabilities

- **Janet (CC subprocess)** can see Graph API error codes and messages when tools fail, instead of generic "400 Bad Request"
- **Janet** can use error details to self-diagnose and adjust her approach (e.g., fix timezone format, add missing fields)
- **Janet** can search API docs when she encounters an unfamiliar error code, per CLAUDE.md guidance
- **Janet** can reschedule recurring event instances using instance IDs from `list_events` directly
- **Any MCP client** (Claude Desktop, future tools) benefits from richer error messages automatically
- **Developers** can debug tool failures faster from container logs

## Design

### Error Surfacing in graph.py

In `graph.py`'s `request()` function, replace the bare `response.raise_for_status()` (line 69) with error body extraction on 4xx:

```python
# Before raising on 4xx, extract the Graph API error detail
if response.status_code >= 400:
    detail = ""
    try:
        error_body = response.json()
        error_info = error_body.get("error", {})
        code = error_info.get("code", "")
        message = error_info.get("message", "")
        detail = f" — {code}: {message}" if code else ""
    except Exception:
        # Response wasn't JSON, fall through to raise_for_status
        pass
    if detail:
        raise httpx.HTTPStatusError(
            f"{response.status_code}{detail}",
            request=response.request,
            response=response,
        )
    response.raise_for_status()
```

The 5xx retry logic above this block is untouched — those still retry before reaching this point. If the body isn't JSON (rare), it falls through to the original `raise_for_status()`.

Effect: every tool that calls `graph.request()` now gets error messages like `"400 — ErrorOccurrenceCrossingBoundary: Cannot move occurrence past adjacent occurrence"` instead of just `"400 Bad Request"`.

### Janet CLAUDE.md Changes

Two additions to `.claude/CLAUDE.md` in the personal-assistant repo:

**1. Troubleshooting nudge** (new section after existing tool sections):

```
### When tools return errors
Graph API errors include the error code and message (e.g., "ErrorOccurrenceCrossingBoundary: ..."). Read the error detail before retrying — it usually tells you exactly what's wrong. If the error code is unfamiliar, search the web for "microsoft graph {error code}" to understand the constraint. Don't retry with variations hoping something sticks.
```

**2. Recurring event guidance** (add to the existing calendar/meetings section):

```
### Rescheduling recurring events
Instance IDs from list_events work directly with update_event — no special handling needed for recurring events. When rescheduling a single occurrence:
1. Use list_events to find the instance. It has its own id and a seriesMasterId.
2. Call update_event with the instance id. Always pass both start AND end together.
3. Use the same timezone format as the original event, or UTC with properly converted times.
4. The Graph API automatically creates a series exception — you don't need to do anything special.
5. Constraint: you cannot move an occurrence past the day of the adjacent occurrence in the series.
```

## Assumptions

| # | Assumption | Risk if Wrong | Validation |
|---|-----------|---------------|------------|
| 1 | Graph API consistently returns `{"error": {"code": "...", "message": "..."}}` on 4xx | Error extraction silently fails, falls through to generic raise_for_status — no worse than today | Validated: documented OData error format |
| 2 | `httpx.HTTPStatusError` can be constructed with a custom message + request + response | Exception construction fails | Verify against httpx docs before implementation |
| 3 | MCP gateway passes tool exceptions through to Janet as error text | Janet still sees generic errors | Validated: Janet saw "400" in her conversation, so exceptions propagate |
| 4 | CLAUDE.md changes deploy via existing entrypoint.sh copy mechanism | Guidance doesn't reach production Janet | Validated: entrypoint copies `.claude/CLAUDE.md` to runtime location |

## Testing

- Unit test: mock a 400 response with Graph error JSON body → verify exception message contains error code and message
- Unit test: mock a 400 response with non-JSON body → verify falls through to standard raise_for_status
- Unit test: mock a 403 response → verify error extraction works for other 4xx codes
- Existing tests continue to pass (5xx retry behavior unchanged)

## Files Changed

| File | Repo | Change |
|------|------|--------|
| `src/microsoft_mcp/graph.py` | microsoft-mcp | Extract Graph error body on 4xx, include in exception message |
| `tests/` (test file TBD) | microsoft-mcp | Tests for 4xx error extraction |
| `.claude/CLAUDE.md` | personal-assistant | Troubleshooting nudge + recurring event guidance |

## Validated Findings (from brainstorming)

These were validated with real API calls during brainstorming and do NOT need re-verification:

- `PATCH /me/events/{instanceId}` works for recurring event instances — no special endpoint needed
- Location updates, time changes, and reverts all succeed on recurring instances
- The event's `type` automatically changes from `"occurrence"` to `"exception"` when modified
- `update_event` tool works as-is for recurring events — Janet's 400 was caused by her payload, not the tool
- Instance IDs from `calendarView` (used by `list_events`) are first-class event IDs
- `ErrorOccurrenceCrossingBoundary` only triggers when moving past adjacent occurrences

## Open Question (Out of Scope)

**Tool call logging:** Janet's MCP tool call payloads aren't stored anywhere — we only have her summarized output. If we'd had a log of what she actually sent to `update_event`, we could have diagnosed the 400 immediately. This is a broader observability question that applies to all MCP tool usage, not just calendar operations. Worth considering as a separate initiative.
