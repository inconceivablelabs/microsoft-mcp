"""Tests for get_email body_format parameter (Tasks 2 & 3).

Covers:
- Task 2: get_email wiring — body_format drives prefer_body_text on graph.request
- Task 3: body_format surfaces in the tool schema with text|html constraint + default
"""

from unittest.mock import patch
from microsoft_mcp.tools import get_email as _get_email_tool, mcp

# FastMCP 2.8.0: @mcp.tool wraps in FunctionTool; .fn is the raw callable
get_email = _get_email_tool.fn

TOOL_REGISTRY = mcp._tool_manager._tools

FAKE_MESSAGE = {
    "id": "msg-001",
    "subject": "Test Subject",
    "body": {
        "contentType": "text",
        "content": "Hello, world!",
    },
    "from": {"emailAddress": {"address": "sender@example.com", "name": "Sender"}},
    "toRecipients": [
        {"emailAddress": {"address": "recipient@example.com", "name": "Recipient"}}
    ],
}


def _fake_message_with_attachments():
    msg = dict(FAKE_MESSAGE)
    msg["attachments"] = [
        {
            "id": "att-1",
            "name": "file.pdf",
            "size": 1024,
            "contentType": "application/pdf",
        }
    ]
    return msg


@patch("microsoft_mcp.tools.address_resolution.resolve_x500_in_message")
@patch("microsoft_mcp.tools.graph.request")
def test_get_email_default_uses_prefer_body_text(mock_request, mock_resolve):
    """Default get_email() call → graph.request called with prefer_body_text=True."""
    mock_request.return_value = dict(FAKE_MESSAGE)

    get_email(email_id="msg-001", account_id="acct-1")

    mock_request.assert_called_once()
    call_kwargs = mock_request.call_args.kwargs
    assert call_kwargs.get("prefer_body_text") is True


@patch("microsoft_mcp.tools.address_resolution.resolve_x500_in_message")
@patch("microsoft_mcp.tools.graph.request")
def test_get_email_text_format_uses_prefer_body_text(mock_request, mock_resolve):
    """body_format="text" → graph.request called with prefer_body_text=True."""
    mock_request.return_value = dict(FAKE_MESSAGE)

    get_email(email_id="msg-001", account_id="acct-1", body_format="text")

    mock_request.assert_called_once()
    call_kwargs = mock_request.call_args.kwargs
    assert call_kwargs.get("prefer_body_text") is True


@patch("microsoft_mcp.tools.address_resolution.resolve_x500_in_message")
@patch("microsoft_mcp.tools.graph.request")
def test_get_email_html_format_no_prefer_body_text(mock_request, mock_resolve):
    """body_format="html" → graph.request called with prefer_body_text=False."""
    mock_request.return_value = dict(FAKE_MESSAGE)

    get_email(email_id="msg-001", account_id="acct-1", body_format="html")

    mock_request.assert_called_once()
    call_kwargs = mock_request.call_args.kwargs
    assert call_kwargs.get("prefer_body_text") is False


@patch("microsoft_mcp.tools.address_resolution.resolve_x500_in_message")
@patch("microsoft_mcp.tools.graph.request")
def test_get_email_returns_message_dict(mock_request, mock_resolve):
    """get_email returns the message dict from graph.request unchanged (plus x500 rewrite)."""
    mock_request.return_value = dict(FAKE_MESSAGE)

    result = get_email(email_id="msg-001", account_id="acct-1")

    assert result["id"] == "msg-001"
    assert result["subject"] == "Test Subject"
    assert "body" in result


@patch("microsoft_mcp.tools.address_resolution.resolve_x500_in_message")
@patch("microsoft_mcp.tools.graph.request")
def test_get_email_body_truncation_unchanged(mock_request, mock_resolve):
    """Body truncation behavior is unaffected by body_format parameter."""
    long_body = "x" * 60000
    msg = dict(FAKE_MESSAGE)
    msg["body"] = {"contentType": "text", "content": long_body}
    mock_request.return_value = msg

    result = get_email(
        email_id="msg-001",
        account_id="acct-1",
        body_format="text",
        body_max_length=50000,
    )

    assert result["body"]["truncated"] is True
    assert result["body"]["total_length"] == 60000
    assert len(result["body"]["content"]) < 60000


@patch("microsoft_mcp.tools.address_resolution.resolve_x500_in_message")
@patch("microsoft_mcp.tools.graph.request")
def test_get_email_attachments_unaffected(mock_request, mock_resolve):
    """Attachments behavior is unaffected by body_format parameter."""
    mock_request.return_value = _fake_message_with_attachments()

    result = get_email(email_id="msg-001", account_id="acct-1", body_format="text")

    assert "attachments" in result
    assert len(result["attachments"]) == 1
    assert result["attachments"][0]["name"] == "file.pdf"


# --- Task 3: Schema tests ---


def test_get_email_schema_has_body_format():
    """body_format must appear in get_email's FastMCP tool schema."""
    tool = TOOL_REGISTRY.get("get_email")
    assert tool is not None, "get_email tool not registered"

    schema = tool.parameters
    props = schema.get("properties", {})
    assert "body_format" in props, (
        f"body_format not in get_email schema properties: {list(props.keys())}"
    )


def test_get_email_schema_body_format_has_enum():
    """body_format schema must enumerate 'text' and 'html' as allowed values."""
    tool = TOOL_REGISTRY.get("get_email")
    assert tool is not None, "get_email tool not registered"
    schema = tool.parameters
    bf_schema = schema["properties"]["body_format"]

    # FastMCP may represent Literal as enum or anyOf; handle both
    if "enum" in bf_schema:
        allowed = bf_schema["enum"]
    elif "anyOf" in bf_schema:
        allowed = []
        for item in bf_schema["anyOf"]:
            if "enum" in item:
                allowed.extend(item["enum"])
            if "const" in item:
                allowed.append(item["const"])
    else:
        raise AssertionError(f"body_format schema has no enum/anyOf: {bf_schema}")

    assert "text" in allowed, f"'text' not in body_format enum: {allowed}"
    assert "html" in allowed, f"'html' not in body_format enum: {allowed}"


def test_get_email_schema_body_format_default_is_text():
    """body_format schema default must be 'text'."""
    tool = TOOL_REGISTRY.get("get_email")
    assert tool is not None, "get_email tool not registered"
    schema = tool.parameters
    bf_schema = schema["properties"]["body_format"]

    # Default may appear directly in the property or via anyOf
    default_val = bf_schema.get("default")
    assert default_val == "text", (
        f"body_format default expected 'text', got {default_val!r}. Full schema: {bf_schema}"
    )
