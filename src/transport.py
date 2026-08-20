"""Message framing and I/O for the MCP server.

The transport is the only layer that knows *how* bytes travel. It moves opaque
lines of text and never inspects their contents: parsing belongs to
``jsonrpc.py`` and meaning belongs to ``mcp_server.py``. That separation is
what lets the same server be exposed over HTTP later by writing a second
``Transport`` implementation and changing nothing else.

Framing is newline-delimited JSON (NDJSON), as required by the MCP stdio
transport: one message per line, and no raw newline may appear inside a
message. ``jsonrpc.encode`` guarantees the second half of that contract.

One rule governs this whole module: **stdout carries protocol traffic and
nothing else.** A stray ``print`` corrupts the stream and the client drops the
connection. Diagnostics go to stderr, which is why ``configure_logging`` exists.
"""

from __future__ import annotations

import logging
import sys
from abc import ABC, abstractmethod
from typing import TextIO

logger = logging.getLogger(__name__)


class Transport(ABC):
    """A bidirectional channel that carries one text message at a time."""

    @abstractmethod
    def read_message(self) -> str | None:
        """Block until the next message arrives.

        Returns the raw text of the message, or ``None`` when the peer closed
        the channel and no more messages will arrive.
        """

    @abstractmethod
    def write_message(self, text: str) -> None:
        """Send one already-serialized message."""

    def close(self) -> None:
        """Release any resources. Safe to call more than once."""


class StdioTransport(Transport):
    """NDJSON over stdin/stdout, the transport MCP clients launch locally.

    Streams can be injected for testing; by default the process streams are
    reconfigured to UTF-8 first. That reconfiguration is not optional on
    Windows, where the console defaults to a legacy code page (cp1252) and
    would mangle any non-ASCII product name on the way out.
    """

    def __init__(
        self, stdin: TextIO | None = None, stdout: TextIO | None = None
    ) -> None:
        self._stdin = stdin if stdin is not None else _prepare_stdin(sys.stdin)
        self._stdout = stdout if stdout is not None else _prepare_stdout(sys.stdout)
        self._closed = False

    def read_message(self) -> str | None:
        while True:
            line = self._stdin.readline()
            if line == "":
                # Empty string (as opposed to "\n") means end of file.
                logger.debug("stdin closed by the client")
                return None
            line = line.strip()
            if not line:
                # Blank separator lines are tolerated and skipped.
                continue
            logger.debug("<-- %s", line)
            return line

    def write_message(self, text: str) -> None:
        if "\n" in text or "\r" in text:
            # Would break framing: the client would read two truncated halves.
            raise ValueError("A framed message must not contain newline characters")
        if self._closed:
            raise RuntimeError("Transport is closed")
        logger.debug("--> %s", text)
        self._stdout.write(text + "\n")
        self._stdout.flush()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._stdout.flush()
        except ValueError:
            # Stream already torn down by the interpreter; nothing to flush.
            pass


def _prepare_stdin(stream: TextIO) -> TextIO:
    """Force UTF-8 on the input stream, keeping universal-newline decoding."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        # newline is left at its default so that a client sending CRLF still
        # yields clean lines.
        reconfigure(encoding="utf-8", errors="replace")
    return stream


def _prepare_stdout(stream: TextIO) -> TextIO:
    """Force UTF-8 and LF-only line endings on the output stream."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        # newline="\n" disables the Windows LF -> CRLF translation, so every
        # framed message ends with exactly one byte of delimiter.
        reconfigure(encoding="utf-8", errors="strict", newline="\n")
    return stream


def configure_logging(level: int = logging.INFO, stream: TextIO | None = None) -> None:
    """Send all diagnostics to stderr so stdout stays protocol-only."""
    logging.basicConfig(
        level=level,
        stream=stream if stream is not None else sys.stderr,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
