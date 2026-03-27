"""Tests for graph.request_text() helper."""

from unittest.mock import patch, MagicMock
from microsoft_mcp.graph import request_text


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_request_text_returns_raw_text(mock_token, mock_client):
    """request_text() should return response body as a string, not parsed JSON."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello world"
    mock_response.headers = {}
    mock_response.raise_for_status = MagicMock()
    mock_client.request.return_value = mock_response

    result = request_text("/me/onlineMeetings/123/transcripts/456/content", "acct-1")

    assert result == "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello world"
    assert isinstance(result, str)


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_request_text_passes_accept_header(mock_token, mock_client):
    """request_text() should set the Accept header to the requested format."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "plain transcript text"
    mock_response.headers = {}
    mock_response.raise_for_status = MagicMock()
    mock_client.request.return_value = mock_response

    request_text(
        "/me/onlineMeetings/123/transcripts/456/content",
        "acct-1",
        accept="text/plain",
    )

    call_kwargs = mock_client.request.call_args
    headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
    assert headers["Accept"] == "text/plain"


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_request_text_default_accept_is_vtt(mock_token, mock_client):
    """Default accept header should be text/vtt."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "WEBVTT\n\n..."
    mock_response.headers = {}
    mock_response.raise_for_status = MagicMock()
    mock_client.request.return_value = mock_response

    request_text("/some/path", "acct-1")

    call_kwargs = mock_client.request.call_args
    headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
    assert headers["Accept"] == "text/vtt"


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_request_text_retries_on_429(mock_token, mock_client):
    """request_text() should retry on 429 rate limiting."""
    mock_429 = MagicMock()
    mock_429.status_code = 429
    mock_429.headers = {"Retry-After": "0"}

    mock_200 = MagicMock()
    mock_200.status_code = 200
    mock_200.text = "transcript content"
    mock_200.headers = {}
    mock_200.raise_for_status = MagicMock()

    mock_client.request.side_effect = [mock_429, mock_200]

    result = request_text("/some/path", "acct-1")
    assert result == "transcript content"
    assert mock_client.request.call_count == 2


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_request_text_retries_on_500(mock_token, mock_client):
    """request_text() should retry on 500+ server errors."""
    mock_500 = MagicMock()
    mock_500.status_code = 500
    mock_500.headers = {}

    mock_200 = MagicMock()
    mock_200.status_code = 200
    mock_200.text = "transcript content"
    mock_200.headers = {}
    mock_200.raise_for_status = MagicMock()

    mock_client.request.side_effect = [mock_500, mock_200]

    result = request_text("/some/path", "acct-1")
    assert result == "transcript content"
    assert mock_client.request.call_count == 2
