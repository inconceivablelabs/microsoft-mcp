"""X.500 legacy DN -> SMTP resolution for email-listing tool results.

Graph returns from.emailAddress.address as an X.500 DN
(/O=EXCHANGELABS/...) for certain message types -- meeting RSVPs,
NDRs, etc. This module detects those, resolves them to SMTP via
/users $filter on proxyAddresses, caches results in a shared-volume
JSON file, and rewrites message dicts in place.

See docs/plans/2026-05-06-directory-search-and-x500-resolution-design.md.
"""

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
