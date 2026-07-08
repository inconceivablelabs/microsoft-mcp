"""Tests for transcript tools."""

from unittest.mock import patch
from microsoft_mcp.tools import list_transcripts as _list_transcripts_tool
from microsoft_mcp.tools import get_transcript_content as _get_transcript_content_tool

list_transcripts = _list_transcripts_tool.fn
get_transcript_content = _get_transcript_content_tool.fn


@patch("microsoft_mcp.tools.graph.request")
def test_list_transcripts(mock_request):
    mock_request.return_value = {
        "value": [{"id": "transcript-1", "createdDateTime": "2026-03-27T10:00:00Z"}]
    }
    result = list_transcripts(meeting_id="meeting-1", account_id="acct-1")
    # Routed through request_paginated with an explicit $top so Graph doesn't
    # cap the response at its ~20-item default page.
    mock_request.assert_called_once_with(
        "GET",
        "/me/onlineMeetings/meeting-1/transcripts",
        "acct-1",
        params={"$top": 100},
    )
    assert len(result) == 1
    assert result[0]["id"] == "transcript-1"


@patch("microsoft_mcp.tools.graph.request")
def test_list_transcripts_empty(mock_request):
    mock_request.return_value = {"value": []}
    result = list_transcripts(meeting_id="meeting-1", account_id="acct-1")
    assert result == []


@patch("microsoft_mcp.tools.graph.request")
def test_list_transcripts_none_response(mock_request):
    mock_request.return_value = None
    result = list_transcripts(meeting_id="meeting-1", account_id="acct-1")
    assert result == []


@patch("microsoft_mcp.tools.graph.request")
def test_list_transcripts_follows_pagination(mock_request):
    """Graph paginates /transcripts (~20/page). list_transcripts must follow
    @odata.nextLink and return ALL transcripts, not just the first page.
    Regression: the tool used graph.request (single page) and silently dropped
    everything beyond ~20 (pa-umrt)."""
    page1 = {
        "value": [
            {"id": f"t{i}", "createdDateTime": "2026-01-01T00:00:00Z"}
            for i in range(20)
        ],
        "@odata.nextLink": (
            "https://graph.microsoft.com/v1.0/me/onlineMeetings/meeting-1/transcripts?$skiptoken=abc"
        ),
    }
    page2 = {
        "value": [
            {"id": f"t{i}", "createdDateTime": "2026-01-01T00:00:00Z"}
            for i in range(20, 25)
        ]
    }
    mock_request.side_effect = [page1, page2]

    result = list_transcripts(meeting_id="meeting-1", account_id="acct-1")

    assert len(result) == 25, "should aggregate across all pages, not cap at the first"
    assert [r["id"] for r in result] == [f"t{i}" for i in range(25)]
    assert mock_request.call_count == 2, "should have followed the nextLink to page 2"


@patch("microsoft_mcp.tools.graph.request_text")
def test_get_transcript_content_vtt(mock_request_text):
    mock_request_text.return_value = "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello"
    result = get_transcript_content(
        meeting_id="meeting-1",
        transcript_id="transcript-1",
        account_id="acct-1",
    )
    mock_request_text.assert_called_once_with(
        "/me/onlineMeetings/meeting-1/transcripts/transcript-1/content",
        "acct-1",
        accept="text/vtt",
    )
    assert result == "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello"


@patch("microsoft_mcp.tools.graph.request_text")
def test_get_transcript_content_plain(mock_request_text):
    mock_request_text.return_value = "Hello world"
    get_transcript_content(
        meeting_id="meeting-1",
        transcript_id="transcript-1",
        account_id="acct-1",
        content_format="text/plain",
    )
    mock_request_text.assert_called_once_with(
        "/me/onlineMeetings/meeting-1/transcripts/transcript-1/content",
        "acct-1",
        accept="text/plain",
    )


@patch("microsoft_mcp.tools.graph.request_text")
def test_get_transcript_content_truncation(mock_request_text):
    mock_request_text.return_value = "A" * 10000
    result = get_transcript_content(
        meeting_id="meeting-1",
        transcript_id="transcript-1",
        account_id="acct-1",
        max_length=100,
    )
    assert len(result) <= 150
    assert result.startswith("A" * 100)
    assert "truncated" in result.lower()


@patch("microsoft_mcp.tools.graph.request_text")
def test_get_transcript_content_no_truncation_by_default(mock_request_text):
    long_content = "A" * 100000
    mock_request_text.return_value = long_content
    result = get_transcript_content(
        meeting_id="meeting-1",
        transcript_id="transcript-1",
        account_id="acct-1",
    )
    assert result == long_content
