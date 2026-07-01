# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

Microsoft 365 MCP server — provides Outlook email, calendar, OneDrive, and contacts tools via the Model Context Protocol.

Forked from [elyxlz/microsoft-mcp](https://github.com/elyxlz/microsoft-mcp).

## Architecture

- **Framework:** FastMCP 2.8.0
- **Auth:** MSAL device code flow, token cached at configurable path (default: `~/.microsoft_mcp_token_cache.json`)
- **API:** Microsoft Graph API via httpx

## Development Commands

```bash
# Install dependencies
uv sync --all-extras

# Run tests (MUST use venv python to get pinned FastMCP 2.8.0)
.venv/bin/python -m pytest tests/ -v

# Linting
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Type checking
uv run pyright src/
```

## Quality Gates

Pre-commit hooks and CI both run `ruff check`, `ruff format --check`, and `pyright`. Install hooks once after cloning:

```bash
pre-commit install
```

**Scope:** all three gates (`ruff check`, `ruff format --check`, `pyright`) run on `src/` AND `tests/` in both pre-commit and CI.

CI workflow is `.github/workflows/quality.yml`.

## Graph API Lessons

- **`/me/calendarView`** returns individual recurring event instances; **`/me/events`** only returns series masters. Use calendarView for listing events.
- **`/search/query` for events** is unreliable: indexing delays (new events not found for minutes/hours), misses short subjects, returns series masters not instances. Removed `search_events` tool entirely — `list_events` (calendarView) is more reliable.
- **MSAL silent token acquisition with `account=None`** falls back to device code flow instead of failing — causes 15-minute hangs. Must validate account_id matches a cached account BEFORE calling `acquire_token_silent()`.
- **Token refresh:** MSAL refresh tokens expire after 90 days of inactivity. Re-auth requires running `authenticate.py` interactively.
- **Per-call container spawn (auth.py:34-39).** mcp-gateway spawns a fresh microsoft-mcp container per tool call, all mounting the shared token-cache volume. **In-memory caches are dead-on-arrival.** Persistent state must live in the volume — alongside the MSAL token cache or as a sibling file. Reuse `auth._atomic_write` for atomic writes (multi-writer safe; documented from pa-20x9 incident on 2026-04-16). Reference: `address_resolution.py`'s shared-volume cache for X.500 DN→SMTP resolution (pa-jsa6).
- **`graph.request` auto-injects advanced-query headers (graph.py:36-42).** When the request's params contain `$search`, `contains(`, or `/any(`, the layer automatically adds `ConsistencyLevel: eventual` header and `$count=true` query param. Don't write a wrapper helper that duplicates this — it'll diverge. New tools using these query patterns just call `graph.request` directly.
- **`search_emails` has TWO structurally distinct branches.** With `folder` set, uses `graph.request_paginated` against `/me/mailFolders/{folder}/messages`. Without folder, uses `graph.search_query` against `/search/query`. Post-processing hooks (X.500 walker, address normalization, etc.) must be applied to BOTH branches independently. Easy to miss when reading the function casually — the spec-review subagent caught this gap during pa-jsa6.
- **`graph.search_query` yields unwrapped `resource` dicts (graph.py:391).** It walks `hitsContainers[].hits[].resource` internally and yields the inner message. Same shape as `list_emails` output — no envelope unwrapping needed at call sites.
- **OData and KQL escape conventions in `$filter` / `$search` interpolation.** OData literal-string requires single quotes doubled (`"' "''" `) — see `address_resolution._odata_escape`. KQL `$search` phrase literals require double quotes backslash-escaped — see `tools._kql_escape_quotes`. Both helpers are one-liners; use them at every interpolation site, even when source data shouldn't contain the special character (defensive, prevents 400s on edge inputs).
- **`graph.request(json=...)` content-type selection must use `is not None`, not truthiness** (pa-69fc, 2026-05-18). A `json={}` body is falsy-truthy in Python — `if json:` treats it as absent, sets `Content-Type: application/octet-stream` while httpx still serializes the JSON body. Graph rejects the resulting mismatch as malformed. `delete_event(send_cancellation=true)` was the only caller passing `json={}` (to `POST /me/events/{id}/cancel`); the cancellation path silently failed for two days. Fix in `graph.py:33` is `is not None`. Regression test in `tests/test_graph_content_type.py` covers all three branches.
- **`get_email` now defaults to plain-text body (`body_format="text"`)** (pa-l24r.2, 2026-06-23). Graph's `Prefer: outlook.body-content-type="text"` header is sent by default, so `body.contentType` in the response is `"text"` — markup-free, token-efficient. Pass `body_format="html"` to get the raw stored HTML (e.g. for formatting inspection). The `$search`/`$select` auto-triggers in `graph.request` are unchanged; `prefer_body_text=True` is a new explicit override added alongside them.
- **Any `/me/mailFolders` or `/childFolders` listing must use `graph.request_paginated`, never `graph.request`** (mcp-fk1, 2026-07-01). The non-paginated call only returns Graph's first page (~10 folders); newly-created folders sort last in Graph's default order, so folder-name resolution silently missed them past page 1. `_resolve_folder_id` and `move_email` (now deduped to call it) both fixed. Regression tests in `tests/test_folder_pagination.py`.
