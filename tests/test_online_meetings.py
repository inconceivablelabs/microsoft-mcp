"""Tests for online meeting tools."""

from unittest.mock import patch
from microsoft_mcp.tools import list_online_meetings as _list_online_meetings_tool
from microsoft_mcp.tools import get_online_meeting as _get_online_meeting_tool

list_online_meetings = _list_online_meetings_tool.fn
get_online_meeting = _get_online_meeting_tool.fn


@patch("microsoft_mcp.tools.graph.request")
def test_list_meetings_filter_by_join_url(mock_request):
    """When filter_join_url is provided, should embed filter in path to avoid double-encoding."""
    mock_request.return_value = {"value": [{"id": "meeting-1"}]}

    result = list_online_meetings(
        account_id="acct-1",
        filter_join_url="https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=%7b%22Tid%22%3a%22tid%22%7d",
    )

    call_args = mock_request.call_args
    path = call_args[0][1]
    # Verify the URL is in the path (not double-encoded via params)
    assert "JoinWebUrl eq" in path
    assert "teams.microsoft.com" in path
    assert len(result) == 1


@patch("microsoft_mcp.tools.graph.request")
def test_list_meetings_filter_by_meeting_id(mock_request):
    """When filter_join_meeting_id is provided, should use joinMeetingIdSettings filter."""
    mock_request.return_value = {"value": [{"id": "meeting-1"}]}

    list_online_meetings(
        account_id="acct-1",
        filter_join_meeting_id="283426623862",
    )

    call_args = mock_request.call_args
    params = call_args.kwargs.get("params") or call_args[0][3]
    assert "joinMeetingIdSettings/joinMeetingId eq" in params["$filter"]
    assert "283426623862" in params["$filter"]


def test_list_meetings_no_filter_raises():
    """Should raise ValueError when no filter is provided."""
    try:
        list_online_meetings(account_id="acct-1")
        assert False, "Expected ValueError"
    except ValueError as e:
        assert "required" in str(e).lower()


@patch("microsoft_mcp.tools.graph.request")
def test_list_meetings_empty_result(mock_request):
    """Should return empty list when no meetings match."""
    mock_request.return_value = None

    result = list_online_meetings(
        account_id="acct-1",
        filter_join_meeting_id="000000000000",
    )
    assert result == []


# --- get_online_meeting tests ---


@patch("microsoft_mcp.tools.graph.request")
def test_get_online_meeting(mock_request):
    """Should GET /me/onlineMeetings/{meetingId}."""
    mock_request.return_value = {"id": "meeting-1", "subject": "Standup"}
    result = get_online_meeting(meeting_id="meeting-1", account_id="acct-1")
    mock_request.assert_called_once_with(
        "GET", "/me/onlineMeetings/meeting-1", "acct-1"
    )
    assert result["subject"] == "Standup"


@patch("microsoft_mcp.tools.graph.request")
def test_get_online_meeting_not_found(mock_request):
    """Should raise ValueError when meeting not found."""
    mock_request.return_value = None
    try:
        get_online_meeting(meeting_id="nonexistent", account_id="acct-1")
        assert False, "Expected ValueError"
    except ValueError as e:
        assert "not found" in str(e).lower()
