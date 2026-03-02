"""Tests for categories support in ms365_update_email."""

from unittest.mock import patch
from microsoft_mcp.tools import update_email as _update_email_tool

# FastMCP 2.8.0: @mcp.tool(name=...) wraps in FunctionTool; .fn is the raw callable
update_email = _update_email_tool.fn


@patch("microsoft_mcp.tools.graph.request")
def test_categories_passed_in_patch_body(mock_request):
    """When categories is provided, it should appear in the PATCH JSON body."""
    mock_request.return_value = {"id": "msg-1", "categories": ["Blue category"]}

    result = update_email(
        email_id="msg-1",
        account_id="acct-1",
        categories=["Blue category"],
    )

    mock_request.assert_called_once_with(
        "PATCH",
        "/me/messages/msg-1",
        "acct-1",
        json={"categories": ["Blue category"]},
    )
    assert result["categories"] == ["Blue category"]


@patch("microsoft_mcp.tools.graph.request")
def test_categories_merged_with_updates(mock_request):
    """categories param should merge with (and override) the updates dict."""
    mock_request.return_value = {
        "id": "msg-1",
        "isRead": True,
        "categories": ["Red category"],
    }

    result = update_email(
        email_id="msg-1",
        account_id="acct-1",
        updates={"isRead": True, "categories": ["Old category"]},
        categories=["Red category"],
    )

    # categories kwarg wins over updates dict
    mock_request.assert_called_once_with(
        "PATCH",
        "/me/messages/msg-1",
        "acct-1",
        json={"isRead": True, "categories": ["Red category"]},
    )
    assert result["categories"] == ["Red category"]


@patch("microsoft_mcp.tools.graph.request")
def test_updates_without_categories(mock_request):
    """When only updates is provided (no categories), it should work as before."""
    mock_request.return_value = {"id": "msg-1", "isRead": False}

    result = update_email(
        email_id="msg-1",
        account_id="acct-1",
        updates={"isRead": False},
    )

    mock_request.assert_called_once_with(
        "PATCH",
        "/me/messages/msg-1",
        "acct-1",
        json={"isRead": False},
    )
    assert result["isRead"] is False


@patch("microsoft_mcp.tools.graph.request")
def test_empty_categories_list_clears_categories(mock_request):
    """Passing an empty list should clear all categories on the message."""
    mock_request.return_value = {"id": "msg-1", "categories": []}

    result = update_email(
        email_id="msg-1",
        account_id="acct-1",
        categories=[],
    )

    mock_request.assert_called_once_with(
        "PATCH",
        "/me/messages/msg-1",
        "acct-1",
        json={"categories": []},
    )
    assert result["categories"] == []


def test_no_updates_and_no_categories_raises():
    """Calling with neither updates nor categories should raise ValueError."""
    try:
        update_email(email_id="msg-1", account_id="acct-1")
        assert False, "Expected ValueError"
    except ValueError as e:
        assert "Nothing to update" in str(e)
