"""Tests for Teams attendance-report tools (mcp-b49).

list_attendance_reports pages /me/onlineMeetings/{id}/attendanceReports;
get_attendance_report fetches the report summary then pages its
attendanceRecords child collection (NOT $expand, which truncates silently
on large meetings — mcp-fk1 class bug) and projects per participant.

Patching microsoft_mcp.graph.request covers request_paginated too, since
request_paginated delegates to request("GET", ...) per page (graph.py).
"""

from unittest.mock import patch

import pytest

from microsoft_mcp.tools import list_attendance_reports
from microsoft_mcp.tools import get_attendance_report


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


@patch("microsoft_mcp.graph.request")
def test_get_attendance_report_projects_and_pages_records(mock_request):
    """Summary + paged records → per-participant projection.

    Covers: two-call flow, records paged across 2 pages (child collection,
    not $expand), null identity (external attendee → name falls back to
    email, id is None), interval mapping.
    """

    def fake(method, path, account_id=None, **kwargs):
        if path == "/me/onlineMeetings/MID/attendanceReports/r1":
            return {
                "id": "r1",
                "meetingStartDateTime": "2026-07-16T16:00:13.7Z",
                "meetingEndDateTime": "2026-07-16T17:10:14.6Z",
                "totalParticipantCount": 2,
            }
        if path == "/me/onlineMeetings/MID/attendanceReports/r1/attendanceRecords":
            return {
                "value": [
                    {
                        "emailAddress": "tbooth@caringbridge.org",
                        "totalAttendanceInSeconds": 4185,
                        "role": "Organizer",
                        "identity": {"id": "oid-tom", "displayName": "Tom Booth"},
                        "attendanceIntervals": [
                            {
                                "joinDateTime": "2026-07-16T16:00:26Z",
                                "leaveDateTime": "2026-07-16T17:10:12Z",
                                "durationInSeconds": 4185,
                            }
                        ],
                    }
                ],
                "@odata.nextLink": (
                    "https://graph.microsoft.com/v1.0/me/onlineMeetings/MID/"
                    "attendanceReports/r1/attendanceRecords?$skiptoken=1"
                ),
            }
        if (
            path
            == "/me/onlineMeetings/MID/attendanceReports/r1/attendanceRecords?$skiptoken=1"
        ):
            return {
                "value": [
                    {
                        "emailAddress": "ext@vendor.com",
                        "totalAttendanceInSeconds": 120,
                        "role": "Attendee",
                        "identity": None,  # external/anonymous participant
                        "attendanceIntervals": [
                            {
                                "joinDateTime": "2026-07-16T16:05:00Z",
                                "leaveDateTime": "2026-07-16T16:07:00Z",
                                "durationInSeconds": 120,
                            }
                        ],
                    }
                ]
            }
        raise AssertionError(f"unexpected path: {path}")

    mock_request.side_effect = fake

    result = get_attendance_report("MID", "r1", "acct-1")

    assert result == {
        "meeting_start": "2026-07-16T16:00:13.7Z",
        "meeting_end": "2026-07-16T17:10:14.6Z",
        "participant_count": 2,
        "participants": [
            {
                "name": "Tom Booth",
                "email": "tbooth@caringbridge.org",
                "id": "oid-tom",
                "role": "Organizer",
                "total_seconds": 4185,
                "intervals": [
                    {
                        "join": "2026-07-16T16:00:26Z",
                        "leave": "2026-07-16T17:10:12Z",
                        "seconds": 4185,
                    }
                ],
            },
            {
                "name": "ext@vendor.com",
                "email": "ext@vendor.com",
                "id": None,
                "role": "Attendee",
                "total_seconds": 120,
                "intervals": [
                    {
                        "join": "2026-07-16T16:05:00Z",
                        "leave": "2026-07-16T16:07:00Z",
                        "seconds": 120,
                    }
                ],
            },
        ],
    }
    # Records fetched via the paginated child collection with $top=100.
    record_calls = [
        c
        for c in mock_request.call_args_list
        if c.args[1].startswith(
            "/me/onlineMeetings/MID/attendanceReports/r1/attendanceRecords"
        )
    ]
    assert record_calls[0].kwargs.get("params") == {"$top": 100}
    assert len(record_calls) == 2  # two pages fetched


@patch("microsoft_mcp.graph.request")
def test_get_attendance_report_raises_when_report_missing(mock_request):
    """None summary (bad/empty report) raises ValueError, not AttributeError."""
    mock_request.return_value = None
    with pytest.raises(ValueError):
        get_attendance_report("MID", "bad", "acct-1")


@patch("microsoft_mcp.graph.request")
def test_get_attendance_report_empty_records(mock_request):
    """A report with no attendance records → participants: []."""

    def fake(method, path, account_id=None, **kwargs):
        if path == "/me/onlineMeetings/MID/attendanceReports/r1":
            return {
                "meetingStartDateTime": "2026-07-16T16:00:00Z",
                "meetingEndDateTime": "2026-07-16T16:30:00Z",
                "totalParticipantCount": 0,
            }
        if path == "/me/onlineMeetings/MID/attendanceReports/r1/attendanceRecords":
            return {"value": []}
        raise AssertionError(f"unexpected path: {path}")

    mock_request.side_effect = fake
    result = get_attendance_report("MID", "r1", "acct-1")
    assert result["participants"] == []
    assert result["participant_count"] == 0
