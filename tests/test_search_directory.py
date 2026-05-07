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
