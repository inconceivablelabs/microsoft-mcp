"""Tests for list_events' $select (pa-rrue.8/.9).

Janet's morning brief drops events Tom only tentatively accepted or is
"Following" (responseStatus.response == "tentativelyAccepted") and personal
"Purple category" events. Neither field was selected, and the brief's forward
fetch uses include_details=False, so BOTH modes must select them. Graph event
properties: https://learn.microsoft.com/en-us/graph/api/resources/event

Asserts on the argument handed to graph.request_paginated, because a mocked
response would contain whatever fields the fixture says regardless of what
was selected.
"""

from unittest.mock import patch

import pytest

from microsoft_mcp.tools import list_events

NEW_FIELDS = {"categories", "responseStatus"}

# Fields each mode selected before pa-rrue.8/.9; a rewrite must not drop them.
PRIOR_FIELDS = {
    True: {
        "id",
        "subject",
        "start",
        "end",
        "location",
        "body",
        "attendees",
        "organizer",
        "isAllDay",
        "recurrence",
        "onlineMeeting",
        "seriesMasterId",
    },
    False: {
        "id",
        "subject",
        "start",
        "end",
        "location",
        "organizer",
        "seriesMasterId",
    },
}


def _selected_fields(include_details: bool) -> set[str]:
    with patch(
        "microsoft_mcp.graph.request_paginated", return_value=iter([])
    ) as mock_paginated:
        list_events("acct-1", include_details=include_details)

    mock_paginated.assert_called_once()
    args, kwargs = mock_paginated.call_args
    assert args[0] == "/me/calendarView"
    return set(kwargs["params"]["$select"].split(","))


@pytest.mark.parametrize("include_details", [True, False])
def test_select_includes_categories_and_response_status(include_details):
    selected = _selected_fields(include_details)
    missing = NEW_FIELDS - selected
    assert not missing, f"include_details={include_details} missing {missing}"


@pytest.mark.parametrize("include_details", [True, False])
def test_select_keeps_prior_fields(include_details):
    selected = _selected_fields(include_details)
    missing = PRIOR_FIELDS[include_details] - selected
    assert not missing, f"include_details={include_details} dropped {missing}"


@pytest.mark.parametrize("include_details", [True, False])
def test_select_includes_is_all_day(include_details):
    # pa-rrue.11: the brief's include_details=False fetch reads isAllDay to
    # render all-day events; without it every all-day event parses as timed.
    selected = _selected_fields(include_details)
    assert "isAllDay" in selected, f"include_details={include_details} missing isAllDay"
