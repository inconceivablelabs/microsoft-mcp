# Directory Search + X.500 DN Resolution

**Beads:** pa-b14f (new GAL tool), pa-jsa6 (X.500 DN→SMTP resolution)
**Date:** 2026-05-06 (revised 2026-05-07 after design review + assumption validation)
**Status:** Design

## Problem

Two related gaps in microsoft-mcp's email/people surfaces:

1. **No GAL access (pa-b14f).** Both `list_contacts` and `search_contacts` query `/me/contacts` (the user's personal address book). For Tom this collection has ~15 entries — CB-internal employees he corresponds with daily live in Exchange's Global Address List, which no current tool reaches. Verified by reading `tools.py:1156` and `tools.py:1521` — `search_contacts` adds `$search` only narrowing within `/me/contacts`.

2. **X.500 DN leaks through email tools (pa-jsa6).** Graph returns `from.emailAddress.address` as an X.500 legacy DN (e.g. `/O=EXCHANGELABS/OU=.../CN=hash-TOM BOOTH`) for certain message types — notably meeting RSVPs (Accepted:/Declined:/Tentative:) and NDRs. Consumers expecting SMTP addresses (Janet's triage filter) silently fail to match. Workaround in personal-assistant (`search_untriaged_emails`) string-matches on display name when address starts with `/O=`; the upstream fix is owed to all consumers.

## Actor Capabilities

| Actor | Capability | Status |
|---|---|---|
| Janet (MCP client) | search the org directory by name, receive `{name, email, job_title, department, person_type, source}` | NEW |
| Janet | search the personal address book via `search_contacts` | unchanged |
| Janet | read `from.emailAddress.address` from email-listing tools and get a real SMTP, never X.500 | NEW |
| Backfill scripts (e.g. `backfill_entity_emails.py`) | same as Janet's directory capability | NEW |
| Other MCP clients (Claude Desktop) | same capabilities as Janet | NEW |

## Design

### pa-b14f: `search_directory` tool

New MCP tool, additive — existing contact tools unchanged.

**Signature:**
```python
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
    address book (/me/contacts)."""
```

**Two-tier lookup:**
1. **Primary:** `GET /me/people?$search="<query>"&$top=<limit>&$select=displayName,scoredEmailAddresses,jobTitle,department,personType` — relevance-ranked, includes anyone the user has corresponded with via mail/Teams/calendar. `graph.request` automatically applies `ConsistencyLevel: eventual` + `$count=true` because the URL contains `$search`.
2. **Fallback:** if primary returns 0 results, call `GET /users?$search="displayName:<query>" OR "mail:<query>"&$top=<limit>&$select=displayName,mail,userPrincipalName,jobTitle,department`. Same auto-injected advanced-query headers via graph.request.

**Notes on syntax:**
- `/me/people` accepts free-text in `$search` (e.g. `?$search="Smith"`) — fuzzy across displayName and emailAddress; no field qualification needed.
- `/users` `$search` requires `field:value` form with quotes per term, OR uppercase between terms (no parens), e.g. `?$search="displayName:Smith" OR "mail:smith"`.

**Normalized response shape (per row):**
```python
{
    "name": str,                        # displayName
    "email": str | None,                # see selection logic below
    "job_title": str | None,
    "department": str | None,
    "person_type": str | None,          # /me/people personType, None for /users hits
    "source": "people" | "users",       # which endpoint matched (debug signal)
}
```

**Email-field selection logic:**
- `/me/people` rows: `scoredEmailAddresses[0].address` if present, else `None`.
- `/users` rows: `mail` if present, else `userPrincipalName` (covers shared mailboxes / service accounts where `mail` is null), else `None`.

**`person_type` passthrough rationale:** /me/people returns `OrganizationUser` for CB employees vs `Person` (with subclass `OtherContact`) for external correspondents. Lets Janet disambiguate "this is a CB colleague" from "this is someone Tom emailed once externally."

**`search_contacts` docstring update:** Add a one-sentence note clarifying that `search_contacts` only searches the personal address book (`/me/contacts`); for the organization directory use `search_directory`.

### pa-jsa6: X.500 DN → SMTP helper

Helper-layer fix in a new `address_resolution.py` module. Called from email-listing tools that surface `emailAddress` objects.

**Detection:** `address.startswith("/O=")` — simple, no false positives in real Graph traffic. Detector handles `None` / missing `address` field defensively.

**Resolution path (single DN):**
```python
GET /users
    ?$filter=proxyAddresses/any(p:p eq 'X500:<DN>')
    &$select=mail,proxyAddresses,id,displayName
```
`graph.request` auto-injects `ConsistencyLevel: eventual` + `$count=true` because of `/any(`.

Returns the `mail` field of the matching user, or `None` if no match.

**Batched resolution (multiple DNs in one tool call):**
For an email batch with N distinct unresolved DNs, build one OR-chained filter:
```
$filter=
  proxyAddresses/any(p:p eq 'X500:<dn1>')
  or proxyAddresses/any(p:p eq 'X500:<dn2>')
  ...
  or proxyAddresses/any(p:p eq 'X500:<dnN>')
```
One Graph call resolves the whole batch. After response, build a `{x500_dn: smtp}` map by walking each returned user's `proxyAddresses` (each user has one `X500:` entry per legacy mailbox; multiple if migrated). URL length budget: ~3KB for 10 DNs (well under 16KB practical limit).

**Why OR-chained `any(eq)` and not `any(p:p in (...))`:** the latter combination (`in` operator inside `any`) is not documented for Graph, only `eq`. OR-chained `any(eq)` is documented to work and adds at most ~250 chars per DN.

**Cache (file-based JSON):**

Path: `<token-cache parent dir>/.microsoft_mcp_x500_cache.json`. Same shared volume as the MSAL token cache, so it survives the per-call container-spawn model documented in `auth.py:34-39`.

```json
{
  "<account_id>": {
    "<X500_DN>": "<smtp>" | null,
    ...
  }
}
```

**Cache semantics:**
- **Cache hit (value = smtp string):** use it.
- **Cache hit (value = null):** previous lookup confirmed no match — do NOT re-query. Cached forever (until manual cache invalidation).
- **Cache miss:** include in batch resolution; on response, write `smtp` for found users, `null` for DNs that returned no match.
- **Lookup error (network failure, 429, 500-class):** do NOT cache. Distinguish from "no match" by inspecting the response — exception path skips cache write.

**Atomic writes:** reuse `auth._atomic_write`. Read-modify-write race between two simultaneous tool-call containers: worst case is one extra Graph call (the loser's cache update is lost; next call rebuilds). Acceptable.

**Application points:** A helper `_resolve_x500_in_message(msg, account_id) -> msg` that walks message fields and rewrites X.500 entries in place. Field-walking handles two shapes:

- **Object fields** (`from`, `sender`, `replyTo`): single `{emailAddress: {address, name}}` object. Check `address.startswith("/O=")`, resolve, replace.
- **Array fields** (`toRecipients`, `ccRecipients`, `bccRecipients`): list of `{emailAddress: {address, name}}` objects. Iterate, check each, resolve unmapped DNs.

Helper short-circuits when `from.emailAddress` or `from.emailAddress.address` is missing entirely (drafts, system NDRs).

**Called post-fetch by:**
- `search_emails` — applies at the `hitsContainers[].hits[].resource` level (POST `/search/query` envelope per `tools.py:1517`), not the top level.
- `list_emails` — applies at each item in the paginated result.
- `get_email` — applies at the single returned message.

(Out of scope for now: calendar attendees, contact creation echoes, forward_email recipient echoes — different fault domain, no reported bug.)

**Fail-open behavior:** If the resolver fails (network error, scope issue, all DNs return None), the helper logs and returns the message unchanged with the X.500 DN intact. Existing consumer behavior preserved on lookup failure.

### Shared infrastructure

- `address_resolution.py` — new module: detector, batched resolver, cache I/O, message-walker.
- Tests live in new files: `tests/test_search_directory.py` and `tests/test_x500_resolution.py`.
- `graph.request` already handles advanced-query header injection — no new helper needed.

## Assumptions

| Assumption | Risk if wrong | Validation method |
|---|---|---|
| `/me/people` is callable under Tom's app's pre-consented `.default` scope (likely needs `People.Read` delegated permission) | Primary GAL-search path 401s | First implementation call against `/me/people` validates this. If 403 ErrorAccessDenied, ask Tom to add `People.Read` (delegated) to the Azure app registration |
| `/users` `$search` and `$filter=proxyAddresses/any(...)` work under existing scope. `User.ReadBasic.All` is sufficient (Graph docs list this as least-privileged for /users-list with the fields we need). Tom states `User.Read.All` is granted, which strictly subsumes `User.ReadBasic.All` | Both pa-b14f fallback and pa-jsa6 resolver fail with 403 | First /users call validates. Note: if grant is revoked tenant-wide, both features dark-fail simultaneously |
| The X.500 DN in `from.emailAddress.address` matches the corresponding `proxyAddresses` entry verbatim with `X500:` prefix (per Microsoft's documented model — capitalized prefix denotes primary X500 alias) | `$filter` with `eq 'X500:<DN>'` returns 0 hits even for valid users | Capture a real RSVP message DN, then `GET /users/{id}/proxyAddresses` and compare. If case-sensitivity bites, defensive secondary path: `$filter=proxyAddresses/any(p:startswith(p, 'X500:'))` + client-side filter (uglier but works) |
| `/me/people` `$search` accepts free-text without field-qualification (e.g. `?$search="Smith"` matches displayName and email) — verified via Graph docs (people-insights-overview) | Free-text query path 400s | Implementation can fall back to explicit filter shape if needed; doc claim is firm |
| `/users` `$search` requires `field:value` form with OR uppercase — Graph docs explicit | Fallback path 400s with bad syntax | Documented; first call validates |
| The shared volume that holds the MSAL token cache (`auth.py:34-39`) is also readable/writable for our resolution cache file | Cache file fails to persist; effectively no cache | Cache module probes existence/permissions on first read; logs and degrades to no-cache if unavailable |
| RSVPs and NDRs are the dominant message types surfacing X.500 DNs in `from.address`. Other types may exist (system messages, Exchange admin) but resolver works regardless of source class | Lower cache hit rate than expected; resolver still functional | Log cache-hit-rate counter; review post-deploy after a 30-day window |
| URL length for OR-chained `any(eq)` filter stays under Graph's accepted ceiling for batch sizes ≤20 DNs (~5KB worst case at ~250 chars/DN) | Batch query 414 (URI Too Long); resolver falls back to single-DN calls | Cap batch size at 15 distinct DNs per Graph call; if exceeded, split into multiple calls |

**Removed assumption from prior draft:** "Advanced-query (ConsistencyLevel + $count) required for `$filter=proxyAddresses/any(...)`" — `graph.request` (graph.py:36-42) already auto-injects these headers when it detects `$search`, `contains(`, or `/any(` in the request. No design action needed.

## Out of Scope

- Renaming `search_contacts` → `search_personal_contacts` (breaking change; not worth it; docstring update is sufficient).
- X.500 resolution in calendar attendee fields, contact creation responses, forward_email recipient echoes (different fault domain; no reported bug).
- Cache eviction policy beyond manual flush (cache file is small, entries are stable; if it grows pathologically, add a max-entries cap later).
- Backfilling existing entity emails using `search_directory` (separate concern; pa-civi's email-keyed lookup handles new emails organically; opportunistic per Tom's 2026-05-06 decision).
- Janet persona-doc updates pointing to `search_directory` (lives in personal-assistant repo, separate change after deploy).
- Tenant-scoped (vs account-scoped) cache keying — single-tenant deployment for now; revisit if multi-tenant.

## Test Strategy

**TDD per change.** Each new function gets a failing test before implementation.

`test_search_directory.py`:
- /me/people primary path returns normalized rows with all six fields
- /users fallback fires when /me/people returns 0
- Email field selection: `/me/people` uses scoredEmailAddresses[0]; `/users` uses mail then UPN
- Normalization handles missing fields (no jobTitle, no department, no email)
- `person_type` populated on /me/people rows, None on /users rows
- `source` field correctly identifies which endpoint matched
- Limit param respected on both paths
- Free-text query (`/me/people`) and field-qualified query (`/users`) syntax both build correctly

`test_x500_resolution.py`:
- `_is_x500_dn` detector returns True only for `/O=` prefix; False on None, "", SMTP, missing field
- Single-DN resolver returns SMTP for known DN
- Batched resolver: N DNs → 1 Graph call, OR-chained filter built correctly
- Batch size cap at 15 DNs; 20 DNs → 2 Graph calls
- Resolver returns None for unknown DN, caches negative
- Lookup error (mocked exception): does NOT write cache
- Repeated calls hit cache (mock /users called once across two `_resolve_x500_in_message` invocations)
- `_resolve_x500_in_message` rewrites `from`, `sender`, `replyTo` (object shape)
- `_resolve_x500_in_message` rewrites `toRecipients`, `ccRecipients`, `bccRecipients` (array shape)
- Helper short-circuits cleanly when `from.emailAddress.address` is absent
- search_emails integration: X.500 in `hitsContainers[].hits[].resource.from.emailAddress.address` → SMTP
- list_emails integration: X.500 in paginated message items → SMTP
- get_email integration: X.500 in single message → SMTP
- Cache file format: account_id keyed map; atomic write tested via concurrent-write mock
- Cache file missing or corrupt: degrades to no-cache, logs warning, continues

## Deploy

Standard microsoft-mcp deploy chain (per personal-assistant MEMORY.md):
1. Push to `inconceivablelabs/microsoft-mcp` main
2. GHCR auto-builds via GitHub Actions
3. Tom restarts the gateway container on Windows host (`docker compose up -d` in the gateway dir, or `docker compose restart`)

Janet's `.claude/CLAUDE.md` MS365 section gets a follow-up update after deploy points to `search_directory`. Tracked separately, not part of this design.

## Review history

- **2026-05-06:** initial design.
- **2026-05-07:** revised after parallel design-review + assumption-validation subagents:
  - Dropped redundant `graph.advanced_query` helper (graph.py already auto-injects headers).
  - Switched in-memory cache to file-based shared-volume cache (per-call container spawn invalidates in-memory).
  - Added batched OR-chained resolution for multi-DN message batches.
  - Distinguished no-match (cache forever) from lookup-error (do not cache).
  - Added field-walking shape spec (object vs array fields).
  - Added `$select` to both Graph calls.
  - Added `person_type` passthrough on /me/people normalization.
  - Added UPN fallback for `/users` rows with null `mail`.
  - Noted `User.ReadBasic.All` is the documented minimum, not `User.Read.All`.
  - Documented search_emails' `hitsContainers[].hits[].resource` shape vs list/get_email.
  - Removed unverified `proxyAddresses/any(p:p in (...))` syntax in favor of documented OR-chained `any(eq)`.
