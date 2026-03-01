# Microsoft 365 MCP Server Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deploy a Microsoft 365 MCP server (email, calendar, files, contacts) accessible from Claude Code, Desktop, and Cowork via the existing mcp-gateway.

**Architecture:** Fork `elyxlz/microsoft-mcp` (Python, FastMCP, MIT), rename all 35 tools with `ms365_` prefix, make token cache configurable, Dockerize following the fabric-mcp pattern, and register with mcp-gateway at `mcp.inconceivablelabs.com/mcp`.

**Tech Stack:** Python 3.12, FastMCP, MSAL, httpx, Docker, mcp-gateway

```
Claude Code ─┐
Desktop ─────┼─→ mcp-gateway (port 8811) ─→ microsoft-mcp container ─→ Graph API
Cowork ──────┘
```

---

## Assumptions

| Assumption | Basis | Verification |
|-----------|-------|--------------|
| Upstream tests pass without real credentials | Common for MCP projects | Task 1: clone and attempt `pytest` |
| `update_email` passes arbitrary fields to Graph PATCH | FastMCP tool pattern | Task 4: read `tools.py` and `graph.py` |
| Token cache path can be made configurable without breaking MSAL | MSAL reads/writes via helper functions | Task 2: modify `_read_cache`/`_write_cache` |
| Gateway catalog supports `volumes` field for container mounts | Gateway docs mention it, `--long-lived` flag exists | Task 7: test with actual gateway |
| `@mcp.tool(name=...)` parameter works in fastmcp>=2.8.0 | Standard FastMCP API | Task 3: verify in FastMCP docs/source |

---

### Task 1: Clone Upstream and Verify Baseline

**Files:**
- Create: `projects/microsoft-mcp/` (clone target — flatten, don't nest)

**Step 1: Clone the repo into projects/microsoft-mcp**

The `docs/plans/` directory already exists. Clone into a temp location, then move source files into the project directory (preserving our `docs/` folder).

```bash
cd /workspaces/claude-remote/projects
git clone https://github.com/elyxlz/microsoft-mcp.git microsoft-mcp-upstream
# Move upstream files into our project dir, preserving our docs/
cp -r microsoft-mcp-upstream/src microsoft-mcp/
cp -r microsoft-mcp-upstream/tests microsoft-mcp/
cp microsoft-mcp-upstream/pyproject.toml microsoft-mcp/
cp microsoft-mcp-upstream/uv.lock microsoft-mcp/
cp microsoft-mcp-upstream/.python-version microsoft-mcp/
cp microsoft-mcp-upstream/authenticate.py microsoft-mcp/
cp microsoft-mcp-upstream/.gitignore microsoft-mcp/
rm -rf microsoft-mcp-upstream
```

**Step 2: Install dependencies**

```bash
cd /workspaces/claude-remote/projects/microsoft-mcp
uv sync
```

Expected: dependencies install successfully.

**Step 3: Run tests to check baseline**

```bash
cd /workspaces/claude-remote/projects/microsoft-mcp
uv run pytest tests/ -v 2>&1 | head -50
```

Expected: tests either pass or skip (integration tests may require credentials). Note the result — we just need to know the baseline.

**Step 4: Commit baseline**

```bash
cd /workspaces/claude-remote/projects/microsoft-mcp
git init
git add -A
git commit -m "feat: import elyxlz/microsoft-mcp upstream source"
```

---

### Task 2: Make Token Cache Path Configurable

The upstream hardcodes the token cache to `~/.microsoft_mcp_token_cache.json`. For Docker deployment, we need it configurable via environment variable.

**Files:**
- Modify: `src/microsoft_mcp/auth.py`
- Create: `tests/test_auth_cache_path.py`

**Step 1: Write the failing test**

```python
# tests/test_auth_cache_path.py
import os
import pathlib
from unittest.mock import patch


def test_cache_path_defaults_to_home_directory():
    """Without env var, cache path should be ~/.microsoft_mcp_token_cache.json"""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MICROSOFT_MCP_TOKEN_CACHE", None)
        # Re-import to pick up env change
        import importlib
        from microsoft_mcp import auth
        importlib.reload(auth)
        expected = pathlib.Path.home() / ".microsoft_mcp_token_cache.json"
        assert auth.CACHE_FILE == expected


def test_cache_path_from_env_var():
    """MICROSOFT_MCP_TOKEN_CACHE env var should override default path"""
    custom_path = "/data/token_cache.json"
    with patch.dict(os.environ, {"MICROSOFT_MCP_TOKEN_CACHE": custom_path}):
        import importlib
        from microsoft_mcp import auth
        importlib.reload(auth)
        assert auth.CACHE_FILE == pathlib.Path(custom_path)
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_auth_cache_path.py -v
```

Expected: FAIL — `CACHE_FILE` is currently hardcoded, doesn't read env var.

**Step 3: Write minimal implementation**

In `src/microsoft_mcp/auth.py`, change the `CACHE_FILE` line from:

```python
CACHE_FILE = pl.Path.home() / ".microsoft_mcp_token_cache.json"
```

to:

```python
CACHE_FILE = pl.Path(
    os.getenv("MICROSOFT_MCP_TOKEN_CACHE", str(pl.Path.home() / ".microsoft_mcp_token_cache.json"))
)
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_auth_cache_path.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add src/microsoft_mcp/auth.py tests/test_auth_cache_path.py
git commit -m "feat: make token cache path configurable via MICROSOFT_MCP_TOKEN_CACHE env var"
```

---

### Task 3: Rename All Tools with ms365_ Prefix

All 35 tools in `tools.py` need the `ms365_` prefix so they're distinguishable in the shared gateway namespace alongside fabric and capacities tools.

**Files:**
- Modify: `src/microsoft_mcp/tools.py`
- Create: `tests/test_tool_names.py`

**Step 1: Write the failing test**

```python
# tests/test_tool_names.py
from microsoft_mcp.tools import mcp


def test_all_tools_have_ms365_prefix():
    """Every registered tool must start with ms365_"""
    tools = mcp._tool_manager.tools
    for name in tools:
        assert name.startswith("ms365_"), f"Tool '{name}' missing ms365_ prefix"


def test_expected_email_tools_exist():
    """Core email tools must be registered"""
    tools = mcp._tool_manager.tools
    expected = [
        "ms365_list_emails",
        "ms365_get_email",
        "ms365_search_emails",
        "ms365_reply_to_email",
        "ms365_move_email",
        "ms365_update_email",
    ]
    for name in expected:
        assert name in tools, f"Expected tool '{name}' not found"
```

Note: `mcp._tool_manager.tools` is the internal FastMCP tool registry. If this doesn't work, check FastMCP docs for the correct way to list registered tools (may be `mcp.list_tools()` or similar). Adjust the test accordingly.

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_tool_names.py -v
```

Expected: FAIL — tools currently have no prefix.

**Step 3: Rename all tools**

In `src/microsoft_mcp/tools.py`, add `name="ms365_..."` to every `@mcp.tool` decorator. The pattern is:

```python
# Before:
@mcp.tool
def list_emails(account_id: str, ...) -> ...:

# After:
@mcp.tool(name="ms365_list_emails")
def list_emails(account_id: str, ...) -> ...:
```

Also update the FastMCP instance name:

```python
# Before:
mcp = FastMCP("microsoft-mcp")

# After:
mcp = FastMCP("microsoft-365")
```

Complete rename list (old function name → new tool name):

| Function | Tool Name |
|----------|-----------|
| `list_accounts` | `ms365_list_accounts` |
| `authenticate_account` | `ms365_authenticate_account` |
| `complete_authentication` | `ms365_complete_authentication` |
| `list_emails` | `ms365_list_emails` |
| `get_email` | `ms365_get_email` |
| `create_email_draft` | `ms365_create_email_draft` |
| `send_email` | `ms365_send_email` |
| `update_email` | `ms365_update_email` |
| `delete_email` | `ms365_delete_email` |
| `move_email` | `ms365_move_email` |
| `reply_to_email` | `ms365_reply_to_email` |
| `reply_all_email` | `ms365_reply_all_email` |
| `list_events` | `ms365_list_events` |
| `get_event` | `ms365_get_event` |
| `create_event` | `ms365_create_event` |
| `update_event` | `ms365_update_event` |
| `delete_event` | `ms365_delete_event` |
| `respond_event` | `ms365_respond_event` |
| `check_availability` | `ms365_check_availability` |
| `list_contacts` | `ms365_list_contacts` |
| `get_contact` | `ms365_get_contact` |
| `create_contact` | `ms365_create_contact` |
| `update_contact` | `ms365_update_contact` |
| `delete_contact` | `ms365_delete_contact` |
| `list_files` | `ms365_list_files` |
| `get_file` | `ms365_get_file` |
| `create_file` | `ms365_create_file` |
| `update_file` | `ms365_update_file` |
| `delete_file` | `ms365_delete_file` |
| `get_attachment` | `ms365_get_attachment` |
| `search_files` | `ms365_search_files` |
| `search_emails` | `ms365_search_emails` |
| `search_events` | `ms365_search_events` |
| `search_contacts` | `ms365_search_contacts` |
| `unified_search` | `ms365_unified_search` |

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_tool_names.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add src/microsoft_mcp/tools.py tests/test_tool_names.py
git commit -m "feat: rename all tools with ms365_ prefix for gateway disambiguation"
```

---

### Task 4: Verify and Extend Categories Support in update_email

The primary use case includes categorizing emails. The Graph API supports `PATCH /me/messages/{id}` with `{"categories": ["Category Name"]}`. We need to verify the existing `update_email` tool passes this through.

**Files:**
- Modify (if needed): `src/microsoft_mcp/tools.py` — the `update_email` function
- Modify (if needed): `src/microsoft_mcp/graph.py` — the underlying PATCH call

**Step 1: Read the update_email implementation**

Read `src/microsoft_mcp/tools.py` and find the `update_email` function. Check its parameters — does it accept a `categories` argument?

Read `src/microsoft_mcp/graph.py` and find the method it calls. Does the PATCH call forward arbitrary fields, or only specific ones (like `isRead`)?

**Step 2: If categories already supported → skip to commit**

If `update_email` already accepts and forwards a `categories: list[str]` parameter, no changes needed.

**Step 3: If categories NOT supported → add it**

Add a `categories` parameter to `update_email`:

```python
@mcp.tool(name="ms365_update_email")
def update_email(
    account_id: str,
    message_id: str,
    is_read: bool | None = None,
    categories: list[str] | None = None,
) -> dict:
    """Update email properties (read/unread status, categories)."""
    body = {}
    if is_read is not None:
        body["isRead"] = is_read
    if categories is not None:
        body["categories"] = categories
    # ... existing PATCH call with body
```

The Graph API endpoint is `PATCH /me/messages/{message_id}` with the JSON body.

**Step 4: Commit**

```bash
git add -A
git commit -m "feat: ensure update_email supports categories via Graph API PATCH"
```

---

### Task 5: Create Dockerfile

Follow the fabric-mcp Dockerfile pattern exactly.

**Files:**
- Create: `Dockerfile`

**Step 1: Write the Dockerfile**

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y dumb-init && rm -rf /var/lib/apt/lists/*

RUN adduser --disabled-password --gecos "" mcpuser

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project

COPY src/ src/
COPY authenticate.py ./
RUN uv sync --no-dev --frozen

RUN mkdir -p /data && chown -R mcpuser:mcpuser /app /data

ENV MICROSOFT_MCP_TOKEN_CACHE=/data/token_cache.json

USER mcpuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD pgrep -f "python.*microsoft_mcp" || exit 1

ENTRYPOINT ["dumb-init", "--"]
CMD [".venv/bin/python", "-m", "microsoft_mcp"]
```

Key differences from fabric-mcp:
- Copies `authenticate.py` for interactive auth in temp containers
- Creates `/data` directory for token cache volume mount
- Sets `MICROSOFT_MCP_TOKEN_CACHE` to `/data/token_cache.json`

**Step 2: Verify server.py has __main__ support**

Check that `src/microsoft_mcp/__main__.py` exists or that `server.py` is the entry point for `python -m microsoft_mcp`. If `__main__.py` doesn't exist, create it:

```python
# src/microsoft_mcp/__main__.py
from .server import main

main()
```

**Step 3: Commit**

```bash
git add Dockerfile
git add src/microsoft_mcp/__main__.py  # if created
git commit -m "feat: add Dockerfile following fabric-mcp pattern"
```

---

### Task 6: Build and Test Docker Image Locally

**Step 1: Build the image**

```bash
cd /workspaces/claude-remote/projects/microsoft-mcp
docker build -t microsoft-mcp:local .
```

Expected: successful build.

**Step 2: Verify the server starts (will fail on missing CLIENT_ID — that's expected)**

```bash
docker run --rm \
  -e MICROSOFT_MCP_CLIENT_ID=test \
  microsoft-mcp:local \
  .venv/bin/python -c "from microsoft_mcp.tools import mcp; print('Tools:', len(mcp._tool_manager.tools))"
```

Expected: prints tool count (35). This confirms the package is importable inside the container.

**Step 3: Tag for gateway**

```bash
docker tag microsoft-mcp:local ghcr.io/inconceivablelabs/microsoft-mcp:latest
```

**Step 4: Commit (no code changes, just verify)**

No commit needed — this task is verification only.

---

### Task 7: Initial Authentication (Interactive — Tom Does This)

This step requires interactive browser access. Tom runs these commands.

**Step 1: Create the token volume**

```bash
docker volume create microsoft-mcp-tokens
```

**Step 2: Run authenticate.py interactively**

```bash
docker run -it --rm \
  -v microsoft-mcp-tokens:/data \
  -e MICROSOFT_MCP_CLIENT_ID="$(rbw get 'Microsoft MCP Client ID')" \
  -e MICROSOFT_MCP_TOKEN_CACHE=/data/token_cache.json \
  microsoft-mcp:local \
  .venv/bin/python authenticate.py
```

Expected: prints a device code URL + code. Tom opens the URL in a browser, enters the code, signs in. Token cache is written to the volume.

**Step 3: Verify token was saved**

```bash
docker run --rm \
  -v microsoft-mcp-tokens:/data \
  microsoft-mcp:local \
  ls -la /data/
```

Expected: `token_cache.json` exists with non-zero size.

---

### Task 8: Gateway Integration (Windows Host — Tom Does This)

All gateway config files live on the Windows host. Tom edits these directly.

**Step 1: Add to catalog**

Edit `C:\Users\tboot\.docker\mcp\custom-local-catalog.yaml`. Add under `registry:` (sibling of `fabric-mcp-server`):

```yaml
  microsoft-mcp-server:
    description: Microsoft 365 integration (Outlook, Calendar, OneDrive, Contacts) via Graph API
    title: Microsoft 365 MCP Server
    type: server
    dateAdded: "2026-03-01T00:00:00Z"
    image: ghcr.io/inconceivablelabs/microsoft-mcp:latest
    ref: ""
    source: https://github.com/elyxlz/microsoft-mcp
    upstream: https://github.com/elyxlz/microsoft-mcp
    icon: https://avatars.githubusercontent.com/u/inconceivablelabs
    env:
      - name: MICROSOFT_MCP_CLIENT_ID
        value: <value>
      - name: MICROSOFT_MCP_TOKEN_CACHE
        value: /data/token_cache.json
    tools:
      - name: ms365_list_accounts
      - name: ms365_authenticate_account
      - name: ms365_complete_authentication
      - name: ms365_list_emails
      - name: ms365_get_email
      - name: ms365_create_email_draft
      - name: ms365_send_email
      - name: ms365_update_email
      - name: ms365_delete_email
      - name: ms365_move_email
      - name: ms365_reply_to_email
      - name: ms365_reply_all_email
      - name: ms365_list_events
      - name: ms365_get_event
      - name: ms365_create_event
      - name: ms365_update_event
      - name: ms365_delete_event
      - name: ms365_respond_event
      - name: ms365_check_availability
      - name: ms365_list_contacts
      - name: ms365_get_contact
      - name: ms365_create_contact
      - name: ms365_update_contact
      - name: ms365_delete_contact
      - name: ms365_list_files
      - name: ms365_get_file
      - name: ms365_create_file
      - name: ms365_update_file
      - name: ms365_delete_file
      - name: ms365_get_attachment
      - name: ms365_search_files
      - name: ms365_search_emails
      - name: ms365_search_events
      - name: ms365_search_contacts
      - name: ms365_unified_search
    prompts: 0
    resources: {}
    metadata:
      category: productivity
      tags: [microsoft, outlook, email, calendar, onedrive]
      license: MIT
      owner: inconceivablelabs
```

**Step 2: Add secrets to config**

Edit `C:\Users\tboot\.docker\mcp\config.yaml`. Add under `servers:`:

```yaml
  microsoft-mcp-server:
    env:
      MICROSOFT_MCP_CLIENT_ID: "<from rbw: 'Microsoft MCP Client ID'>"
      MICROSOFT_MCP_TOKEN_CACHE: /data/token_cache.json
```

**Step 3: Add volume mount**

The gateway catalog may support a `volumes` field on server entries. If so, add to the catalog entry:

```yaml
    volumes:
      - "microsoft-mcp-tokens:/data"
```

If the catalog doesn't support volumes, the volume mount needs to go in `docker-compose.yml` or we need to pass it through the gateway's Docker socket configuration. **This is assumption #4 — verify at implementation time.**

Alternative if volumes aren't supported: use `--long-lived` flag on the gateway and run the auth step inside the same container the gateway spawns.

**Step 4: Update compose file**

Edit `C:\Users\tboot\mcp-gateway\docker-compose.yml`. Add `microsoft-mcp-server` to the `--servers` list:

```
--servers=capacities-mcp-server,private-journal,fabric-mcp-server,microsoft-mcp-server
```

If needed for volume support, also add to the compose `volumes:` section:

```yaml
volumes:
  microsoft-mcp-tokens:
    external: true
```

**Step 5: Restart the gateway**

```powershell
cd C:\Users\tboot\mcp-gateway
docker compose down
docker compose up -d
```

---

### Task 9: End-to-End Verification

**Step 1: Verify tools are visible**

From Claude Code, search for ms365 tools. They should appear as deferred tools in the MCP gateway.

**Step 2: Test read operations**

Call `ms365_list_emails` — should return recent emails from the authenticated account.

Call `ms365_search_emails` with a known subject — should return matching messages.

**Step 3: Test write operations**

Call `ms365_update_email` on a test message with `categories: ["Test Category"]` — should succeed.

Call `ms365_move_email` to move a test message to a subfolder — should succeed.

Call `ms365_create_email_draft` with a test subject/body — should create a draft visible in Outlook.

**Step 4: Test from Claude Desktop**

Repeat a simple read test from Claude Desktop to verify the gateway proxy works end-to-end.

---

## Risks

- **Token expiry**: MSAL refresh tokens expire after 90 days of inactivity. Mitigated by regular use. Re-auth requires re-running Task 7.
- **Community repo maintenance**: 39 stars, small project. Mitigated by forking — we control our copy and can pull upstream changes selectively.
- **Gateway volume support**: If the catalog doesn't support the `volumes` field, we'll need an alternative strategy for token persistence. Fallback: bind-mount from the host, or use `--long-lived` containers.
- **Rate limiting**: Graph API has per-user throttling. The upstream already handles 429 retries with `Retry-After`. Unlikely to hit limits with manual Claude usage.

## Out of Scope

- CI/CD for the Docker image (manual builds for now)
- Automated token refresh pipeline (manual re-auth is fine for single-user)
- Pruning unused tools (full suite costs nothing)
- GitHub repo push (local only initially, push when stable)
