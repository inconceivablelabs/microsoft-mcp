# Design: `get_email` text-body option (token-cost lever)

**Date:** 2026-06-23
**Status:** Proposed
**Bead:** pa-l24r.2 (cross-repo: the microsoft-mcp side of Janet's email-body token lever)
**Related:** pa-pq2g (Headroom eval, closed), pa-kz9s.9 (cache forensics — incremental-write cost), personal-assistant `dispatcher.py:548` (triage `get_email` call site)

## Problem

`get_email` returns the email **body as raw HTML** (`contentType: "html"`). Measured on real bodies via stdlib strip: HTML markup is **24%** of a clean transactional email up to **84%+** of a templated notification — pure token waste for an LLM consumer. Janet's ms365 `email_triage` agent reads each candidate email via `get_email` (`dispatcher.py:548`), so that markup lands in the model context every triage turn, contributing to the incremental cache-creation write that `pa-kz9s.9` identified as the dominant per-call cost.

**Root cause (confirmed in source):** `graph.request()` already sets `Prefer: outlook.body-content-type="text"` when a GET has `$search` or `body` in `$select` (graph.py:26-30) — which is why `search_emails` and `list_emails` return text. `get_email` sets neither (`params = {}` + only `$expand`, tools.py:246-249), so the header is never added and Graph falls back to its **HTML default**. `get_email` is the lone read path not opting into the text conversion the codebase already relies on.

**Authoritative confirmation:** Microsoft Graph "Get message" docs list `Prefer: outlook.body-content-type` (`"text"|"html"`, default HTML) for the single-message GET, with Example 3 returning `body.contentType: "text"`. (https://learn.microsoft.com/en-us/graph/api/message-get)

## Approach

Let callers request the body as text, reusing the existing header mechanism — **no new dependency, no client-side HTML stripping** (Graph does a higher-quality conversion server-side, and preserves the *full* body text, unlike the truncated `bodyPreview`).

## Actor capabilities

- **`graph.request()`** can be told to request a text body, setting the `Prefer: outlook.body-content-type="text"` header explicitly (today it only infers it from `$search`/`$select`).
- **`get_email`** caller can choose `body_format="text"` (markup-free, full content) or `"html"` (raw stored HTML, for formatting inspection).
- **Janet's triage agent** receives email bodies as text — same content, markup-free — cutting per-email tokens by the measured 24-84%.
- **Debugging caller** (verify a reply's bold/italic) can still get raw HTML via `body_format="html"`.

## Architecture

1. `graph.request(...)` gains `prefer_body_text: bool = False`. When `True`, set `headers["Prefer"] = 'outlook.body-content-type="text"'`. Keep the existing `$search`/`$select` auto-triggers (they already cover their call sites). Single new branch; no change to the returned field set (we do **not** add `$select`, so `get_email` keeps returning all default fields).
2. `get_email(...)` gains `body_format: Literal["text", "html"] = "text"` and passes `prefer_body_text=(body_format == "text")`.
3. No client-side parsing. Graph returns `body.contentType: "text"` with full text content; the existing `body_max_length` truncation block is unchanged.

## Key decision — default `"text"` (DECIDED 2026-06-23)

**Default `body_format="text"`.** This is an LLM-facing tool; HTML markup is waste for *every* consumer, and a text default needs **no triage-prompt change** (the agent gets text automatically). The only behavior change is for the rare "inspect raw stored HTML" case (CLAUDE.md line 231), which opts in with `body_format="html"`.

**Accepted tradeoff:** this changes `get_email`'s default output for all consumers (a backwards-compat-relevant change in a public shared tool). The CLAUDE.md note (line ~231) must be updated to document the new default + the `"html"` opt-out. (Conservative alternative — default `"html"` + a `dispatcher.py:548` prompt tweak — was considered and rejected in favor of the better LLM-facing default.)

## Do-no-harm boundary

- Strips **markup only** — Graph's text conversion preserves the full message text, links inline, sender/subject/recipients/dates/attachments (separate fields, untouched). Tom's "work email needs full context" requirement holds.
- Out of scope (deferred, lossy on content Tom values): quoted-thread/signature trimming.
- gmail path is separate (already returns plaintext; its truncation/"cut off" issue is a distinct quality bug, not this lever).

## Assumptions

| # | Assumption | Risk if wrong | Validation | Status |
|---|---|---|---|---|
| 1 | `Prefer: outlook.body-content-type="text"` is honored on single-message GET `/me/messages/{id}` | Lever does nothing | Graph docs Example 3 (single-message GET) | **VERIFIED (docs)** |
| 2 | The header string + mechanism already work in this codebase | Rework needed | graph.py:28,30 already set it; `list_emails`/`search_emails` return text empirically | **VERIFIED (source + live)** |
| 3 | `get_email` returns HTML solely because it sends no `$select`/`$search` | Wrong fix | tools.py:246-249 (`params={}`); live `get_email` returned `contentType:html` | **VERIFIED (source + live)** |
| 4 | Setting the header (without `$select`) returns the full body text + all default fields | Field loss / partial body | Live `get_email` with `body_format="text"` after change returns text body + all fields + `Preference-Applied` | **PENDING (post-impl live check)** |
| 5 | FastMCP surfaces the new `body_format` param in the tool schema automatically | Param unusable by agent | Existing decorated-tool params surface; confirm via tool-schema test | PENDING |

## Testing

- Unit (mock `graph.request`): `body_format="text"` → request made with `prefer_body_text=True`; `"html"` → `False`; default → `"text"`.
- Unit (mock at HTTP layer / `graph.request` header assembly): `prefer_body_text=True` sets the exact `Prefer` header; `False` does not.
- Regression: existing `get_email` tests (test_integration.py:131, test_x500_resolution.py:489) still pass; x500 rewrite path unaffected.
- Live verification (Assumption 4): one real `get_email(body_format="text")` returns `body.contentType=="text"` + `Preference-Applied` header.
- Gates: `.venv/bin/python -m pytest tests/ -v`, `ruff check`, `ruff format --check`, `pyright src/ tests/`.

## Out of scope

Quoted-thread trimming; gmail changes; any personal-assistant change beyond (if default stays `"html"`) the one-line `dispatcher.py:548` prompt tweak.
