"""Tests that all MCP tools are registered without namespace prefixes.

Gateway-level prefixing (Docker MCP Gateway tool-name-prefix feature)
replaces source-level prefixes. Server tools use generic names
(list_emails, list_events, etc.) and the gateway adds a prefix.
"""

from microsoft_mcp.tools import mcp

# FastMCP 2.8.0: tools stored in _tool_manager._tools (dict keyed by name)
TOOL_REGISTRY = mcp._tool_manager._tools


def test_no_tools_have_ms365_prefix():
    """No registered tool should start with ms365_ (gateway handles prefixing)."""
    assert len(TOOL_REGISTRY) > 0, "No tools registered"
    violations = [name for name in TOOL_REGISTRY if name.startswith("ms365_")]
    assert violations == [], f"Tools still have ms365_ prefix: {violations}"


def test_expected_email_tools_exist():
    """Core email tools must be registered."""
    expected = [
        "list_emails",
        "get_email",
        "search_emails",
        "reply_to_email",
        "move_email",
        "update_email",
    ]
    for name in expected:
        assert name in TOOL_REGISTRY, f"Expected tool '{name}' not found"


def test_expected_calendar_tools_exist():
    """Core calendar tools must be registered."""
    expected = [
        "list_events",
        "get_event",
        "create_event",
        "update_event",
        "delete_event",
    ]
    for name in expected:
        assert name in TOOL_REGISTRY, f"Expected tool '{name}' not found"


def test_expected_file_tools_exist():
    """Core OneDrive tools must be registered."""
    expected = [
        "list_files",
        "get_file",
        "create_file",
        "delete_file",
        "search_files",
    ]
    for name in expected:
        assert name in TOOL_REGISTRY, f"Expected tool '{name}' not found"


def test_expected_contact_tools_exist():
    """Core contact tools must be registered."""
    expected = [
        "list_contacts",
        "get_contact",
        "create_contact",
    ]
    for name in expected:
        assert name in TOOL_REGISTRY, f"Expected tool '{name}' not found"


def test_tool_count_unchanged():
    """Guard against accidentally dropping tools during rename."""
    assert len(TOOL_REGISTRY) == 34, (
        f"Expected 34 tools, found {len(TOOL_REGISTRY)}: {sorted(TOOL_REGISTRY.keys())}"
    )
