"""Tests for folder resolution across multiple Graph pages (mcp-fk1).

_resolve_folder_id and move_email's inline resolver previously listed
folders via the non-paginated graph.request, which returns only Graph's
first page (~10 folders when $top is unset). Folders sorting beyond page
1 (e.g. newly created ones, which sort last in Graph's default
childFolders order) were never found. Both must enumerate folders via
graph.request_paginated, which follows @odata.nextLink.
"""

from unittest.mock import patch

from microsoft_mcp.tools import (
    _resolve_folder_id,
    move_email,
)


@patch("microsoft_mcp.graph.request")
def test_resolve_folder_id_finds_top_level_folder_beyond_first_page(mock_request):
    """A top-level folder that only appears on Graph's second page must
    still resolve — this was the mcp-fk1 bug. Dispatches by exact path so
    a buggy single-page implementation can't pass by accidentally
    consuming the page-2 fixture via the childFolders call instead."""

    def fake(method, path, account_id, **kwargs):
        if path == "/me/mailFolders":
            return {
                "value": [{"id": "A", "displayName": "Inbox"}],
                "@odata.nextLink": (
                    "https://graph.microsoft.com/v1.0/me/mailFolders?$skiptoken=1"
                ),
            }
        if path == "/me/mailFolders?$skiptoken=1":
            return {"value": [{"id": "B", "displayName": "Partners"}]}
        if path == "/me/mailFolders/A/childFolders":
            return {"value": []}
        raise AssertionError(f"unexpected path: {path}")

    mock_request.side_effect = fake

    assert _resolve_folder_id("Partners", "acct-1") == "B"


@patch("microsoft_mcp.graph.request")
def test_resolve_folder_id_finds_child_folder_beyond_first_page(mock_request):
    """A child folder that only appears on the childFolders endpoint's
    second page must still resolve."""
    top = {"value": [{"id": "INBOX-ID", "displayName": "Inbox"}]}
    children_page1 = {
        "value": [{"id": "FIN-ID", "displayName": "Finance"}],
        "@odata.nextLink": (
            "https://graph.microsoft.com/v1.0/me/mailFolders/INBOX-ID/"
            "childFolders?$skiptoken=1"
        ),
    }
    children_page2 = {
        "value": [{"id": "RC-ID", "displayName": "Research Collaborators"}]
    }
    mock_request.side_effect = [top, children_page1, children_page2]

    assert _resolve_folder_id("Research Collaborators", "acct-1") == "RC-ID"


@patch("microsoft_mcp.graph.request")
def test_move_email_resolves_folder_beyond_first_page(mock_request):
    """move_email must find destination folders beyond Graph's first
    page, same as list_emails. Dispatches by exact path/method so a
    buggy single-page implementation can't pass by accident."""

    def fake(method, path, account_id=None, **kwargs):
        if method == "GET" and path == "/me/mailFolders":
            return {
                "value": [{"id": "A", "displayName": "Inbox"}],
                "@odata.nextLink": (
                    "https://graph.microsoft.com/v1.0/me/mailFolders?$skiptoken=1"
                ),
            }
        if method == "GET" and path == "/me/mailFolders?$skiptoken=1":
            return {"value": [{"id": "B", "displayName": "Receipts"}]}
        if method == "GET" and path == "/me/mailFolders/A/childFolders":
            return {"value": []}
        if method == "POST" and path == "/me/messages/msg-1/move":
            return {"id": "moved-msg-id"}
        raise AssertionError(f"unexpected call: {method} {path}")

    mock_request.side_effect = fake

    result = move_email(
        email_id="msg-1", destination_folder="Receipts", account_id="acct-1"
    )

    assert result == {"status": "moved", "new_id": "moved-msg-id"}
