"""Entry point: python -m src

Wires the layers together and starts the read/dispatch loop. Swapping stdio for
another transport is a one-line change here.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .handlers.prompts import CAPABILITY_CONFIG as PROMPTS_CAPABILITY_CONFIG
from .handlers.prompts import CAPABILITY_NAME as PROMPTS_CAPABILITY_NAME
from .handlers.prompts import PromptHandlers
from .handlers.resources import CAPABILITY_CONFIG as RESOURCES_CAPABILITY_CONFIG
from .handlers.resources import CAPABILITY_NAME as RESOURCES_CAPABILITY_NAME
from .handlers.resources import ResourceHandlers
from .handlers.tools import CAPABILITY_CONFIG as TOOLS_CAPABILITY_CONFIG
from .handlers.tools import CAPABILITY_NAME as TOOLS_CAPABILITY_NAME
from .handlers.tools import ToolHandlers
from .http_transport import serve_http
from .mcp_server import MCPServer, serve
from .transport import StdioTransport, configure_logging

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def build_server() -> MCPServer:
    """Create the server and register its features.

    Features are registered here so the protocol layer stays free of business
    logic. Adding a feature never touches the protocol code.
    """
    server = MCPServer()

    tools = ToolHandlers()
    server.register_feature(
        TOOLS_CAPABILITY_NAME, TOOLS_CAPABILITY_CONFIG, tools.methods()
    )

    resources = ResourceHandlers()
    server.register_feature(
        RESOURCES_CAPABILITY_NAME, RESOURCES_CAPABILITY_CONFIG, resources.methods()
    )

    prompts = PromptHandlers()
    server.register_feature(
        PROMPTS_CAPABILITY_NAME, PROMPTS_CAPABILITY_CONFIG, prompts.methods()
    )

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src",
        description="VIBBO MCP server (stdio transport). Reads JSON-RPC 2.0 "
        "messages from stdin, one per line, and writes responses to stdout.",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve over HTTP instead of stdio. Same server, same handlers; "
        "only the framing changes.",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Interface to bind when --http is used (default: %(default)s).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for --http. Defaults to the PORT variable, then 8080.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=LOG_LEVELS,
        help="Log verbosity. Logs always go to stderr so stdout stays "
        "protocol only (default: %(default)s).",
    )
    args = parser.parse_args(argv)

    configure_logging(level=getattr(logging, args.log_level))

    if args.http:
        # A fresh server per session, so two clients never share lifecycle
        # state. Over stdio one process is one client and this is implicit.
        serve_http(build_server, host=args.host, port=args.port)
        return 0

    transport = StdioTransport()
    server = build_server()
    try:
        serve(transport, server)
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("interrupted, shutting down")
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
