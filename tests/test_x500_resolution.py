"""Tests for address_resolution module (pa-jsa6).

X.500 legacy DN detection, batched /users $filter resolution,
file-based shared-volume cache, and message-walker for email tools.
"""

import logging
from unittest.mock import patch

import pytest

from microsoft_mcp.address_resolution import (
    _is_x500_dn,
    _read_cache,
    _resolve_dns_via_graph,
    _write_cache_atomic,
    resolve_dns,
    resolve_x500_in_message,
)
from microsoft_mcp.tools import (
    get_email as _get_email_tool,
    list_emails as _list_emails_tool,
    search_emails as _search_emails_tool,
)

list_emails = _list_emails_tool.fn
get_email = _get_email_tool.fn
search_emails = _search_emails_tool.fn


# --- Detector ---


def test_detector_recognizes_x500_dn():
    assert (
        _is_x500_dn(
            "/O=EXCHANGELABS/OU=EXCHANGE ADMINISTRATIVE GROUP (FYDIBOHF23SPDLT)/CN=RECIPIENTS/CN=hash-TOM BOOTH"
        )
        is True
    )


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
    """One DN -> one Graph call -> {dn: smtp} map."""
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
    """N DNs -> single Graph call with OR-chained any(eq) clauses."""
    dn1 = "/O=EXCHANGELABS/CN=user1"
    dn2 = "/O=EXCHANGELABS/CN=user2"
    mock_request.return_value = {
        "value": [
            {
                "id": "u1",
                "mail": "user1@cb.org",
                "proxyAddresses": [f"X500:{dn1}", "SMTP:user1@cb.org"],
            },
            {
                "id": "u2",
                "mail": "user2@cb.org",
                "proxyAddresses": [f"X500:{dn2}", "SMTP:user2@cb.org"],
            },
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
    """20 DNs -> 2 Graph calls (15 + 5) to stay under URL-length ceiling."""
    dns = [f"/O=EXCHANGELABS/CN=user{i}" for i in range(20)]
    mock_request.return_value = {"value": []}

    _resolve_dns_via_graph(dns, account_id="acct-1")
    assert mock_request.call_count == 2


@patch("microsoft_mcp.address_resolution.graph.request")
def test_resolver_handles_user_with_multiple_x500_entries(mock_request):
    """User has multiple X500 entries; only the requested DN gets mapped."""
    requested_dn = "/O=EXCHANGELABS/CN=user1-current"
    legacy_dn = "/O=OLDORG/CN=user1-legacy"  # Not requested.
    mock_request.return_value = {
        "value": [
            {
                "id": "u1",
                "mail": "user1@cb.org",
                "proxyAddresses": [
                    f"X500:{requested_dn}",
                    f"X500:{legacy_dn}",
                    "SMTP:user1@cb.org",
                ],
            }
        ]
    }
    result = _resolve_dns_via_graph([requested_dn], account_id="acct-1")
    assert result == {requested_dn: "user1@cb.org"}
    assert legacy_dn not in result


@patch("microsoft_mcp.address_resolution.graph.request")
def test_resolver_ignores_unrelated_users_in_response(mock_request):
    """Graph may return users whose X500 entries don't match the request."""
    requested_dn = "/O=EXCHANGELABS/CN=wanted"
    unrelated_dn = "/O=EXCHANGELABS/CN=unrelated"
    mock_request.return_value = {
        "value": [
            {
                "id": "u1",
                "mail": "wanted@cb.org",
                "proxyAddresses": [
                    f"X500:{requested_dn}",
                    "SMTP:wanted@cb.org",
                ],
            },
            {
                "id": "u2",
                "mail": "unrelated@cb.org",
                "proxyAddresses": [
                    f"X500:{unrelated_dn}",
                    "SMTP:unrelated@cb.org",
                ],
            },
        ]
    }
    result = _resolve_dns_via_graph([requested_dn], account_id="acct-1")
    assert result == {requested_dn: "wanted@cb.org"}


@patch("microsoft_mcp.address_resolution.graph.request")
def test_resolver_handles_null_mail_on_returned_user(mock_request):
    """If a matching user has mail=None, the DN maps to None (not an error)."""
    dn = "/O=EXCHANGELABS/CN=shared-mailbox"
    mock_request.return_value = {
        "value": [
            {
                "id": "u1",
                "mail": None,
                "proxyAddresses": [f"X500:{dn}", "smtp:shared@cb.org"],
            }
        ]
    }
    result = _resolve_dns_via_graph([dn], account_id="acct-1")
    assert result == {dn: None}


@pytest.fixture
def isolated_cache_file(tmp_path, monkeypatch):
    """Redirect the cache file to a tmp dir for the test.

    Monkeypatches auth.CACHE_FILE so _cache_path() resolves to a tmp
    location -- more refactor-stable than monkeypatching _cache_path itself.
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
            {
                "id": "u1",
                "mail": "u1@cb.org",
                "proxyAddresses": [f"X500:{dn}", "SMTP:u1@cb.org"],
            }
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
def test_resolve_dns_does_not_cache_on_http_errors(
    mock_request, isolated_cache_file, caplog
):
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
    """Narrow except -- KeyError/TypeError from response parsing surface, not silent."""
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
            {
                "id": "u2",
                "mail": "fresh@cb.org",
                "proxyAddresses": [f"X500:{dn2}", "SMTP:fresh@cb.org"],
            }
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
            {
                "id": "u1",
                "mail": "u1@cb.org",
                "proxyAddresses": [f"X500:{dn}", "SMTP:u1@cb.org"],
            }
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
    unwritable = tmp_path / "nonexistent" / "subdir" / "fake_token_cache.json"
    monkeypatch.setattr("microsoft_mcp.auth.CACHE_FILE", unwritable)
    tmp_path.chmod(0o555)
    try:
        with caplog.at_level(
            logging.WARNING, logger="microsoft_mcp.address_resolution"
        ):
            _write_cache_atomic({"acct-1": {"/O=X/CN=y": "y@cb.org"}})
        assert any(
            "cache write failed" in rec.message.lower() for rec in caplog.records
        )
    finally:
        tmp_path.chmod(0o755)


# --- Walker ---


@patch("microsoft_mcp.address_resolution.graph.request")
def test_walker_rewrites_from_field(mock_request, isolated_cache_file):
    dn = "/O=EXCHANGELABS/CN=tom"
    mock_request.return_value = {
        "value": [
            {
                "id": "t",
                "mail": "tbooth@cb.org",
                "proxyAddresses": [f"X500:{dn}", "SMTP:tbooth@cb.org"],
            }
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
            {
                "id": "t",
                "mail": "tbooth@cb.org",
                "proxyAddresses": [f"X500:{dn}", "SMTP:tbooth@cb.org"],
            }
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
    """SMTP addresses are not X.500 -- walker must not query Graph for them."""
    msg = {
        "id": "m3",
        "from": {"emailAddress": {"address": "tbooth@cb.org", "name": "Tom"}},
    }
    resolve_x500_in_message(msg, account_id="acct-1")
    mock_request.assert_not_called()
    assert msg["from"]["emailAddress"]["address"] == "tbooth@cb.org"


@patch("microsoft_mcp.address_resolution.graph.request")
def test_walker_one_graph_call_for_multiple_x500_in_one_message(
    mock_request, isolated_cache_file
):
    """from + recipients with X.500 -> single batched Graph call."""
    dn1 = "/O=EXCHANGELABS/CN=user1"
    dn2 = "/O=EXCHANGELABS/CN=user2"
    mock_request.return_value = {
        "value": [
            {
                "id": "u1",
                "mail": "u1@cb.org",
                "proxyAddresses": [f"X500:{dn1}", "SMTP:u1@cb.org"],
            },
            {
                "id": "u2",
                "mail": "u2@cb.org",
                "proxyAddresses": [f"X500:{dn2}", "SMTP:u2@cb.org"],
            },
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


# --- Tool integration ---


@patch("microsoft_mcp.tools.address_resolution.resolve_dns")
@patch("microsoft_mcp.tools.graph.request_paginated")
def test_list_emails_rewrites_x500_in_results(
    mock_paginated, mock_resolve, isolated_cache_file
):
    dn = "/O=EXCHANGELABS/CN=tom"
    mock_paginated.return_value = iter(
        [{"id": "m1", "from": {"emailAddress": {"address": dn, "name": "Tom Booth"}}}]
    )
    mock_resolve.return_value = {dn: "tbooth@cb.org"}

    rows = list_emails(account_id="acct-1", folder="inbox", limit=5, include_body=False)

    assert rows[0]["from"]["emailAddress"]["address"] == "tbooth@cb.org"


@patch("microsoft_mcp.tools.address_resolution.resolve_dns")
@patch("microsoft_mcp.tools.graph.request")
def test_get_email_rewrites_x500_in_single_message(
    mock_request, mock_resolve, isolated_cache_file
):
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
def test_search_emails_rewrites_x500_in_results(
    mock_search, mock_resolve, isolated_cache_file
):
    """search_emails uses POST /search/query; results pass through the walker."""
    dn = "/O=EXCHANGELABS/CN=tom"
    mock_search.return_value = iter(
        [{"id": "m1", "from": {"emailAddress": {"address": dn, "name": "Tom Booth"}}}]
    )
    mock_resolve.return_value = {dn: "tbooth@cb.org"}

    rows = search_emails(query="meeting", account_id="acct-1", limit=10)

    assert rows[0]["from"]["emailAddress"]["address"] == "tbooth@cb.org"


@patch("microsoft_mcp.tools.address_resolution.resolve_dns")
@patch("microsoft_mcp.tools.graph.request_paginated")
def test_search_emails_with_folder_rewrites_x500_in_results(
    mock_paginated, mock_resolve, isolated_cache_file
):
    """Folder-scoped search_emails uses request_paginated; results pass through the walker."""
    dn = "/O=EXCHANGELABS/CN=tom"
    mock_paginated.return_value = iter(
        [{"id": "m1", "from": {"emailAddress": {"address": dn, "name": "Tom Booth"}}}]
    )
    mock_resolve.return_value = {dn: "tbooth@cb.org"}

    rows = search_emails(query="meeting", account_id="acct-1", limit=10, folder="inbox")

    assert rows[0]["from"]["emailAddress"]["address"] == "tbooth@cb.org"
