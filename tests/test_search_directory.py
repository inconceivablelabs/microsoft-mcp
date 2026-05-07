"""Tests for search_directory (pa-b14f).

Two-tier directory search: /me/people relevance-ranked primary,
/users $search fallback. Returns a normalized row shape so callers
don't have to branch on which endpoint produced the row.
"""

from unittest.mock import patch
from microsoft_mcp.tools import search_directory as _search_directory_tool

search_directory = _search_directory_tool.fn


@patch("microsoft_mcp.tools.graph.request")
def test_me_people_primary_returns_normalized_rows(mock_request):
    """When /me/people returns matches, /users is not called and rows are normalized."""
    mock_request.return_value = {
        "value": [
            {
                "displayName": "Brittany Alley",
                "scoredEmailAddresses": [
                    {"address": "balley@caringbridge.org", "relevanceScore": 0.95}
                ],
                "jobTitle": "VP Product",
                "department": "Product",
                "personType": {"class": "Person", "subclass": "OrganizationUser"},
            }
        ]
    }

    rows = search_directory(query="Brittany", account_id="acct-1", limit=5)

    # /users fallback (added in Task B2) is not invoked when /me/people returns rows.
    assert mock_request.call_count == 1
    call = mock_request.call_args
    assert call.args[0] == "GET"
    assert call.args[1] == "/me/people"
    assert call.kwargs["params"]["$search"] == '"Brittany"'
    assert call.kwargs["params"]["$top"] == 5
    assert "displayName,scoredEmailAddresses" in call.kwargs["params"]["$select"]

    assert rows == [
        {
            "name": "Brittany Alley",
            "email": "balley@caringbridge.org",
            "job_title": "VP Product",
            "department": "Product",
            "person_type": "OrganizationUser",
            "source": "people",
        }
    ]


@patch("microsoft_mcp.tools.graph.request")
def test_users_fallback_when_people_returns_empty(mock_request):
    """/users fallback fires when /me/people returns 0 rows."""
    mock_request.side_effect = [
        {"value": []},  # /me/people returns nothing
        {
            "value": [
                {
                    "id": "user-id-1",
                    "displayName": "Casey Kim",
                    "mail": "ckim@caringbridge.org",
                    "userPrincipalName": "ckim@caringbridge.org",
                    "jobTitle": "Engineer",
                    "department": "Eng",
                }
            ]
        },
    ]

    rows = search_directory(query="Casey", account_id="acct-1", limit=10)

    assert mock_request.call_count == 2
    second_call = mock_request.call_args_list[1]
    assert second_call.args[0] == "GET"
    assert second_call.args[1] == "/users"
    assert (
        second_call.kwargs["params"]["$search"] == '"displayName:Casey" OR "mail:Casey"'
    )
    assert (
        "displayName,mail,userPrincipalName" in second_call.kwargs["params"]["$select"]
    )
    assert "id" in second_call.kwargs["params"]["$select"]

    assert rows == [
        {
            "name": "Casey Kim",
            "email": "ckim@caringbridge.org",
            "job_title": "Engineer",
            "department": "Eng",
            "person_type": None,
            "source": "users",
        }
    ]


@patch("microsoft_mcp.tools.graph.request")
def test_users_row_falls_back_to_upn_when_mail_null(mock_request):
    """Service-account / shared-mailbox rows have null mail; fall back to UPN."""
    mock_request.side_effect = [
        {"value": []},
        {
            "value": [
                {
                    "id": "user-id-2",
                    "displayName": "Shared Inbox",
                    "mail": None,
                    "userPrincipalName": "sharedbox@caringbridge.org",
                    "jobTitle": None,
                    "department": None,
                }
            ]
        },
    ]
    rows = search_directory(query="shared", account_id="acct-1", limit=5)
    assert rows[0]["email"] == "sharedbox@caringbridge.org"
