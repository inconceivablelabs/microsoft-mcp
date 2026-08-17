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
    list_emails,
    _resolve_folder_id,
)


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


def _fake_paginated_folders(folders: dict[str, list[dict[str, str]]]):
    """Stand-in for graph.request_paginated that serves folder/child
    listings from `folders` (path -> items) and returns no messages for
    any other path (e.g. the final /messages listing). Folder resolution
    now goes through request_paginated too (mcp-fk1), so this replaces
    the old split between a `graph.request`-mocked folder walk and a
    `graph.request_paginated`-mocked messages call."""

    def fake(path, account_id, params=None, limit=None):
        return iter(folders.get(path, []))

    return fake


@patch("microsoft_mcp.tools.graph.request_paginated")
def test_custom_folder_resolved_to_id_at_top_level(mock_paginated):
    """A custom display name found at the top level should be rewritten
    to its folder ID in the messages endpoint."""
    mock_paginated.side_effect = _fake_paginated_folders(
        {
            "/me/mailFolders": [
                {"id": "AAA=", "displayName": "Inbox"},
                {"id": "BBB=", "displayName": "Action Required"},
            ],
        }
    )

    list_emails(
        account_id="acct-1",
        folder="Action Required",
        limit=5,
        include_body=False,
    )

    # Only the top-level listing should have been needed — no childFolders calls.
    paths_called = [call.args[0] for call in mock_paginated.call_args_list]
    assert paths_called[0] == "/me/mailFolders"
    assert not any("childFolders" in path for path in paths_called)

    call = mock_paginated.call_args
    assert call.args[0] == "/me/mailFolders/BBB=/messages"


@patch("microsoft_mcp.tools.graph.request_paginated")
def test_custom_folder_resolved_one_level_deep(mock_paginated):
    """Folders nested under Inbox (e.g. Inbox/Finance) should be
    discovered on the child-folder pass."""
    mock_paginated.side_effect = _fake_paginated_folders(
        {
            "/me/mailFolders": [
                {"id": "INBOX-ID", "displayName": "Inbox"},
                {"id": "ARCHIVE-ID", "displayName": "Archive"},
            ],
            "/me/mailFolders/INBOX-ID/childFolders": [
                {"id": "FIN-ID", "displayName": "Finance"},
                {"id": "LEGAL-ID", "displayName": "Legal & Privacy"},
            ],
            "/me/mailFolders/ARCHIVE-ID/childFolders": [],
        }
    )

    list_emails(
        account_id="acct-1",
        folder="Legal & Privacy",
        limit=5,
        include_body=False,
    )

    call = mock_paginated.call_args
    assert call.args[0] == "/me/mailFolders/LEGAL-ID/messages"


@patch("microsoft_mcp.tools.graph.request_paginated")
def test_custom_folder_match_is_case_insensitive(mock_paginated):
    """Custom-folder match must be case-insensitive (matches move_email
    behavior)."""
    mock_paginated.side_effect = _fake_paginated_folders(
        {"/me/mailFolders": [{"id": "CF-ID", "displayName": "Action Required"}]}
    )

    list_emails(
        account_id="acct-1",
        folder="action required",
        limit=5,
        include_body=False,
    )

    call = mock_paginated.call_args
    assert call.args[0] == "/me/mailFolders/CF-ID/messages"


@patch("microsoft_mcp.tools.graph.request_paginated")
def test_missing_custom_folder_raises_value_error(mock_paginated):
    """If a custom name can't be resolved at top level or one level deep,
    surface a ValueError rather than letting Graph reject a malformed ID."""
    mock_paginated.side_effect = _fake_paginated_folders(
        {
            "/me/mailFolders": [{"id": "INBOX-ID", "displayName": "Inbox"}],
            "/me/mailFolders/INBOX-ID/childFolders": [],
        }
    )

    with pytest.raises(ValueError, match="NoSuchFolder"):
        list_emails(
            account_id="acct-1",
            folder="NoSuchFolder",
            limit=5,
            include_body=False,
        )

    # Resolution failed, so the messages endpoint must never be reached.
    paths_called = [call.args[0] for call in mock_paginated.call_args_list]
    assert not any(path.endswith("/messages") for path in paths_called)


@patch("microsoft_mcp.tools.graph.request")
def test_resolve_helper_returns_none_when_missing(mock_request):
    """The helper itself returns None when no match is found — callers
    decide how to surface that."""
    mock_request.side_effect = [
        {"value": [{"id": "INBOX-ID", "displayName": "Inbox"}]},
        {"value": []},
    ]

    assert _resolve_folder_id("Missing", "acct-1") is None
