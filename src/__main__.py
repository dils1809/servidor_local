"""Entry point: python -m src

Wires the layers together and starts the read/dispatch loop. Swapping stdio for
another transport is a one-line change here.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .handlers.tools import CAPABILITY_CONFIG as TOOLS_CAPABILITY_CONFIG
from .handlers.tools import CAPABILITY_NAME as TOOLS_CAPABILITY_NAME
from .handlers.tools import ToolHandlers
from .mcp_server import MCPServer, serve
from .transport import StdioTransport, configure_logging

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def build_server() -> MCPServer:
    """Create the server and register its features.

    Features are registered here so the protocol layer stays free of business
    logic. Resources and prompts get added in later milestones.
    """
    server = MCPServer()

    tools = ToolHandlers()
    server.register_feature(
        TOOLS_CAPABILITY_NAME, TOOLS_CAPABILITY_CONFIG, tools.methods()
    )

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src",
        description="VIBBO MCP server (stdio transport). Reads JSON-RPC 2.0 "
        "messages from stdin, one per line, and writes responses to stdout.",
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
