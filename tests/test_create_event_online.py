"""Tests for create_event online meeting enhancement."""

from unittest.mock import patch
from microsoft_mcp.tools import create_event as _create_event_tool

create_event = _create_event_tool.fn


@patch("microsoft_mcp.tools.graph.request")
def test_create_event_with_online_meeting(mock_request):
    """When is_online_meeting=True, should set isOnlineMeeting and provider."""
    mock_request.return_value = {
        "id": "event-1",
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness",
    }

    result = create_event(
        account_id="acct-1",
        subject="Sprint Planning",
        start="2026-03-28T10:00:00",
        end="2026-03-28T11:00:00",
        is_online_meeting=True,
    )

    call_args = mock_request.call_args
    event_json = call_args.kwargs.get("json") or call_args[1].get("json")
    assert event_json["isOnlineMeeting"] is True
    assert event_json["onlineMeetingProvider"] == "teamsForBusiness"
    assert result["isOnlineMeeting"] is True


@patch("microsoft_mcp.tools.graph.request")
def test_create_event_without_online_meeting(mock_request):
    """When is_online_meeting is not set, should NOT include online meeting fields."""
    mock_request.return_value = {"id": "event-1"}

    create_event(
        account_id="acct-1",
        subject="Lunch",
        start="2026-03-28T12:00:00",
        end="2026-03-28T13:00:00",
    )

    call_args = mock_request.call_args
    event_json = call_args.kwargs.get("json") or call_args[1].get("json")
    assert "isOnlineMeeting" not in event_json
    assert "onlineMeetingProvider" not in event_json


@patch("microsoft_mcp.tools.graph.request")
def test_create_event_custom_provider(mock_request):
    """Should allow overriding the online meeting provider."""
    mock_request.return_value = {"id": "event-1"}

    create_event(
        account_id="acct-1",
        subject="Call",
        start="2026-03-28T14:00:00",
        end="2026-03-28T14:30:00",
        is_online_meeting=True,
        online_meeting_provider="skypeForBusiness",
    )

    call_args = mock_request.call_args
    event_json = call_args.kwargs.get("json") or call_args[1].get("json")
    assert event_json["onlineMeetingProvider"] == "skypeForBusiness"
