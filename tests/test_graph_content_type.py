"""Tests for Content-Type header selection in graph.request().

Regression coverage for pa-69fc: an empty-dict `json={}` body was treated as
falsy and got `application/octet-stream`, causing Graph to reject otherwise
valid POSTs like `/me/events/{id}/cancel` whose body is `{}` when no comment
is supplied.
"""

from unittest.mock import patch, MagicMock
import httpx
from microsoft_mcp.graph import request


def _ok_response():
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 202
    resp.headers = {}
    resp.content = b""
    resp.request = MagicMock(spec=httpx.Request)
    resp.raise_for_status.return_value = None
    return resp


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_post_empty_json_dict_sets_application_json(mock_token, mock_client):
    """POST with json={} must set Content-Type: application/json, not octet-stream.

    Reproduces pa-69fc: `delete_event(send_cancellation=True)` calls
    `request("POST", ..., json={})`. Empty dict is Python-falsy, so the old
    `if json` check picked the octet-stream branch and Graph rejected the
    request as malformed.
    """
    mock_client.request.return_value = _ok_response()

    request("POST", "/me/events/abc/cancel", "acct-1", json={})

    headers = mock_client.request.call_args.kwargs["headers"]
    assert headers["Content-Type"] == "application/json"


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_post_non_empty_json_sets_application_json(mock_token, mock_client):
    """Sanity: the normal case (non-empty JSON body) still picks application/json."""
    mock_client.request.return_value = _ok_response()

    request("POST", "/me/sendMail", "acct-1", json={"message": {"subject": "hi"}})

    headers = mock_client.request.call_args.kwargs["headers"]
    assert headers["Content-Type"] == "application/json"


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_post_with_bytes_data_keeps_octet_stream(mock_token, mock_client):
    """Raw-bytes uploads (data=, json=None) must still use octet-stream."""
    mock_client.request.return_value = _ok_response()

    request("PUT", "/me/drive/items/x/content", "acct-1", data=b"file-bytes")

    headers = mock_client.request.call_args.kwargs["headers"]
    assert headers["Content-Type"] == "application/octet-stream"
