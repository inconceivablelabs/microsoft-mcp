# Directory Search + X.500 DN Resolution Implementation Plan

> **For agentic workers:** REQUIRED: Use subagent-driven-development (if subagents available) or executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add upstream MS365 directory access (`search_directory`) and X.500-DN→SMTP resolution to microsoft-mcp, so Janet can find CB-internal people via the GAL and stop seeing X.500 leaks from email tools.

**Architecture:** New tool `search_directory` calls `/me/people` (relevance-ranked) with `/users` `$search` fallback. Separate module `address_resolution.py` provides X.500 detection, batched OR-chained `/users` `$filter` resolution, file-based shared cache (atomic writes via existing `auth._atomic_write`), and a message-walker hook that runs post-fetch in `search_emails`/`list_emails`/`get_email`.

**Tech Stack:** FastMCP 2.8.0, httpx via existing `graph.request`, MSAL via existing `auth.py`, pytest + unittest.mock. No new runtime deps.

**Design doc:** `docs/plans/2026-05-06-directory-search-and-x500-resolution-design.md`

**Beads:** pa-b14f (search_directory), pa-jsa6 (X.500 resolver). Both tracked in personal-assistant beads (cross-repo bug tracking).

---

## File Structure

**Create:**
- `src/microsoft_mcp/address_resolution.py` — X.500 detector, batched resolver, file cache, message-walker
- `tests/test_search_directory.py` — primary path, fallback, normalization, UPN-fallback
- `tests/test_x500_resolution.py` — detector, resolver, cache, walker, integration

**Modify:**
- `src/microsoft_mcp/tools.py` — add `search_directory` tool, update `search_contacts` docstring, integrate `resolve_x500_in_message` into `search_emails`/`list_emails`/`get_email`

No splits or restructures of existing files. Existing patterns followed (FunctionTool `.fn` for tests, `graph.request` for HTTP, `auth._atomic_write` for shared-volume files).

---

## Assumptions

| Assumption | Basis | Verification |
|---|---|---|
| `/me/people` callable under Tom's app's pre-consented `.default` scope | Graph docs list `People.Read` as least-privileged; `.default` returns whatever's pre-consented; not visible from source | First real call in Task B1. If 403 ErrorAccessDenied, surface to Tom to add `People.Read` (delegated) before continuing |
| `/users` `$search` and `$filter=proxyAddresses/any(...)` callable under existing scope; `User.ReadBasic.All` is sufficient (Tom states `User.Read.All` granted, which subsumes it) | Graph docs (user-list); Tom's design statement | First real call in Task B2 (fallback) and Task C1 (resolver). 403 on either surfaces to Tom |
| X.500 DN in `from.emailAddress.address` is byte-identical to the matching `proxyAddresses` `X500:` entry, capitalized prefix verbatim | Microsoft docs: capitalized prefix denotes primary; body preserved from Exchange | Task C1 first run captures real RSVP DN, fetches `/users/{id}/proxyAddresses`, compares verbatim. If case-sensitivity bites, defensive secondary path: `startswith(p,'X500:')` + client-side filter |
| `graph.request` (graph.py:36-42) auto-injects `ConsistencyLevel: eventual` and `$count=true` on `$search` / `contains(` / `/any(` patterns | Read graph.py:36-42 directly during design review | Verified — no design action needed |
| `graph.request` raises `httpx.HTTPStatusError` (subclass of `httpx.HTTPError`) for failed Graph calls; network errors raise `httpx.RequestError` (also a subclass of `httpx.HTTPError`) | Read graph.py:81-98 directly | Verified — `except httpx.HTTPError` is the correct narrowing |
| The shared volume that holds the MSAL token cache (`auth.py:34-39`) is also writable for our resolution cache file at the same parent dir | Read auth.py:34-39 directly during design review | Cache module probes write permission on first read; logs and degrades to no-cache if unavailable. Tested in Task C2 |
| `/me/people` `$search` accepts free-text without field qualification; `/users` `$search` requires `field:value` form with OR uppercase | Graph docs (people-insights-overview, user-list) | Tasks B1 and B2 use these syntaxes; first call validates |

---

## Key Decisions

| Decision | Alternatives considered | Why this choice |
|---|---|---|
| New `search_directory` tool, leave `list_contacts`/`search_contacts` unchanged | (B) modify `search_contacts` to accept `scope` param; (C) rename existing tool | Additive, no breakage on existing callers, clear semantic separation between personal book and org directory |
| `/me/people` primary + `/users` `$search` fallback | (A) `/me/people` only — misses non-correspondents; (C) `/users` only — loses relevance ranking | Tom's `User.Read.All` is granted, removing the friction that previously made fallback unattractive; covers correspondent-and-stranger long tail in one tool |
| Helper-layer X.500 fix (post-fetch on `search_emails`/`list_emails`/`get_email`) | (A) fix only `search_emails`; (C) fix at `graph.py` response-extraction layer | Fixes the actual bug class (any email-listing tool) without bleeding into calendar/contact paths where no bug is reported |
| `/users?$filter=proxyAddresses/any(p:p eq 'X500:<DN>')` for resolution | (A) parse displayName from CN + `/me/people` search — heuristic, brittle on edge cases | With `User.Read.All` granted, deterministic DN lookup via `proxyAddresses` is more correct than name-parsing |
| File-based shared-volume cache, account-keyed JSON | (B) in-memory dict — useless under per-call container spawn (auth.py:34-39); (C) no cache — 25-150s added per triage run | Survives container spawns; reuses existing atomic-write pattern; bounded "extra Graph call" worst case on race |
| OR-chained `any(eq)` for batch resolution, NOT `any(p:p in (...))` | `in` inside `any(...)` is not documented for Graph; `eq` is | Documented syntax avoids speculative path; URL length stays bounded (15-DN cap = ~4KB) |
| Distinguish "no match" (cache forever) from "lookup error" (don't cache) | Cache everything — risk: transient 429s become permanent unresolvable entries | Cleanly separates confirmed-absent from transient-failure; `except httpx.HTTPError` narrows the catch so own-code bugs surface |
| Drop the proposed `graph.advanced_query` helper | Add it as new shared infrastructure | `graph.request` already auto-injects `ConsistencyLevel: eventual` + `$count=true` on `$search`/`contains(`/`/any(` patterns — duplicating it risks divergence |
| Cap batch size at 15 distinct DNs per Graph call | (A) no cap — risk of URL length 414; (B) lower cap (5) — more calls in worst case | 15 × ~250 chars = ~4KB filter, well under 16KB practical URL limit; >15 splits into multiple calls |
| Six tasks instead of eleven | Original plan had 11 tasks; review flagged three commits as ceremonial | Merging detector+resolver, cache+walker, and three integration sites preserves TDD red-green-commit discipline while eliminating commits that don't earn their own `git log` entry |

---

## Phase B: `search_directory` (pa-b14f)

### Task B1: `/me/people` primary path

**Satisfies:** Janet can search the org directory and get relevance-ranked correspondents.

**Files:**
- Modify: `src/microsoft_mcp/tools.py` (add new `@mcp.tool` definition immediately after `search_contacts` at line 1536)
- Test: `tests/test_search_directory.py` (new file)

- [ ] **Step 1: Write the failing test for the primary path.**

```python
# tests/test_search_directory.py
"""Tests for search_directory (pa-b14f).

Two-tier directory search: /me/people relevance-ranked primary,
/users $search fallback. Returns a normalized row shape so callers
don't have to branch on which endpoint produced the row.
"""

from unittest.mock import patch
from microsoft_mcp.tools import search_directory as _search_directory_tool

search_directory = _search_directory_tool.fn


@patch("microsoft_mcp.tools.graph.request")
def test_me_people_primary_returns_normalized_rows(mock_request):
    """When /me/people returns matches, /users is not called and rows are normalized."""
    mock_request.return_value = {
        "value": [
            {
                "displayName": "Brittany Alley",
                "scoredEmailAddresses": [
                    {"address": "balley@caringbridge.org", "relevanceScore": 0.95}
                ],
                "jobTitle": "VP Product",
                "department": "Product",
                "personType": {"class": "Person", "subclass": "OrganizationUser"},
            }
        ]
    }

    rows = search_directory(query="Brittany", account_id="acct-1", limit=5)

    assert mock_request.call_count == 1
    call = mock_request.call_args
    assert call.args[0] == "GET"
    assert call.args[1] == "/me/people"
    assert call.kwargs["params"]["$search"] == '"Brittany"'
    assert call.kwargs["params"]["$top"] == 5
    assert "displayName,scoredEmailAddresses" in call.kwargs["params"]["$select"]

    assert rows == [
        {
            "name": "Brittany Alley",
            "email": "balley@caringbridge.org",
            "job_title": "VP Product",
            "department": "Product",
            "person_type": "OrganizationUser",
            "source": "people",
        }
    ]
```

- [ ] **Step 2: Run test to verify it fails.**

```bash
.venv/bin/python -m pytest tests/test_search_directory.py::test_me_people_primary_returns_normalized_rows -v
```

Expected: FAIL with `ImportError: cannot import name 'search_directory'`.

- [ ] **Step 3: Implement primary path.** Add at end of `tools.py` (immediately after `search_contacts` at line 1536):

```python
def _normalize_people_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a /me/people response row to the search_directory shape."""
    scored = row.get("scoredEmailAddresses") or []
    email = scored[0].get("address") if scored else None
    person_type_obj = row.get("personType") or {}
    return {
        "name": row.get("displayName"),
        "email": email,
        "job_title": row.get("jobTitle"),
        "department": row.get("department"),
        "person_type": person_type_obj.get("subclass"),
        "source": "people",
    }


@mcp.tool(name="search_directory")
def search_directory(
    query: str,
    account_id: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Search the organization directory for people by name or email.

    Returns relevance-ranked correspondents first (via /me/people), falling
    back to full directory search (via /users) for non-correspondents.

    Distinct from search_contacts, which only searches the user's personal
    address book (/me/contacts).
    """
    people_response = graph.request(
        "GET",
        "/me/people",
        account_id,
        params={
            "$search": f'"{query}"',
            "$top": min(limit, 100),
            "$select": "displayName,scoredEmailAddresses,jobTitle,department,personType",
        },
    )
    people_rows = (people_response or {}).get("value", []) if people_response else []
    if people_rows:
        return [_normalize_people_row(r) for r in people_rows]

    # Fallback path implemented in Task B2.
    return []
```

- [ ] **Step 4: Run test to verify it passes.**

```bash
.venv/bin/python -m pytest tests/test_search_directory.py::test_me_people_primary_returns_normalized_rows -v
```

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/microsoft_mcp/tools.py tests/test_search_directory.py
git commit -m "feat(directory): add search_directory tool with /me/people primary path (pa-b14f)"
```

---

### Task B2: `/users` `$search` fallback + `search_contacts` docstring

**Satisfies:** Janet can find directory members she has not corresponded with; can disambiguate `search_contacts` from `search_directory` without reading source.

**Files:**
- Modify: `src/microsoft_mcp/tools.py` (extend `search_directory`, add `_normalize_users_row`, update `search_contacts` docstring at line 1526)
- Test: `tests/test_search_directory.py` (add fallback + UPN-fallback tests)

- [ ] **Step 1: Write the failing tests.**

```python
@patch("microsoft_mcp.tools.graph.request")
def test_users_fallback_when_people_returns_empty(mock_request):
    """/users fallback fires when /me/people returns 0 rows."""
    mock_request.side_effect = [
        {"value": []},  # /me/people returns nothing
        {
            "value": [
                {
                    "id": "user-id-1",
                    "displayName": "Casey Kim",
                    "mail": "ckim@caringbridge.org",
                    "userPrincipalName": "ckim@caringbridge.org",
                    "jobTitle": "Engineer",
                    "department": "Eng",
                }
            ]
        },
    ]

    rows = search_directory(query="Casey", account_id="acct-1", limit=10)

    assert mock_request.call_count == 2
    second_call = mock_request.call_args_list[1]
    assert second_call.args[0] == "GET"
    assert second_call.args[1] == "/users"
    assert second_call.kwargs["params"]["$search"] == '"displayName:Casey" OR "mail:Casey"'
    assert "displayName,mail,userPrincipalName" in second_call.kwargs["params"]["$select"]
    assert "id" in second_call.kwargs["params"]["$select"]

    assert rows == [
        {
            "name": "Casey Kim",
            "email": "ckim@caringbridge.org",
            "job_title": "Engineer",
            "department": "Eng",
            "person_type": None,
            "source": "users",
        }
    ]


@patch("microsoft_mcp.tools.graph.request")
def test_users_row_falls_back_to_upn_when_mail_null(mock_request):
    """Service-account / shared-mailbox rows have null mail; fall back to UPN."""
    mock_request.side_effect = [
        {"value": []},
        {
            "value": [
                {
                    "id": "user-id-2",
                    "displayName": "Shared Inbox",
                    "mail": None,
                    "userPrincipalName": "sharedbox@caringbridge.org",
                    "jobTitle": None,
                    "department": None,
                }
            ]
        },
    ]
    rows = search_directory(query="shared", account_id="acct-1", limit=5)
    assert rows[0]["email"] == "sharedbox@caringbridge.org"
```

- [ ] **Step 2: Run tests to verify they fail.**

```bash
.venv/bin/python -m pytest tests/test_search_directory.py -v
```

Expected: the two new tests FAIL (only `/me/people` is called; fallback path returns `[]`).

- [ ] **Step 3: Implement fallback + update `search_contacts` docstring.**

Add the normalizer and replace the `return []` placeholder in `search_directory`:

```python
def _normalize_users_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a /users response row to the search_directory shape."""
    return {
        "name": row.get("displayName"),
        "email": row.get("mail") or row.get("userPrincipalName"),
        "job_title": row.get("jobTitle"),
        "department": row.get("department"),
        "person_type": None,
        "source": "users",
    }


# Inside search_directory, replace `return []` with:
    users_response = graph.request(
        "GET",
        "/users",
        account_id,
        params={
            "$search": f'"displayName:{query}" OR "mail:{query}"',
            "$top": min(limit, 100),
            "$select": "id,displayName,mail,userPrincipalName,jobTitle,department",
        },
    )
    users_rows = (users_response or {}).get("value", []) if users_response else []
    return [_normalize_users_row(r) for r in users_rows]
```

Update `search_contacts` docstring at `tools.py:1526`:

```python
# Before:
    """Search contacts. Uses traditional search since unified_search doesn't support contacts."""

# After:
    """Search the user's PERSONAL address book (/me/contacts) only.

    For the organization directory (CB-internal employees, GAL contacts),
    use search_directory instead. Uses traditional search since
    unified_search doesn't support contacts.
    """
```

- [ ] **Step 4: Run tests to verify they pass.**

```bash
.venv/bin/python -m pytest tests/test_search_directory.py -v
```

Expected: all four tests PASS (including the original primary-path test).

- [ ] **Step 5: Run gates and commit.**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
git add src/microsoft_mcp/tools.py tests/test_search_directory.py
git commit -m "feat(directory): add /users fallback to search_directory; clarify search_contacts scope (pa-b14f)"
```

---

## Phase C: X.500 DN resolution (pa-jsa6)

### Task C1: Detector + batched resolver

**Satisfies:** Helper layer can identify X.500 DNs and resolve them to SMTPs via `/users` `$filter` in batched OR-chained Graph calls.

**Files:**
- Create: `src/microsoft_mcp/address_resolution.py`
- Test: `tests/test_x500_resolution.py` (new file)

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_x500_resolution.py
"""Tests for address_resolution module (pa-jsa6).

X.500 legacy DN detection, batched /users $filter resolution,
file-based shared-volume cache, and message-walker for email tools.
"""

from unittest.mock import patch
from microsoft_mcp.address_resolution import _is_x500_dn, _resolve_dns_via_graph


# --- Detector ---

def test_detector_recognizes_x500_dn():
    assert _is_x500_dn("/O=EXCHANGELABS/OU=EXCHANGE ADMINISTRATIVE GROUP (FYDIBOHF23SPDLT)/CN=RECIPIENTS/CN=hash-TOM BOOTH") is True


def test_detector_rejects_smtp():
    assert _is_x500_dn("tbooth@caringbridge.org") is False


def test_detector_handles_none_and_empty():
    assert _is_x500_dn(None) is False
    assert _is_x500_dn("") is False


def test_detector_rejects_lowercase_o_prefix():
    """Defense in depth: only the documented capitalized /O= form is X.500."""
    assert _is_x500_dn("/o=lowercase/CN=...") is False


# --- Batched resolver ---

@patch("microsoft_mcp.address_resolution.graph.request")
def test_resolver_returns_dn_to_smtp_map(mock_request):
    """One DN → one Graph call → {dn: smtp} map."""
    dn = "/O=EXCHANGELABS/OU=.../CN=hash-TOM BOOTH"
    mock_request.return_value = {
        "value": [
            {
                "id": "user-id-1",
                "mail": "tbooth@caringbridge.org",
                "proxyAddresses": [
                    "SMTP:tbooth@caringbridge.org",
                    f"X500:{dn}",
                ],
            }
        ]
    }

    result = _resolve_dns_via_graph([dn], account_id="acct-1")

    assert result == {dn: "tbooth@caringbridge.org"}
    call = mock_request.call_args
    assert call.args[0] == "GET"
    assert call.args[1] == "/users"
    expected_filter = f"proxyAddresses/any(p:p eq 'X500:{dn}')"
    assert call.kwargs["params"]["$filter"] == expected_filter
    assert "mail,proxyAddresses,id" in call.kwargs["params"]["$select"]


@patch("microsoft_mcp.address_resolution.graph.request")
def test_resolver_returns_none_for_unmatched_dn(mock_request):
    """Unmatched DN gets None in the result map."""
    dn = "/O=UNKNOWN/CN=ghost"
    mock_request.return_value = {"value": []}

    result = _resolve_dns_via_graph([dn], account_id="acct-1")
    assert result == {dn: None}


@patch("microsoft_mcp.address_resolution.graph.request")
def test_resolver_batches_multiple_dns_in_one_call(mock_request):
    """N DNs → single Graph call with OR-chained any(eq) clauses."""
    dn1 = "/O=EXCHANGELABS/CN=user1"
    dn2 = "/O=EXCHANGELABS/CN=user2"
    mock_request.return_value = {
        "value": [
            {"id": "u1", "mail": "user1@cb.org", "proxyAddresses": [f"X500:{dn1}", "SMTP:user1@cb.org"]},
            {"id": "u2", "mail": "user2@cb.org", "proxyAddresses": [f"X500:{dn2}", "SMTP:user2@cb.org"]},
        ]
    }

    result = _resolve_dns_via_graph([dn1, dn2], account_id="acct-1")

    assert mock_request.call_count == 1
    expected_filter = (
        f"proxyAddresses/any(p:p eq 'X500:{dn1}') "
        f"or proxyAddresses/any(p:p eq 'X500:{dn2}')"
    )
    assert mock_request.call_args.kwargs["params"]["$filter"] == expected_filter
    assert result == {dn1: "user1@cb.org", dn2: "user2@cb.org"}


@patch("microsoft_mcp.address_resolution.graph.request")
def test_resolver_caps_batch_at_15_dns(mock_request):
    """20 DNs → 2 Graph calls (15 + 5) to stay under URL-length ceiling."""
    dns = [f"/O=EXCHANGELABS/CN=user{i}" for i in range(20)]
    mock_request.return_value = {"value": []}

    _resolve_dns_via_graph(dns, account_id="acct-1")
    assert mock_request.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail.**

```bash
.venv/bin/python -m pytest tests/test_x500_resolution.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'microsoft_mcp.address_resolution'`.

- [ ] **Step 3: Implement detector + resolver.** Create `src/microsoft_mcp/address_resolution.py`:

```python
"""X.500 legacy DN → SMTP resolution for email-listing tool results.

Graph returns from.emailAddress.address as an X.500 DN
(/O=EXCHANGELABS/...) for certain message types — meeting RSVPs,
NDRs, etc. This module detects those, resolves them to SMTP via
/users $filter on proxyAddresses, caches results in a shared-volume
JSON file, and rewrites message dicts in place.

See docs/plans/2026-05-06-directory-search-and-x500-resolution-design.md.
"""

from typing import Any

from microsoft_mcp import graph

_BATCH_CAP = 15


def _is_x500_dn(address: str | None) -> bool:
    """Return True iff the address is an X.500 legacy DN.

    Detection key: capitalized /O= prefix per Microsoft's documented
    proxyAddresses convention. None / empty / lowercase return False.
    """
    if not address:
        return False
    return address.startswith("/O=")


def _resolve_dns_via_graph(
    dns: list[str], account_id: str
) -> dict[str, str | None]:
    """Batch-resolve X.500 DNs to SMTPs via /users $filter on proxyAddresses.

    Returns {dn: smtp_or_None}. Splits batches >15 DNs into multiple Graph
    calls to stay under URL-length limits. Lookup errors are NOT swallowed
    here — callers (cache layer) decide whether to cache or skip.
    """
    if not dns:
        return {}

    result: dict[str, str | None] = {dn: None for dn in dns}

    for start in range(0, len(dns), _BATCH_CAP):
        batch = dns[start : start + _BATCH_CAP]
        filter_expr = " or ".join(
            f"proxyAddresses/any(p:p eq 'X500:{dn}')" for dn in batch
        )
        response = graph.request(
            "GET",
            "/users",
            account_id,
            params={
                "$filter": filter_expr,
                "$select": "mail,proxyAddresses,id,displayName",
            },
        )
        users = (response or {}).get("value", []) if response else []

        for user in users:
            mail = user.get("mail")
            for proxy in user.get("proxyAddresses", []):
                if proxy.startswith("X500:"):
                    proxy_dn = proxy[len("X500:") :]
                    if proxy_dn in result:
                        result[proxy_dn] = mail

    return result
```

- [ ] **Step 4: Run tests to verify they pass.**

```bash
.venv/bin/python -m pytest tests/test_x500_resolution.py -v
```

Expected: all 8 tests (4 detector + 4 resolver) PASS.

- [ ] **Step 5: Run gates and commit.**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
git add src/microsoft_mcp/address_resolution.py tests/test_x500_resolution.py
git commit -m "feat(x500): add DN detector and batched /users \$filter resolver (pa-jsa6)"
```

---

### Task C2: File cache + message walker

**Satisfies:** Resolution survives the per-call container-spawn model and rewrites X.500 DNs in place across all message email-address fields. Logs on fail-open path so silent-failure isn't a debugging dead end.

**Files:**
- Modify: `src/microsoft_mcp/address_resolution.py` (add cache I/O, public `resolve_dns`, walker)
- Test: `tests/test_x500_resolution.py` (add cache + walker tests)

- [ ] **Step 1: Write the failing tests.**

```python
import json
import logging
import pytest
from microsoft_mcp.address_resolution import (
    resolve_dns,
    resolve_x500_in_message,
    _read_cache,
    _write_cache_atomic,
)


@pytest.fixture
def isolated_cache_file(tmp_path, monkeypatch):
    """Redirect the cache file to a tmp dir for the test.

    Monkeypatches auth.CACHE_FILE so _cache_path() resolves to a tmp
    location — more refactor-stable than monkeypatching _cache_path itself.
    """
    fake_token_cache = tmp_path / "fake_token_cache.json"
    monkeypatch.setattr("microsoft_mcp.auth.CACHE_FILE", fake_token_cache)
    return tmp_path / ".microsoft_mcp_x500_cache.json"


# --- Cache ---

@patch("microsoft_mcp.address_resolution.graph.request")
def test_resolve_dns_uses_cache_on_second_call(mock_request, isolated_cache_file):
    """First call hits Graph; second call with same DN does not."""
    dn = "/O=EXCHANGELABS/CN=user1"
    mock_request.return_value = {
        "value": [
            {"id": "u1", "mail": "u1@cb.org", "proxyAddresses": [f"X500:{dn}", "SMTP:u1@cb.org"]}
        ]
    }

    first = resolve_dns([dn], account_id="acct-1")
    second = resolve_dns([dn], account_id="acct-1")

    assert mock_request.call_count == 1
    assert first == second == {dn: "u1@cb.org"}


@patch("microsoft_mcp.address_resolution.graph.request")
def test_resolve_dns_caches_no_match_as_none(mock_request, isolated_cache_file):
    """Confirmed-absent DNs cache as None and don't re-query."""
    dn = "/O=GHOST/CN=missing"
    mock_request.return_value = {"value": []}

    resolve_dns([dn], account_id="acct-1")
    resolve_dns([dn], account_id="acct-1")

    assert mock_request.call_count == 1
    assert _read_cache().get("acct-1", {}).get(dn) is None


@patch("microsoft_mcp.address_resolution.graph.request")
def test_resolve_dns_does_not_cache_on_http_errors(mock_request, isolated_cache_file, caplog):
    """Transient httpx errors don't write the cache; warning is logged."""
    import httpx
    dn = "/O=EXCHANGELABS/CN=user1"
    mock_request.side_effect = httpx.RequestError("simulated network failure")

    with caplog.at_level(logging.WARNING, logger="microsoft_mcp.address_resolution"):
        result = resolve_dns([dn], account_id="acct-1")

    assert result == {dn: None}
    assert _read_cache() == {}
    assert any("X.500 resolution failed" in rec.message for rec in caplog.records)


@patch("microsoft_mcp.address_resolution.graph.request")
def test_resolve_dns_does_not_swallow_own_code_bugs(mock_request, isolated_cache_file):
    """Narrow except — KeyError/TypeError from response parsing surface, not silent."""
    mock_request.side_effect = KeyError("attribute lookup bug")
    with pytest.raises(KeyError):
        resolve_dns(["/O=EXCHANGELABS/CN=x"], account_id="acct-1")


@patch("microsoft_mcp.address_resolution.graph.request")
def test_resolve_dns_only_queries_uncached(mock_request, isolated_cache_file):
    """Mixed batch: cached DN skipped, uncached DN queried."""
    dn1 = "/O=EXCHANGELABS/CN=cached"
    dn2 = "/O=EXCHANGELABS/CN=fresh"

    _write_cache_atomic({"acct-1": {dn1: "cached@cb.org"}})

    mock_request.return_value = {
        "value": [
            {"id": "u2", "mail": "fresh@cb.org", "proxyAddresses": [f"X500:{dn2}", "SMTP:fresh@cb.org"]}
        ]
    }

    result = resolve_dns([dn1, dn2], account_id="acct-1")

    assert mock_request.call_count == 1
    fresh_call_filter = mock_request.call_args.kwargs["params"]["$filter"]
    assert dn1 not in fresh_call_filter
    assert dn2 in fresh_call_filter
    assert result == {dn1: "cached@cb.org", dn2: "fresh@cb.org"}


@patch("microsoft_mcp.address_resolution.graph.request")
def test_cache_rebuilds_after_lost_write(mock_request, isolated_cache_file):
    """Race: a lost write means next call re-queries and re-populates cache cleanly."""
    dn = "/O=EXCHANGELABS/CN=user1"
    mock_request.return_value = {
        "value": [
            {"id": "u1", "mail": "u1@cb.org", "proxyAddresses": [f"X500:{dn}", "SMTP:u1@cb.org"]}
        ]
    }

    # Container A resolves and writes cache.
    resolve_dns([dn], account_id="acct-1")
    # Simulate lost write: blow away the cache file.
    isolated_cache_file.unlink()
    # Container B (cold cache) re-resolves cleanly.
    second = resolve_dns([dn], account_id="acct-1")

    assert second == {dn: "u1@cb.org"}
    assert mock_request.call_count == 2
    assert _read_cache().get("acct-1", {}).get(dn) == "u1@cb.org"


def test_read_cache_handles_missing_file(isolated_cache_file):
    """Missing cache file returns empty dict."""
    assert _read_cache() == {}


def test_read_cache_handles_corrupt_json(isolated_cache_file):
    """Corrupt cache file returns empty dict (degrades to no-cache)."""
    isolated_cache_file.write_text("not valid json{{{")
    assert _read_cache() == {}


def test_write_cache_degrades_when_dir_unwritable(tmp_path, monkeypatch, caplog):
    """If the shared volume is read-only, cache writes log a warning and degrade."""
    # Point CACHE_FILE at a path whose parent doesn't exist and can't be created.
    unwritable = tmp_path / "nonexistent" / "subdir" / "fake_token_cache.json"
    monkeypatch.setattr("microsoft_mcp.auth.CACHE_FILE", unwritable)
    # Make the parent dir creation fail by chmod-ing tmp_path read-only.
    tmp_path.chmod(0o555)
    try:
        with caplog.at_level(logging.WARNING, logger="microsoft_mcp.address_resolution"):
            _write_cache_atomic({"acct-1": {"/O=X/CN=y": "y@cb.org"}})
        assert any("cache write failed" in rec.message.lower() for rec in caplog.records)
    finally:
        tmp_path.chmod(0o755)


# --- Walker ---

@patch("microsoft_mcp.address_resolution.graph.request")
def test_walker_rewrites_from_field(mock_request, isolated_cache_file):
    dn = "/O=EXCHANGELABS/CN=tom"
    mock_request.return_value = {
        "value": [
            {"id": "t", "mail": "tbooth@cb.org", "proxyAddresses": [f"X500:{dn}", "SMTP:tbooth@cb.org"]}
        ]
    }
    msg = {
        "id": "m1",
        "from": {"emailAddress": {"address": dn, "name": "Tom Booth"}},
    }

    resolve_x500_in_message(msg, account_id="acct-1")

    assert msg["from"]["emailAddress"]["address"] == "tbooth@cb.org"
    assert msg["from"]["emailAddress"]["name"] == "Tom Booth"


@patch("microsoft_mcp.address_resolution.graph.request")
def test_walker_rewrites_recipients_array(mock_request, isolated_cache_file):
    dn = "/O=EXCHANGELABS/CN=tom"
    mock_request.return_value = {
        "value": [
            {"id": "t", "mail": "tbooth@cb.org", "proxyAddresses": [f"X500:{dn}", "SMTP:tbooth@cb.org"]}
        ]
    }
    msg = {
        "id": "m1",
        "toRecipients": [{"emailAddress": {"address": dn, "name": "Tom Booth"}}],
        "ccRecipients": [],
    }

    resolve_x500_in_message(msg, account_id="acct-1")

    assert msg["toRecipients"][0]["emailAddress"]["address"] == "tbooth@cb.org"
    assert msg["ccRecipients"] == []


@patch("microsoft_mcp.address_resolution.graph.request")
def test_walker_handles_missing_address_field(mock_request, isolated_cache_file):
    """Drafts and system messages may omit address entirely; walker must not raise."""
    msg = {"id": "m2", "from": {"emailAddress": {"name": "No Address"}}}
    resolve_x500_in_message(msg, account_id="acct-1")
    mock_request.assert_not_called()


@patch("microsoft_mcp.address_resolution.graph.request")
def test_walker_skips_smtp_addresses(mock_request, isolated_cache_file):
    """SMTP addresses are not X.500 — walker must not query Graph for them."""
    msg = {
        "id": "m3",
        "from": {"emailAddress": {"address": "tbooth@cb.org", "name": "Tom"}},
    }
    resolve_x500_in_message(msg, account_id="acct-1")
    mock_request.assert_not_called()
    assert msg["from"]["emailAddress"]["address"] == "tbooth@cb.org"


@patch("microsoft_mcp.address_resolution.graph.request")
def test_walker_one_graph_call_for_multiple_x500_in_one_message(mock_request, isolated_cache_file):
    """from + recipients with X.500 → single batched Graph call."""
    dn1 = "/O=EXCHANGELABS/CN=user1"
    dn2 = "/O=EXCHANGELABS/CN=user2"
    mock_request.return_value = {
        "value": [
            {"id": "u1", "mail": "u1@cb.org", "proxyAddresses": [f"X500:{dn1}", "SMTP:u1@cb.org"]},
            {"id": "u2", "mail": "u2@cb.org", "proxyAddresses": [f"X500:{dn2}", "SMTP:u2@cb.org"]},
        ]
    }
    msg = {
        "id": "m4",
        "from": {"emailAddress": {"address": dn1}},
        "toRecipients": [{"emailAddress": {"address": dn2}}],
    }

    resolve_x500_in_message(msg, account_id="acct-1")

    assert mock_request.call_count == 1
    assert msg["from"]["emailAddress"]["address"] == "u1@cb.org"
    assert msg["toRecipients"][0]["emailAddress"]["address"] == "u2@cb.org"
```

- [ ] **Step 2: Run tests to verify they fail.**

```bash
.venv/bin/python -m pytest tests/test_x500_resolution.py -v
```

Expected: cache + walker tests FAIL with `ImportError: cannot import name 'resolve_dns'` etc.

- [ ] **Step 3: Implement cache + public `resolve_dns` + walker + logging.** Append to `address_resolution.py`:

```python
import json
import logging
import pathlib as pl

import httpx

from microsoft_mcp import auth

logger = logging.getLogger(__name__)

_CACHE_FILENAME = ".microsoft_mcp_x500_cache.json"

_OBJECT_FIELDS = ("from", "sender", "replyTo")
_ARRAY_FIELDS = ("toRecipients", "ccRecipients", "bccRecipients")


def _cache_path() -> pl.Path:
    """Cache file lives next to the MSAL token cache in the shared volume."""
    return auth.CACHE_FILE.parent / _CACHE_FILENAME


def _read_cache() -> dict[str, dict[str, str | None]]:
    """Load the cache. Missing or corrupt file → empty dict (degrade to no-cache)."""
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_cache_atomic(cache: dict[str, dict[str, str | None]]) -> None:
    """Atomic write via the same helper auth uses for the token cache.

    Degrades to no-op (with warning log) if the shared volume is unwritable.
    """
    try:
        auth._atomic_write(_cache_path(), json.dumps(cache, indent=2))
    except OSError as exc:
        logger.warning("X.500 cache write failed (degrading to no-cache): %s", exc)


def resolve_dns(
    dns: list[str], account_id: str
) -> dict[str, str | None]:
    """Resolve X.500 DNs to SMTPs, with file-based shared-volume cache.

    - Cache hit (smtp string): used.
    - Cache hit (None): confirmed absent, used (do NOT re-query).
    - Cache miss: included in batch Graph call.
    - Lookup error (httpx.HTTPError): result has None for that DN, cache NOT written, warning logged.
    - Own-code error (KeyError, TypeError, etc.): propagates — silent swallow would mask bugs.
    """
    if not dns:
        return {}

    cache = _read_cache()
    account_cache = cache.get(account_id, {})

    result: dict[str, str | None] = {}
    uncached: list[str] = []
    for dn in dns:
        if dn in account_cache:
            result[dn] = account_cache[dn]
        else:
            uncached.append(dn)

    if not uncached:
        return result

    try:
        fresh = _resolve_dns_via_graph(uncached, account_id)
    except httpx.HTTPError as exc:
        logger.warning(
            "X.500 resolution failed for %d DN(s) (account=%s): %s",
            len(uncached), account_id, exc,
        )
        for dn in uncached:
            result[dn] = None
        return result

    result.update(fresh)
    account_cache.update(fresh)
    cache[account_id] = account_cache
    _write_cache_atomic(cache)
    return result


def _collect_x500_dns(msg: dict[str, Any]) -> list[str]:
    """Return all distinct X.500 DNs in the message's email-address fields."""
    seen: set[str] = set()

    for field in _OBJECT_FIELDS:
        obj = msg.get(field)
        if not obj:
            continue
        addr = (obj.get("emailAddress") or {}).get("address")
        if _is_x500_dn(addr):
            seen.add(addr)

    for field in _ARRAY_FIELDS:
        arr = msg.get(field) or []
        for item in arr:
            addr = (item.get("emailAddress") or {}).get("address")
            if _is_x500_dn(addr):
                seen.add(addr)

    return list(seen)


def _apply_dn_map(msg: dict[str, Any], dn_to_smtp: dict[str, str | None]) -> None:
    """Rewrite every X.500 DN in msg's address fields if a SMTP mapping exists."""
    for field in _OBJECT_FIELDS:
        obj = msg.get(field)
        if not obj:
            continue
        ea = obj.get("emailAddress") or {}
        addr = ea.get("address")
        if _is_x500_dn(addr) and dn_to_smtp.get(addr):
            ea["address"] = dn_to_smtp[addr]

    for field in _ARRAY_FIELDS:
        arr = msg.get(field) or []
        for item in arr:
            ea = item.get("emailAddress") or {}
            addr = ea.get("address")
            if _is_x500_dn(addr) and dn_to_smtp.get(addr):
                ea["address"] = dn_to_smtp[addr]


def resolve_x500_in_message(msg: dict[str, Any], account_id: str) -> None:
    """Rewrite X.500 DNs in msg's email-address fields to SMTP, in place.

    Handles both object fields (from, sender, replyTo) and array fields
    (toRecipients, ccRecipients, bccRecipients). Fail-open: on resolver
    error or no-match, the X.500 DN is left in place.
    """
    dns = _collect_x500_dns(msg)
    if not dns:
        return
    mapping = resolve_dns(dns, account_id)
    _apply_dn_map(msg, mapping)
```

- [ ] **Step 4: Run tests to verify they pass.**

```bash
.venv/bin/python -m pytest tests/test_x500_resolution.py -v
```

Expected: all detector + resolver + cache + walker tests PASS.

- [ ] **Step 5: Run gates and commit.**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
git add src/microsoft_mcp/address_resolution.py tests/test_x500_resolution.py
git commit -m "feat(x500): add shared-volume cache and message walker (pa-jsa6)"
```

---

## Phase D: Wire X.500 helper into email tools

### Task D: Integrate into `list_emails`, `get_email`, `search_emails`

**Satisfies:** Janet sees real SMTPs (never X.500 DNs) when reading mailbox listings, single messages, or search results.

**Files:**
- Modify: `src/microsoft_mcp/tools.py` (post-fetch hook in three tools)
- Test: `tests/test_x500_resolution.py` (integration tests for all three)

**Verified during plan-writing:**
- `list_emails` defined at `src/microsoft_mcp/tools.py:177-220`. Returns local variable `emails`. Final return on line 220.
- `get_email` defined at `src/microsoft_mcp/tools.py:224-267`. Returns local variable `result`. Final return on line 267. Parameter is `email_id` (not `message_id`).
- `search_emails` returns `list(graph.search_query(query, ["message"], account_id, limit))` on line 1517.
- `graph.search_query` (graph.py:348) yields the unwrapped `resource` dict (line 391: `yield resource`) — same shape as `list_emails` output. No envelope unwrapping needed.

- [ ] **Step 1: Write the failing integration tests.**

```python
# tests/test_x500_resolution.py — append to existing file
from microsoft_mcp.tools import (
    list_emails as _list_emails_tool,
    get_email as _get_email_tool,
    search_emails as _search_emails_tool,
)

list_emails = _list_emails_tool.fn
get_email = _get_email_tool.fn
search_emails = _search_emails_tool.fn


@patch("microsoft_mcp.tools.address_resolution.resolve_dns")
@patch("microsoft_mcp.tools.graph.request_paginated")
def test_list_emails_rewrites_x500_in_results(mock_paginated, mock_resolve, isolated_cache_file):
    dn = "/O=EXCHANGELABS/CN=tom"
    mock_paginated.return_value = iter([
        {"id": "m1", "from": {"emailAddress": {"address": dn, "name": "Tom Booth"}}}
    ])
    mock_resolve.return_value = {dn: "tbooth@cb.org"}

    rows = list_emails(account_id="acct-1", folder="inbox", limit=5, include_body=False)

    assert rows[0]["from"]["emailAddress"]["address"] == "tbooth@cb.org"


@patch("microsoft_mcp.tools.address_resolution.resolve_dns")
@patch("microsoft_mcp.tools.graph.request")
def test_get_email_rewrites_x500_in_single_message(mock_request, mock_resolve, isolated_cache_file):
    dn = "/O=EXCHANGELABS/CN=tom"
    mock_request.return_value = {
        "id": "m1",
        "from": {"emailAddress": {"address": dn, "name": "Tom Booth"}},
    }
    mock_resolve.return_value = {dn: "tbooth@cb.org"}

    msg = get_email(email_id="m1", account_id="acct-1")
    assert msg["from"]["emailAddress"]["address"] == "tbooth@cb.org"


@patch("microsoft_mcp.tools.address_resolution.resolve_dns")
@patch("microsoft_mcp.tools.graph.search_query")
def test_search_emails_rewrites_x500_in_results(mock_search, mock_resolve, isolated_cache_file):
    """search_emails uses POST /search/query; results pass through the walker."""
    dn = "/O=EXCHANGELABS/CN=tom"
    mock_search.return_value = iter([
        {"id": "m1", "from": {"emailAddress": {"address": dn, "name": "Tom Booth"}}}
    ])
    mock_resolve.return_value = {dn: "tbooth@cb.org"}

    rows = search_emails(query="meeting", account_id="acct-1", limit=10)

    assert rows[0]["from"]["emailAddress"]["address"] == "tbooth@cb.org"
```

- [ ] **Step 2: Run tests to verify they fail.**

```bash
.venv/bin/python -m pytest tests/test_x500_resolution.py -v
```

Expected: the three integration tests FAIL (DNs are not rewritten — no hook yet).

- [ ] **Step 3: Add import + hooks to `tools.py`.**

Add at the top with the other imports:

```python
from microsoft_mcp import address_resolution
```

In `list_emails`, immediately before `return emails` at `tools.py:220`:

```python
    # Apply X.500 DN → SMTP rewriting in place (pa-jsa6).
    for msg in emails:
        address_resolution.resolve_x500_in_message(msg, account_id)
    return emails
```

In `get_email`, immediately before `return result` at `tools.py:267`:

```python
    address_resolution.resolve_x500_in_message(result, account_id)
    return result
```

In `search_emails`, replace `return list(graph.search_query(query, ["message"], account_id, limit))` at `tools.py:1517` with:

```python
    results = list(graph.search_query(query, ["message"], account_id, limit))
    for msg in results:
        address_resolution.resolve_x500_in_message(msg, account_id)
    return results
```

- [ ] **Step 4: Run tests to verify they pass.**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 5: Run gates and commit.**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
git add src/microsoft_mcp/tools.py tests/test_x500_resolution.py
git commit -m "feat(x500): rewrite X.500 DNs in list_emails, get_email, search_emails (pa-jsa6)"
```

---

## Final Task: Verify All Actor Capabilities

**Satisfies:** All

For each actor capability from the design doc:

- [ ] **Janet can search the org directory by name and receive `{name, email, job_title, department, person_type, source}`** — verified by `test_me_people_primary_returns_normalized_rows`, `test_users_fallback_when_people_returns_empty`, `test_users_row_falls_back_to_upn_when_mail_null`.

- [ ] **Janet can search the personal address book via `search_contacts`** — unchanged; existing behavior preserved (manual confirmation: `git diff` should show only docstring changes to `search_contacts`).

- [ ] **Janet can read `from.emailAddress.address` from email-listing tools and get a real SMTP, never X.500** — verified by `test_list_emails_rewrites_x500_in_results`, `test_get_email_rewrites_x500_in_single_message`, `test_search_emails_rewrites_x500_in_results`.

- [ ] **Backfill scripts can use `search_directory`** — same surface as Janet's directory capability; no script-specific test needed.

- [ ] **Other MCP clients (Claude Desktop) get the same capabilities** — by virtue of MCP tool registration via `@mcp.tool`; no client-specific test.

- [ ] **Run the full test suite once more.**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Confirm gate trio.**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
```

Expected: PASS on all three.

- [ ] **Push to GHCR build chain.**

```bash
git push
```

Expected: GitHub Actions auto-build kicks off; image lands in GHCR.

- [ ] **Surface deploy step to Tom.** Tom restarts the gateway container on Windows host (`docker compose up -d` in the gateway dir). Janet's `.claude/CLAUDE.md` MS365 section update pointing to `search_directory` is a separate change in `personal-assistant` repo, not this plan.
