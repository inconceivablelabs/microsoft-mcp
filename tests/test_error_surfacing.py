"""Tests for Graph API error detail surfacing in graph.request()."""

from unittest.mock import patch, MagicMock
import httpx
import pytest
from microsoft_mcp.graph import request


def _mock_response(status_code, json_body=None, text_body=None):
    """Create a mock httpx response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = {}
    resp.request = MagicMock(spec=httpx.Request)
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = Exception("Not JSON")
    if text_body is not None:
        resp.text = text_body
    resp.content = b"content"
    # Make raise_for_status behave like real httpx
    real_response = httpx.Response(status_code, request=httpx.Request("GET", "https://example.com"))
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"{status_code}", request=resp.request, response=resp
    )
    return resp


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_400_with_graph_error_includes_detail(mock_token, mock_client):
    """400 response with Graph error JSON includes error code and message."""
    mock_client.request.return_value = _mock_response(
        400,
        json_body={
            "error": {
                "code": "ErrorOccurrenceCrossingBoundary",
                "message": "Cannot move occurrence past adjacent occurrence.",
            }
        },
    )
    with pytest.raises(httpx.HTTPStatusError, match="ErrorOccurrenceCrossingBoundary"):
        request("PATCH", "/me/events/123", "acct-1", json={"subject": "test"})


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_403_with_graph_error_includes_detail(mock_token, mock_client):
    """403 response includes error detail — works for all 4xx, not just 400."""
    mock_client.request.return_value = _mock_response(
        403,
        json_body={
            "error": {
                "code": "Authorization_RequestDenied",
                "message": "Insufficient privileges to complete the operation.",
            }
        },
    )
    with pytest.raises(httpx.HTTPStatusError, match="Authorization_RequestDenied"):
        request("GET", "/me/events", "acct-1")


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_400_non_json_falls_through(mock_token, mock_client):
    """400 with non-JSON body falls through to standard raise_for_status."""
    mock_client.request.return_value = _mock_response(400, text_body="Bad Request")
    with pytest.raises(httpx.HTTPStatusError):
        request("GET", "/me/events", "acct-1")


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_400_error_message_includes_both_code_and_message(mock_token, mock_client):
    """Error message contains both the code and the descriptive message."""
    mock_client.request.return_value = _mock_response(
        400,
        json_body={
            "error": {
                "code": "BadRequest",
                "message": "Property 'start' must include 'dateTime' and 'timeZone'.",
            }
        },
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        request("PATCH", "/me/events/123", "acct-1", json={"start": "bad"})
    assert "BadRequest" in str(exc_info.value)
    assert "dateTime" in str(exc_info.value)


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_500_still_retries(mock_token, mock_client):
    """5xx responses still retry — error surfacing doesn't break existing behavior."""
    fail_resp = MagicMock(spec=httpx.Response)
    fail_resp.status_code = 500
    fail_resp.headers = {}
    fail_resp.request = MagicMock(spec=httpx.Request)
    fail_resp.content = b""

    ok_resp = MagicMock(spec=httpx.Response)
    ok_resp.status_code = 200
    ok_resp.headers = {}
    ok_resp.content = b'{"value": "ok"}'
    ok_resp.json.return_value = {"value": "ok"}
    ok_resp.raise_for_status = MagicMock()

    mock_client.request.side_effect = [fail_resp, ok_resp]
    result = request("GET", "/me/events", "acct-1", max_retries=3)
    assert result == {"value": "ok"}
    assert mock_client.request.call_count == 2
