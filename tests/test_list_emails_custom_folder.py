"""Tests for list_emails custom-folder name resolution (pa-uqf2).

Graph's `/me/mailFolders/{folder}/messages` endpoint accepts well-known
folder names (inbox, sentitems, ...) or folder IDs, but NOT custom
display names like "Action Required" — those return
ErrorInvalidIdMalformed. list_emails now resolves custom names to
folder IDs via the same pattern move_email uses.
"""

from unittest.mock import patch
import pytest
from microsoft_mcp.tools import (
    list_emails as _list_emails_tool,
    _resolve_folder_id,
)

# FastMCP 2.8.0: @mcp.tool(name=...) wraps in FunctionTool; .fn is the raw callable
list_emails = _list_emails_tool.fn


def _paginated_noop(*_args, **_kwargs):
    """Stand-in for graph.request_paginated that returns no emails."""
    return iter([])


@patch("microsoft_mcp.tools.graph.request_paginated", side_effect=_paginated_noop)
@patch("microsoft_mcp.tools.graph.request")
def test_well_known_folder_does_not_trigger_lookup(mock_request, mock_paginated):
    """inbox/sent/drafts/etc. must not hit /me/mailFolders — that's the
    legacy fast path and we don't want to regress latency."""
    list_emails(account_id="acct-1", folder="inbox", limit=5, include_body=False)

    # graph.request (used for the mailFolders walk) should not be called.
    mock_request.assert_not_called()

    # The pagination path should be addressed by the well-known name.
    call = mock_paginated.call_args
    assert call.args[0] == "/me/mailFolders/inbox/messages"


@patch("microsoft_mcp.tools.graph.request_paginated", side_effect=_paginated_noop)
@patch("microsoft_mcp.tools.graph.request")
def test_custom_folder_resolved_to_id_at_top_level(mock_request, mock_paginated):
    """A custom display name found at the top level should be rewritten
    to its folder ID in the messages endpoint."""
    mock_request.return_value = {
        "value": [
            {"id": "AAA=", "displayName": "Inbox"},
            {"id": "BBB=", "displayName": "Action Required"},
        ]
    }

    list_emails(
        account_id="acct-1",
        folder="Action Required",
        limit=5,
        include_body=False,
    )

    # Only the top-level listing should have been needed.
    mock_request.assert_called_once_with("GET", "/me/mailFolders", "acct-1")

    call = mock_paginated.call_args
    assert call.args[0] == "/me/mailFolders/BBB=/messages"


@patch("microsoft_mcp.tools.graph.request_paginated", side_effect=_paginated_noop)
@patch("microsoft_mcp.tools.graph.request")
def test_custom_folder_resolved_one_level_deep(mock_request, mock_paginated):
    """Folders nested under Inbox (e.g. Inbox/Finance) should be
    discovered on the child-folder pass."""
    top = {
        "value": [
            {"id": "INBOX-ID", "displayName": "Inbox"},
            {"id": "ARCHIVE-ID", "displayName": "Archive"},
        ]
    }
    inbox_children = {
        "value": [
            {"id": "FIN-ID", "displayName": "Finance"},
            {"id": "LEGAL-ID", "displayName": "Legal & Privacy"},
        ]
    }
    archive_children = {"value": []}

    def fake_request(method, path, account_id):
        assert method == "GET"
        assert account_id == "acct-1"
        if path == "/me/mailFolders":
            return top
        if path == "/me/mailFolders/INBOX-ID/childFolders":
            return inbox_children
        if path == "/me/mailFolders/ARCHIVE-ID/childFolders":
            return archive_children
        raise AssertionError(f"unexpected path: {path}")

    mock_request.side_effect = fake_request

    list_emails(
        account_id="acct-1",
        folder="Legal & Privacy",
        limit=5,
        include_body=False,
    )

    call = mock_paginated.call_args
    assert call.args[0] == "/me/mailFolders/LEGAL-ID/messages"


@patch("microsoft_mcp.tools.graph.request_paginated", side_effect=_paginated_noop)
@patch("microsoft_mcp.tools.graph.request")
def test_custom_folder_match_is_case_insensitive(mock_request, mock_paginated):
    """Custom-folder match must be case-insensitive (matches move_email
    behavior)."""
    mock_request.return_value = {
        "value": [{"id": "CF-ID", "displayName": "Action Required"}]
    }

    list_emails(
        account_id="acct-1",
        folder="action required",
        limit=5,
        include_body=False,
    )

    call = mock_paginated.call_args
    assert call.args[0] == "/me/mailFolders/CF-ID/messages"


@patch("microsoft_mcp.tools.graph.request_paginated", side_effect=_paginated_noop)
@patch("microsoft_mcp.tools.graph.request")
def test_missing_custom_folder_raises_value_error(mock_request, mock_paginated):
    """If a custom name can't be resolved at top level or one level deep,
    surface a ValueError rather than letting Graph reject a malformed ID."""
    mock_request.side_effect = [
        {"value": [{"id": "INBOX-ID", "displayName": "Inbox"}]},
        {"value": []},  # Inbox has no children
    ]

    with pytest.raises(ValueError, match="NoSuchFolder"):
        list_emails(
            account_id="acct-1",
            folder="NoSuchFolder",
            limit=5,
            include_body=False,
        )

    mock_paginated.assert_not_called()


@patch("microsoft_mcp.tools.graph.request")
def test_resolve_helper_returns_none_when_missing(mock_request):
    """The helper itself returns None when no match is found — callers
    decide how to surface that."""
    mock_request.side_effect = [
        {"value": [{"id": "INBOX-ID", "displayName": "Inbox"}]},
        {"value": []},
    ]

    assert _resolve_folder_id("Missing", "acct-1") is None
