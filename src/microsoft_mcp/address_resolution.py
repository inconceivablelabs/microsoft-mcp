"""X.500 legacy DN -> SMTP resolution for email-listing tool results.

Graph returns from.emailAddress.address as an X.500 DN
(/O=EXCHANGELABS/...) for certain message types -- meeting RSVPs,
NDRs, etc. This module detects those, resolves them to SMTP via
/users $filter on proxyAddresses, caches results in a shared-volume
JSON file, and rewrites message dicts in place.

Design: project-internals/microsoft-mcp/plans/2026-05-06-directory-search-and-x500-resolution-design.md.
"""

import json
import logging
import pathlib as pl
from typing import Any, Iterator, TypeGuard

import httpx

from microsoft_mcp import auth, graph

logger = logging.getLogger(__name__)

_BATCH_CAP = 15
_CACHE_FILENAME = ".microsoft_mcp_x500_cache.json"

_OBJECT_FIELDS = ("from", "sender")
_ARRAY_FIELDS = ("toRecipients", "ccRecipients", "bccRecipients", "replyTo")


def _odata_escape(value: str) -> str:
    """Escape single quotes per OData literal-string convention (' -> '')."""
    return value.replace("'", "''")


def _is_x500_dn(address: str | None) -> TypeGuard[str]:
    """Return True iff the address is an X.500 legacy DN.

    Detection key: capitalized /O= prefix per Microsoft's documented
    proxyAddresses convention. None / empty / lowercase return False.
    Acts as a type guard so callers narrow `str | None` to `str`.
    """
    if not address:
        return False
    return address.startswith("/O=")


def _resolve_dns_via_graph(dns: list[str], account_id: str) -> dict[str, str | None]:
    """Batch-resolve X.500 DNs to SMTPs via /users $filter on proxyAddresses.

    Returns {dn: smtp_or_None}. Splits batches >15 DNs into multiple Graph
    calls to stay under URL-length limits. Lookup errors are NOT swallowed
    here -- callers (cache layer) decide whether to cache or skip.
    """
    if not dns:
        return {}

    result: dict[str, str | None] = {dn: None for dn in dns}

    for start in range(0, len(dns), _BATCH_CAP):
        batch = dns[start : start + _BATCH_CAP]
        filter_expr = " or ".join(
            f"proxyAddresses/any(p:p eq 'X500:{_odata_escape(dn)}')" for dn in batch
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


def _cache_path() -> pl.Path:
    """Cache file lives next to the MSAL token cache in the shared volume."""
    return auth.CACHE_FILE.parent / _CACHE_FILENAME


def _read_cache() -> dict[str, dict[str, str | None]]:
    """Load the cache. Missing or corrupt file -> empty dict (degrade to no-cache)."""
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


def resolve_dns(dns: list[str], account_id: str) -> dict[str, str | None]:
    """Resolve X.500 DNs to SMTPs, with file-based shared-volume cache.

    - Cache hit (smtp string): used.
    - Cache hit (None): confirmed absent, used (do NOT re-query).
    - Cache miss: included in batch Graph call.
    - Lookup error (httpx.HTTPError): result has None for that DN, cache NOT
      written, warning logged.
    - Own-code error (KeyError, TypeError, etc.): propagates -- silent swallow
      would mask bugs.
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
            len(uncached),
            account_id,
            exc,
        )
        for dn in uncached:
            result[dn] = None
        return result

    result.update(fresh)
    account_cache.update(fresh)
    cache[account_id] = account_cache
    _write_cache_atomic(cache)
    return result


def _iter_email_address_dicts(msg: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield each `emailAddress` dict in msg's address fields.

    Walks both object fields (from, sender) and array fields
    (to/cc/bccRecipients, replyTo). Yields mutable dicts so callers can
    rewrite `address` in place. Skips fields that are missing, None, empty,
    or shape-surprising (defense in depth: a list where a dict was expected,
    or vice versa, is silently skipped rather than crashing the tool call).
    """
    for field in _OBJECT_FIELDS:
        obj = msg.get(field)
        if not isinstance(obj, dict):
            continue
        ea = obj.get("emailAddress")
        if isinstance(ea, dict):
            yield ea

    for field in _ARRAY_FIELDS:
        arr = msg.get(field) or []
        for item in arr:
            ea = item.get("emailAddress") if isinstance(item, dict) else None
            if isinstance(ea, dict):
                yield ea


def _collect_x500_dns(msg: dict[str, Any]) -> list[str]:
    """Return all distinct X.500 DNs in the message's email-address fields."""
    seen: set[str] = set()
    for ea in _iter_email_address_dicts(msg):
        addr = ea.get("address")
        if _is_x500_dn(addr):
            seen.add(addr)
    return list(seen)


def _apply_dn_map(msg: dict[str, Any], dn_to_smtp: dict[str, str | None]) -> None:
    """Rewrite every X.500 DN in msg's address fields if a SMTP mapping exists."""
    for ea in _iter_email_address_dicts(msg):
        addr = ea.get("address")
        if _is_x500_dn(addr) and dn_to_smtp.get(addr):
            ea["address"] = dn_to_smtp[addr]


def resolve_x500_in_message(msg: dict[str, Any], account_id: str) -> None:
    """Rewrite X.500 DNs in msg's email-address fields to SMTP, in place.

    Handles both object fields (from, sender) and array fields
    (toRecipients, ccRecipients, bccRecipients, replyTo). Fail-open: on
    resolver error or no-match, the X.500 DN is left in place.
    """
    dns = _collect_x500_dns(msg)
    if not dns:
        return
    mapping = resolve_dns(dns, account_id)
    _apply_dn_map(msg, mapping)
