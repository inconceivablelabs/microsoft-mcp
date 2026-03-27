"""Tests for AI insight tools."""

from unittest.mock import patch
from microsoft_mcp.tools import list_ai_insights as _list_ai_insights_tool
from microsoft_mcp.tools import get_ai_insight as _get_ai_insight_tool

list_ai_insights = _list_ai_insights_tool.fn
get_ai_insight = _get_ai_insight_tool.fn


@patch("microsoft_mcp.tools.graph.request")
def test_list_ai_insights_path(mock_request):
    mock_request.return_value = {
        "value": [{"id": "insight-1", "createdDateTime": "2026-03-27T10:00:00Z"}]
    }
    result = list_ai_insights(meeting_id="meeting-1", account_id="user-oid.tenant-id")
    mock_request.assert_called_once_with(
        "GET",
        "/copilot/users/user-oid/onlineMeetings/meeting-1/aiInsights",
        "user-oid.tenant-id",
    )
    assert len(result) == 1


@patch("microsoft_mcp.tools.graph.request")
def test_list_ai_insights_extracts_user_id(mock_request):
    mock_request.return_value = {"value": []}
    list_ai_insights(
        meeting_id="meeting-1",
        account_id="39c06527-7e99-4ca1-b64d-9b552df9ee5c.63bce28d-c18c-425b-bce3-c2797ce9f182",
    )
    call_args = mock_request.call_args
    path = call_args[0][1]
    assert "/copilot/users/39c06527-7e99-4ca1-b64d-9b552df9ee5c/" in path


@patch("microsoft_mcp.tools.graph.request")
def test_list_ai_insights_empty(mock_request):
    mock_request.return_value = None
    result = list_ai_insights(meeting_id="meeting-1", account_id="oid.tid")
    assert result == []


@patch("microsoft_mcp.tools.graph.request")
def test_get_ai_insight_path(mock_request):
    mock_request.return_value = {
        "id": "insight-1",
        "meetingNotes": [{"title": "Summary", "text": "We discussed X"}],
        "actionItems": [{"title": "Do Y", "ownerDisplayName": "Tom"}],
    }
    result = get_ai_insight(
        meeting_id="meeting-1", insight_id="insight-1", account_id="user-oid.tenant-id",
    )
    mock_request.assert_called_once_with(
        "GET",
        "/copilot/users/user-oid/onlineMeetings/meeting-1/aiInsights/insight-1",
        "user-oid.tenant-id",
    )
    assert len(result["meetingNotes"]) == 1
    assert len(result["actionItems"]) == 1


@patch("microsoft_mcp.tools.graph.request")
def test_get_ai_insight_not_found(mock_request):
    mock_request.return_value = None
    try:
        get_ai_insight(meeting_id="meeting-1", insight_id="nonexistent", account_id="oid.tid")
        assert False, "Expected ValueError"
    except ValueError as e:
        assert "not found" in str(e).lower()
