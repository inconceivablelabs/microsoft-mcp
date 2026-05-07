"""Tests for address_resolution module (pa-jsa6).

X.500 legacy DN detection, batched /users $filter resolution,
file-based shared-volume cache, and message-walker for email tools.
"""

from unittest.mock import patch
from microsoft_mcp.address_resolution import _is_x500_dn, _resolve_dns_via_graph


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
