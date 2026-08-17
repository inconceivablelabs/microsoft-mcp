import os
import sys
from .tools import mcp


def main() -> None:
    if not os.getenv("MICROSOFT_MCP_CLIENT_ID"):
        print(
            "Error: MICROSOFT_MCP_CLIENT_ID environment variable is required",
            file=sys.stderr,
        )
        sys.exit(1)

    # Explicit transport: fastmcp 3.x resolves a bare run() from
    # fastmcp.settings.transport, which reads FASTMCP_TRANSPORT and .env
    # (auth.py already calls load_dotenv()). The gateway speaks stdio over a
    # pipe, so a stray env value must not be able to start an HTTP server.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
