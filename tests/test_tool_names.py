"""Tests that all MCP tools are registered with the ms365_ prefix.

This prevents tool name collisions when the server runs behind an
MCP gateway alongside other servers.
"""

from microsoft_mcp.tools import mcp

# FastMCP 2.8.0: tools stored in _tool_manager._tools (dict keyed by name)
TOOL_REGISTRY = mcp._tool_manager._tools


def test_all_tools_have_ms365_prefix():
    """Every registered tool must start with ms365_."""
    assert len(TOOL_REGISTRY) > 0, "No tools registered"
    violations = [name for name in TOOL_REGISTRY if not name.startswith("ms365_")]
    assert violations == [], f"Tools missing ms365_ prefix: {violations}"


def test_expected_email_tools_exist():
    """Core email tools must be registered with the ms365_ prefix."""
    expected = [
        "ms365_list_emails",
        "ms365_get_email",
        "ms365_search_emails",
        "ms365_reply_to_email",
        "ms365_move_email",
        "ms365_update_email",
    ]
    for name in expected:
        assert name in TOOL_REGISTRY, f"Expected tool '{name}' not found"


def test_expected_calendar_tools_exist():
    """Core calendar tools must be registered with the ms365_ prefix."""
    expected = [
        "ms365_list_events",
        "ms365_get_event",
        "ms365_create_event",
        "ms365_update_event",
        "ms365_delete_event",
    ]
    for name in expected:
        assert name in TOOL_REGISTRY, f"Expected tool '{name}' not found"


def test_expected_file_tools_exist():
    """Core OneDrive tools must be registered with the ms365_ prefix."""
    expected = [
        "ms365_list_files",
        "ms365_get_file",
        "ms365_create_file",
        "ms365_delete_file",
        "ms365_search_files",
    ]
    for name in expected:
        assert name in TOOL_REGISTRY, f"Expected tool '{name}' not found"


def test_expected_contact_tools_exist():
    """Core contact tools must be registered with the ms365_ prefix."""
    expected = [
        "ms365_list_contacts",
        "ms365_get_contact",
        "ms365_create_contact",
    ]
    for name in expected:
        assert name in TOOL_REGISTRY, f"Expected tool '{name}' not found"


def test_tool_count_unchanged():
    """Guard against accidentally dropping tools during rename."""
    assert len(TOOL_REGISTRY) == 35, (
        f"Expected 35 tools, found {len(TOOL_REGISTRY)}: {sorted(TOOL_REGISTRY.keys())}"
    )
