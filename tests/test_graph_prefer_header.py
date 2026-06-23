"""Tests for graph.request() prefer_body_text parameter (Task 1).

Covers the new explicit `prefer_body_text` flag added to graph.request(),
which allows callers to request body content as plain text via the
Prefer: outlook.body-content-type="text" header, independently of the
existing $search/$select auto-triggers.
"""

from unittest.mock import patch, MagicMock
import httpx
from microsoft_mcp.graph import request

PREFER_TEXT_HEADER = 'outlook.body-content-type="text"'


def _ok_response():
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.headers = {}
    resp.content = b'{"id": "abc"}'
    resp.request = MagicMock(spec=httpx.Request)
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"id": "abc"}
    return resp


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_prefer_body_text_true_sets_header(mock_token, mock_client):
    """prefer_body_text=True must set Prefer: outlook.body-content-type="text"."""
    mock_client.request.return_value = _ok_response()

    request("GET", "/me/messages/x", account_id="acct-1", prefer_body_text=True)

    headers = mock_client.request.call_args.kwargs["headers"]
    assert "Prefer" in headers
    assert headers["Prefer"] == PREFER_TEXT_HEADER


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_prefer_body_text_false_no_header(mock_token, mock_client):
    """prefer_body_text=False (default) with no $search/$select → no Prefer header."""
    mock_client.request.return_value = _ok_response()

    request("GET", "/me/messages/x", account_id="acct-1", prefer_body_text=False)

    headers = mock_client.request.call_args.kwargs["headers"]
    assert "Prefer" not in headers


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_default_no_prefer_header_without_triggers(mock_token, mock_client):
    """Default call (prefer_body_text not passed, no $search/$select) → no Prefer header."""
    mock_client.request.return_value = _ok_response()

    request("GET", "/me/messages/x", account_id="acct-1")

    headers = mock_client.request.call_args.kwargs["headers"]
    assert "Prefer" not in headers


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_existing_search_trigger_still_sets_header(mock_token, mock_client):
    """Regression: $search in params still auto-triggers the Prefer header."""
    mock_client.request.return_value = _ok_response()

    request(
        "GET",
        "/me/messages",
        account_id="acct-1",
        params={"$search": '"hello"'},
    )

    headers = mock_client.request.call_args.kwargs["headers"]
    assert "Prefer" in headers
    assert headers["Prefer"] == PREFER_TEXT_HEADER


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_existing_select_body_trigger_still_sets_header(mock_token, mock_client):
    """Regression: $select containing 'body' still auto-triggers the Prefer header."""
    mock_client.request.return_value = _ok_response()

    request(
        "GET",
        "/me/messages",
        account_id="acct-1",
        params={"$select": "id,subject,body,from"},
    )

    headers = mock_client.request.call_args.kwargs["headers"]
    assert "Prefer" in headers
    assert headers["Prefer"] == PREFER_TEXT_HEADER
