"""Tests for Graph API error detail surfacing in graph.request()."""

from unittest.mock import patch, MagicMock
import httpx
import pytest
from microsoft_mcp.graph import request, request_text, download_raw


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


# --- innerError.code surfacing (mcp-xby) ---
#
# Microsoft's calltranscript-get reference is explicit that callers must branch on
# innerError.code, not on the message text: "This API is governed by tenant
# administrator settings for transcript access and speaker attribution... Messages
# are subject to change."
# https://learn.microsoft.com/en-us/graph/api/calltranscript-get?view=graph-rest-1.0
#
# The two 403s below are indistinguishable by error.code alone -- both are
# "AccessDenied" -- and have different fixes:
#   GraphAccessToTranscriptsDisabled -> -EnableGraphTranscriptAccess is off
#   SpeakerAttributionNotAllowed     -> -EnableAttributedTranscripts is off; the
#                                       SAME request succeeds unattributed


def _transcript_403_body(inner_code: str) -> dict:
    return {
        "error": {
            "code": "AccessDenied",
            "message": "Access denied.",
            "innerError": {"code": inner_code},
        }
    }


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_request_surfaces_inner_error_code(mock_token, mock_client):
    """request() includes innerError.code, not just the outer code and message."""
    mock_client.request.return_value = _mock_response(
        403, json_body=_transcript_403_body("SpeakerAttributionNotAllowed")
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        request("GET", "/me/onlineMeetings/1/transcripts", "acct-1")
    assert "SpeakerAttributionNotAllowed" in str(exc_info.value)


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_request_text_403_surfaces_graph_error_detail(mock_token, mock_client):
    """request_text() surfaces the Graph error code and message on 4xx.

    Previously it called raise_for_status() bare, so the transcript CONTENT phase
    reported only httpx's generic 'Client error 403 Forbidden for url ...' while
    the LIST phase reported Graph's actual message.
    """
    mock_client.request.return_value = _mock_response(
        403, json_body=_transcript_403_body("GraphAccessToTranscriptsDisabled")
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        request_text("/me/onlineMeetings/1/transcripts/2/content", "acct-1")
    assert "AccessDenied" in str(exc_info.value)
    assert "Access denied." in str(exc_info.value)


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_request_text_distinguishes_the_two_transcript_403s(mock_token, mock_client):
    """The whole point of mcp-xby: the two tenant-flag 403s must be tellable apart.

    Same status, same outer error.code -- only innerError.code differs.
    """
    mock_client.request.return_value = _mock_response(
        403, json_body=_transcript_403_body("SpeakerAttributionNotAllowed")
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        request_text("/me/onlineMeetings/1/transcripts/2/content", "acct-1")
    assert "SpeakerAttributionNotAllowed" in str(exc_info.value)
    assert "GraphAccessToTranscriptsDisabled" not in str(exc_info.value)


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_request_text_non_json_body_still_raises(mock_token, mock_client):
    """A 4xx with a non-JSON body still raises, it just carries no added detail."""
    mock_client.request.return_value = _mock_response(403, text_body="Forbidden")
    with pytest.raises(httpx.HTTPStatusError):
        request_text("/me/onlineMeetings/1/transcripts/2/content", "acct-1")


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_download_raw_403_surfaces_graph_error_detail(mock_token, mock_client):
    """download_raw() surfaces Graph error detail on 4xx -- same class as request_text.

    Not named in mcp-xby; found by the bead's own scope check.
    """
    mock_client.get.return_value = _mock_response(
        403, json_body=_transcript_403_body("SpeakerAttributionNotAllowed")
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        download_raw("/me/drive/items/1/content", "acct-1")
    assert "AccessDenied" in str(exc_info.value)
    assert "SpeakerAttributionNotAllowed" in str(exc_info.value)


@patch("microsoft_mcp.graph._client")
@patch("microsoft_mcp.graph.get_token", return_value="fake-token")
def test_request_text_500_still_retries(mock_token, mock_client):
    """5xx still retries in request_text -- detail surfacing must not break it."""
    fail_resp = MagicMock(spec=httpx.Response)
    fail_resp.status_code = 500
    fail_resp.headers = {}
    fail_resp.request = MagicMock(spec=httpx.Request)

    ok_resp = MagicMock(spec=httpx.Response)
    ok_resp.status_code = 200
    ok_resp.headers = {}
    ok_resp.text = "WEBVTT"
    ok_resp.raise_for_status = MagicMock()

    mock_client.request.side_effect = [fail_resp, ok_resp]
    result = request_text("/me/onlineMeetings/1/transcripts/2/content", "acct-1")
    assert result == "WEBVTT"
    assert mock_client.request.call_count == 2


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
