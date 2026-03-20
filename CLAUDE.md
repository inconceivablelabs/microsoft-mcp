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
