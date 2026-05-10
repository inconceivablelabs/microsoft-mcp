"""Walker tests for replyTo classification + OBJECT-branch defense (mcp-9k2).

Kept in a separate file so collection doesn't depend on `microsoft_mcp.tools`
imports, which are currently broken at HEAD by a FastMCP API change
unrelated to this fix (see report: tests/test_x500_resolution.py and 8 other
test files fail collection on `_tool.fn` — separate cleanup work).
"""

from unittest.mock import patch

import pytest

from microsoft_mcp.address_resolution import (
    _collect_x500_dns,
    resolve_x500_in_message,
)


@pytest.fixture
def isolated_cache_file(tmp_path, monkeypatch):
    """Redirect the X.500 cache to a tmp dir for the test.

    Mirrors the fixture in test_x500_resolution.py so resolve_x500_in_message
    doesn't hit the shared volume.
    """
    fake_token_cache = tmp_path / "fake_token_cache.json"
    monkeypatch.setattr("microsoft_mcp.auth.CACHE_FILE", fake_token_cache)
    return tmp_path / ".microsoft_mcp_x500_cache.json"


@patch("microsoft_mcp.address_resolution.graph.request")
def test_walker_handles_non_empty_replyto_array(mock_request, isolated_cache_file):
    """replyTo is a recipient collection (array), not a single recipient (mcp-9k2).

    Production AttributeError: when a message has a non-empty replyTo array
    (common on newsletters/mailing lists), the walker called .get() on a list.
    Walker must walk replyTo as an array AND rewrite X.500 DNs in it.
    """
    smtp_dn = "/O=EXCHANGELABS/OU=.../CN=hash-LIST-OWNER"
    mock_request.return_value = {
        "value": [
            {
                "id": "u1",
                "mail": "owner@example.com",
                "proxyAddresses": [f"X500:{smtp_dn}", "SMTP:owner@example.com"],
            }
        ]
    }
    msg = {
        "id": "m-newsletter",
        "from": {"emailAddress": {"address": "list@example.com", "name": "List"}},
        "replyTo": [
            {"emailAddress": {"address": "list@example.com", "name": "List"}},
            {"emailAddress": {"address": smtp_dn, "name": "X500"}},
        ],
    }

    # Pre-fix: this raises AttributeError: 'list' object has no attribute 'get'.
    dns = _collect_x500_dns(msg)
    assert smtp_dn in dns

    resolve_x500_in_message(msg, account_id="acct-1")
    assert msg["replyTo"][1]["emailAddress"]["address"] == "owner@example.com"
    # Non-X.500 entry untouched.
    assert msg["replyTo"][0]["emailAddress"]["address"] == "list@example.com"


@pytest.mark.parametrize("field", ["from", "sender"])
@patch("microsoft_mcp.address_resolution.graph.request")
def test_walker_tolerates_list_in_object_field(
    mock_request, isolated_cache_file, field
):
    """Defense in depth: if Graph ever returns a list where we expect an object,
    walker yields nothing for that field rather than crashing (mcp-9k2).
    """
    msg = {
        "id": "m-malformed",
        field: [{"emailAddress": {"address": "/O=X/CN=y"}}],
    }

    # Explicit no-raise + empty result for sharper failing-test signal.
    assert _collect_x500_dns(msg) == []
    resolve_x500_in_message(msg, account_id="acct-1")
    mock_request.assert_not_called()


@patch("microsoft_mcp.address_resolution.graph.request")
def test_walker_object_fields_happy_path(mock_request, isolated_cache_file):
    """Sanity: from and sender as proper dicts still yield emailAddress dicts."""
    dn_from = "/O=EXCHANGELABS/CN=fromuser"
    dn_sender = "/O=EXCHANGELABS/CN=senderuser"
    mock_request.return_value = {
        "value": [
            {
                "id": "u1",
                "mail": "from@cb.org",
                "proxyAddresses": [f"X500:{dn_from}", "SMTP:from@cb.org"],
            },
            {
                "id": "u2",
                "mail": "sender@cb.org",
                "proxyAddresses": [f"X500:{dn_sender}", "SMTP:sender@cb.org"],
            },
        ]
    }
    msg = {
        "id": "m-happy",
        "from": {"emailAddress": {"address": dn_from, "name": "From"}},
        "sender": {"emailAddress": {"address": dn_sender, "name": "Sender"}},
    }

    resolve_x500_in_message(msg, account_id="acct-1")
    assert msg["from"]["emailAddress"]["address"] == "from@cb.org"
    assert msg["sender"]["emailAddress"]["address"] == "sender@cb.org"
