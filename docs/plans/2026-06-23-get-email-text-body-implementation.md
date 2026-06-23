# Implementation Plan (TDD): `get_email` text-body option

**Design:** `2026-06-23-get-email-text-body-design.md`
**Repo:** microsoft-mcp. **Test cmd:** `.venv/bin/python -m pytest tests/ -v` (pinned FastMCP 2.8.0). **Gates:** `ruff check src/ tests/`, `ruff format --check src/ tests/`, `pyright src/ tests/`.

> **Decided (2026-06-23):** default `body_format="text"`. No personal-assistant prompt change needed (the triage agent gets text automatically). Update microsoft-mcp CLAUDE.md (line ~231) to document the new default + `"html"` opt-out.

Each task: write the failing test first, run it RED, implement, run GREEN, then run the full suite + gates before commit.

## Task 1 — `graph.request()` honors an explicit `prefer_body_text`

**Test (RED):** in `tests/test_graph.py` (or nearest graph-layer test module; create if absent):
- `request("GET", "/me/messages/x", account_id="a", prefer_body_text=True)` → the outgoing request carries header `Prefer: outlook.body-content-type="text"`. (Mock `_client.request`; assert on `headers` kwarg.)
- `prefer_body_text=False` (default) and no `$search`/`$select` → **no** `Prefer` header.
- Existing auto-trigger unchanged: GET with `$select` containing `body` still sets the header (regression).

**Implement (GREEN):** add `prefer_body_text: bool = False` to `request(...)`. In the GET branch, set the `Prefer` header if `prefer_body_text` **or** the existing `$search`/`$select` conditions. Reuse the exact existing header string. No other behavior change.

## Task 2 — `get_email(body_format=...)` wiring

**Test (RED):** in `tests/test_tools.py` (mock `graph.request`, assert call kwargs):
- `get_email(id, acct)` (default) → `graph.request` called with `prefer_body_text=True`.
- `get_email(id, acct, body_format="html")` → `prefer_body_text=False`.
- `get_email(id, acct, body_format="text")` → `prefer_body_text=True`.
- Returned dict / truncation / `include_attachments` behavior unchanged (existing assertions still hold).

**Implement (GREEN):** add `body_format: Literal["text", "html"] = "text"` to `get_email`; pass `prefer_body_text=(body_format == "text")` into `graph.request`. Update docstring. Body-truncation block unchanged.

## Task 3 — tool schema surfaces `body_format`

**Test:** assert `body_format` appears in `get_email`'s FastMCP tool schema with the `text|html` constraint and default. (Extend `tests/test_tool_names.py` or the schema-introspection pattern already used.)

**Implement:** none expected (decorated-tool params surface automatically). If it doesn't surface, adjust the annotation per FastMCP 2.8.0 conventions.

## Task 4 — Regression + live verification

- Full suite green: `.venv/bin/python -m pytest tests/ -v` (esp. test_integration.py:131 `test_get_email`, test_x500_resolution.py:489 x500-rewrite — confirm text-mode bodies don't break x500 rewriting).
- All three gates clean on `src/` AND `tests/`.
- **Live (Assumption 4):** one real `get_email(body_format="text")` against the deployed account → assert `body.contentType == "text"` and response carried `Preference-Applied: outlook.body-content-type="text"`. Capture before/after token sizes on one notification email to confirm the measured reduction reproduces.

## Commit / land

- Conventional commits, one per task where the contract is self-contained (Task 1 and Task 2 may need squashing if the pyright/type contract spans request↔get_email — per microsoft-mcp CLAUDE.md, bidirectional type changes go in one commit).
- Update microsoft-mcp CLAUDE.md note (line ~231): `get_email` now defaults to text; `body_format="html"` for raw HTML inspection.
- Close pa-l24r.2 with the live-verified reduction number; note the gmail "cut off" follow-up stays separate.

## Notes for the implementer
- microsoft-mcp pins FastMCP 2.8.0 in `.venv` — run tests with `.venv/bin/python`, not system python.
- `FunctionTool`-wrapped tools: call the underlying fn via `get_email.fn(...)` in tests if calling the tool object directly fails (precedent: pa get_attachment tests).
- Do NOT add `$select` to `get_email` — it would restrict the returned fields. The header alone drives body format.
