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
