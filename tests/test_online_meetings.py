"""Tests for online meeting tools."""

from unittest.mock import patch
from microsoft_mcp.tools import list_online_meetings as _list_online_meetings_tool
from microsoft_mcp.tools import get_online_meeting as _get_online_meeting_tool

list_online_meetings = _list_online_meetings_tool.fn
get_online_meeting = _get_online_meeting_tool.fn


@patch("microsoft_mcp.tools.graph.request_paginated")
def test_list_meetings_no_filter(mock_paginated):
    """With no filters, should call /me/onlineMeetings with $top."""
    mock_paginated.return_value = iter([{"id": "meeting-1", "subject": "Standup"}])
    result = list_online_meetings(account_id="acct-1", limit=10)
    mock_paginated.assert_called_once()
    assert len(result) == 1
    assert result[0]["subject"] == "Standup"


@patch("microsoft_mcp.tools.graph.request_paginated")
def test_list_meetings_filter_by_join_url(mock_paginated):
    """When filter_join_url is provided, should use $filter on joinWebUrl."""
    mock_paginated.return_value = iter([{"id": "meeting-1"}])
    list_online_meetings(
        account_id="acct-1",
        filter_join_url="https://teams.microsoft.com/l/meetup-join/abc",
    )
    call_args = mock_paginated.call_args
    params = call_args.kwargs.get("params") or call_args[0][2]
    assert "joinWebUrl eq" in params["$filter"]


@patch("microsoft_mcp.tools.graph.request_paginated")
def test_list_meetings_filter_by_date_range(mock_paginated):
    """When date range provided (no joinWebUrl), should filter on startDateTime."""
    mock_paginated.return_value = iter([])
    list_online_meetings(
        account_id="acct-1",
        start_datetime="2026-03-27T00:00:00Z",
        end_datetime="2026-03-28T00:00:00Z",
    )
    call_args = mock_paginated.call_args
    params = call_args.kwargs.get("params") or call_args[0][2]
    assert "startDateTime ge" in params["$filter"]
    assert "startDateTime le" in params["$filter"]


@patch("microsoft_mcp.tools.graph.request_paginated")
def test_list_meetings_join_url_takes_precedence(mock_paginated):
    """joinWebUrl filter should take precedence over date range if both provided."""
    mock_paginated.return_value = iter([])
    list_online_meetings(
        account_id="acct-1",
        filter_join_url="https://teams.microsoft.com/l/meetup-join/abc",
        start_datetime="2026-03-27T00:00:00Z",
        end_datetime="2026-03-28T00:00:00Z",
    )
    call_args = mock_paginated.call_args
    params = call_args.kwargs.get("params") or call_args[0][2]
    assert "joinWebUrl eq" in params["$filter"]
    assert "startDateTime" not in params["$filter"]


@patch("microsoft_mcp.tools.graph.request")
def test_get_online_meeting(mock_request):
    mock_request.return_value = {"id": "meeting-1", "subject": "Standup"}
    result = get_online_meeting(meeting_id="meeting-1", account_id="acct-1")
    mock_request.assert_called_once_with("GET", "/me/onlineMeetings/meeting-1", "acct-1")
    assert result["subject"] == "Standup"


@patch("microsoft_mcp.tools.graph.request")
def test_get_online_meeting_not_found(mock_request):
    mock_request.return_value = None
    try:
        get_online_meeting(meeting_id="nonexistent", account_id="acct-1")
        assert False, "Expected ValueError"
    except ValueError as e:
        assert "not found" in str(e).lower()
