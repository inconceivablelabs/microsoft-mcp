# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

Microsoft 365 MCP server — provides Outlook email, calendar, OneDrive, and contacts tools via the Model Context Protocol.

Forked from [elyxlz/microsoft-mcp](https://github.com/elyxlz/microsoft-mcp).

## Architecture

- **Framework:** FastMCP (venv-pinned, currently 3.4.7 via uv.lock — verify with `.venv/bin/python -c "import fastmcp; print(fastmcp.__version__)"`)
- **Auth:** MSAL device code flow, token cached at configurable path (default: `~/.microsoft_mcp_token_cache.json`)
- **API:** Microsoft Graph API via httpx

## Development Commands

```bash
# Install dependencies
uv sync --all-extras

# Run tests (MUST use venv python to get the pinned FastMCP, currently 3.4.7)
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

## FastMCP 3.x

Migrated 2.14.2 → 3.4.7 on 2026-08-17 (mcp-m4e). `src/` needed zero changes; only the test suite touched moved internals.

- **`@mcp.tool(name=...)` returns the PLAIN FUNCTION, not a `FunctionTool`.** The 2.x `_tool.fn` unwrap idiom is gone — `from microsoft_mcp.tools import list_emails` gives the callable directly. Don't reintroduce `.fn` on a decorated tool; it fails at import.
- **`mcp._tool_manager` was removed.** The registry is reachable only via `await mcp.list_tools()` (returns `Sequence[FunctionTool]`) or `await mcp.get_tool(name)` — both **async**. Sync test modules bootstrap it once at import with `asyncio.run`; see `tests/test_tool_names.py`. `FunctionTool` itself survives and still carries `.fn` and `.parameters` unchanged — only how you *reach* a tool moved.
- **Tool schemas gained fields.** Per-parameter `description` (harvested from docstring `Args:` blocks) and `additionalProperties: false`. Additive only — types, `required`, enums and defaults are unchanged.
- **`tools/call` responses carry `_meta.fastmcp.wrap_result` — additive, NOT a payload-shape change.** Resolved by diffing both versions' source (2026-08-17). The `x-fastmcp-wrap-result` mechanism and the resulting `structuredContent` shape are IDENTICAL in 2.14.2 (`tools/tool.py:412-416`) and 3.4.7 (`tools/base.py:364-368`); 3.x merely adds a sibling `meta={"fastmcp": {"wrap_result": True}}` line announcing it, and only when wrapping actually occurred. A consumer reading `content` or `structuredContent` sees byte-identical data — `_meta` only affects a consumer that looks for it (3.x's own client uses it to unwrap, `client/mixins/tools.py:433`). Low risk for Janet; still worth one live call at the deploy gate.

## Repo & Deploy Gotchas

- **Merging to `main` publishes, but does NOT deploy — an explicit `docker pull` is required** (mcp-m4e, 2026-07-26). `docker-publish.yml` republishes `ghcr.io/inconceivablelabs/microsoft-mcp:latest` on push to `main`, and the gateway catalog pins `:latest` with per-call container spawn — but there is **no pull policy**, so the gateway keeps serving its locally cached image indefinitely. A merged security fix sat inert until `docker pull ghcr.io/inconceivablelabs/microsoft-mcp:latest` was run by hand. Verify the deployed digest (`docker images --digests`), not the merge. Because children are spawned per call, no gateway restart is needed and Janet is not interrupted. Rollback: the prior image survives locally — `docker tag <old-id> ghcr.io/inconceivablelabs/microsoft-mcp:latest`.
- **`docker pull` from the devcontainer fails on the `credsStore` helper** — `error getting credentials - err: exit status 255`. Work around it with an isolated config (`DOCKER_CONFIG=<tmpdir>` containing `{}`); the GHCR package pulls anonymously. Same root cause as the compose-build entry in `bd memories`; do not edit the shared `~/.docker/config.json`.
- **Verify a deploy by READING the file inside the image, not by importing it** (mcp-xby, 2026-08-17). `docker run --entrypoint /app/.venv/bin/python <image> -c "import microsoft_mcp.graph"` produces **no output and never returns** — the `auth`/MSAL import chain hangs, the same class as the `docker exec` heavy-import hangs. Use `--entrypoint sh` and `grep` the source file instead; a file read cannot hang or fail open. Pair it with a **control**: run the same grep against the previous image and require 0 matches, or the check proves nothing. Record the pre-pull digest first (`docker images --digests`) — the old image survives locally, so `docker tag <old-id> ghcr.io/inconceivablelabs/microsoft-mcp:latest` is the rollback.
- **Always pass `-R inconceivablelabs/microsoft-mcp` to `gh`** (mcp-cgn, 2026-07-26). This repo is a fork of `elyxlz/microsoft-mcp`, and bare `gh pr list` / `gh repo view` resolve to the **parent** — silently reporting upstream's PRs and a `master` default branch. Our fork's default is `main`. An unscoped query produced a confidently wrong claim that Dependabot opened no PRs here.

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
- **`attendanceReports` is delegated-reachable — NOT app-only** (mcp-b49, 2026-07-20). `GET /me/onlineMeetings/{id}/attendanceReports` works with the delegated device-flow token (`OnlineMeetingArtifact.Read.All`), unlike the app-only `getAllTranscripts` (mcp-s7p). Fetch per-participant records via the **paginated** `/attendanceReports/{id}/attendanceRecords` child collection, **never `$expand=attendanceRecords`** — a `$expand` of a collection is itself paged by Graph and silently truncates large meetings (mcp-fk1 class). Recurring meetings return one report **per occurrence**. Tools: `list_attendance_reports` / `get_attendance_report`; tests in `tests/test_attendance.py`.
