"""Tests for Teams attendance-report tools (mcp-b49).

list_attendance_reports pages /me/onlineMeetings/{id}/attendanceReports;
get_attendance_report fetches the report summary then pages its
attendanceRecords child collection (NOT $expand, which truncates silently
on large meetings — mcp-fk1 class bug) and projects per participant.

Patching microsoft_mcp.graph.request covers request_paginated too, since
request_paginated delegates to request("GET", ...) per page (graph.py).
"""

from unittest.mock import patch

from microsoft_mcp.tools import list_attendance_reports as _list_tool

list_attendance_reports = _list_tool.fn


@patch("microsoft_mcp.graph.request")
def test_list_attendance_reports_paginates_and_projects(mock_request):
    """Occurrences across two pages are aggregated and light-projected."""

    def fake(method, path, account_id=None, **kwargs):
        if path == "/me/onlineMeetings/MID/attendanceReports":
            return {
                "value": [
                    {
                        "id": "r1",
                        "meetingStartDateTime": "2026-07-16T16:00:13.7Z",
                        "meetingEndDateTime": "2026-07-16T17:10:14.6Z",
                        "totalParticipantCount": 5,
                    }
                ],
                "@odata.nextLink": (
                    "https://graph.microsoft.com/v1.0/me/onlineMeetings/MID/"
                    "attendanceReports?$skiptoken=1"
                ),
            }
        if path == "/me/onlineMeetings/MID/attendanceReports?$skiptoken=1":
            return {
                "value": [
                    {
                        "id": "r2",
                        "meetingStartDateTime": "2026-07-09T16:00:00Z",
                        "meetingEndDateTime": "2026-07-09T17:00:00Z",
                        "totalParticipantCount": 3,
                    }
                ]
            }
        raise AssertionError(f"unexpected path: {path}")

    mock_request.side_effect = fake

    result = list_attendance_reports("MID", "acct-1")

    assert result == [
        {
            "report_id": "r1",
            "meeting_start": "2026-07-16T16:00:13.7Z",
            "meeting_end": "2026-07-16T17:10:14.6Z",
            "participant_count": 5,
        },
        {
            "report_id": "r2",
            "meeting_start": "2026-07-09T16:00:00Z",
            "meeting_end": "2026-07-09T17:00:00Z",
            "participant_count": 3,
        },
    ]
    # Assert the FIRST page request carried $top=100 (paging contract).
    first = mock_request.call_args_list[0]
    assert first.args[0] == "GET"
    assert first.args[1] == "/me/onlineMeetings/MID/attendanceReports"
    assert first.kwargs.get("params") == {"$top": 100}


@patch("microsoft_mcp.graph.request")
def test_list_attendance_reports_empty(mock_request):
    """A meeting with no reports returns [] (empty collection, not error)."""
    mock_request.return_value = {"value": []}
    assert list_attendance_reports("MID", "acct-1") == []
