import os
from unittest.mock import patch

from microsoft_mcp import server


def test_main_selects_stdio_transport_explicitly():
    """main() must pass transport="stdio" rather than relying on the default.

    On fastmcp 2.14.2 a bare mcp.run() resolves transport=None to a hardcoded
    "stdio", so both forms behave identically today. On 3.x the default resolves
    from fastmcp.settings.transport, which reads FASTMCP_TRANSPORT and a .env
    file — and auth.py already calls load_dotenv(). The gateway speaks stdio
    over a pipe, so a stray env value would silently start an HTTP server and
    break every tool call. Assert on the ARGUMENT, since the observable
    behaviour is the same on the pinned version (mcp-t66.2).
    """
    with patch.object(server.mcp, "run") as run_spy:
        with patch.dict(os.environ, {"MICROSOFT_MCP_CLIENT_ID": "test-client-id"}):
            server.main()

    assert run_spy.called, "mcp.run was never called — the probe did not execute"
    _, kwargs = run_spy.call_args
    assert kwargs.get("transport") == "stdio"
