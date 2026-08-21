"""Message framing and I/O.

Moves lines of text without looking inside them. Parsing lives in jsonrpc.py,
meaning lives in mcp_server.py. An HTTP transport later is just another
subclass of Transport.

Framing is NDJSON: one message per line, no raw newline inside a message.

One rule: stdout carries protocol traffic only. A stray print() corrupts the
stream and the client drops the connection. Logs go to stderr.
"""

from __future__ import annotations

import logging
import sys
from abc import ABC, abstractmethod
from typing import TextIO

logger = logging.getLogger(__name__)


class Transport(ABC):
    """A channel that carries one text message at a time."""

    @abstractmethod
    def read_message(self) -> str | None:
        """Wait for the next message. Returns None when the peer disconnects."""

    @abstractmethod
    def write_message(self, text: str) -> None:
        """Send one already-serialized message."""

    def close(self) -> None:
        """Release resources. Safe to call twice."""


class StdioTransport(Transport):
    """NDJSON over stdin/stdout.

    Streams can be injected for testing. By default the process streams are
    reconfigured to UTF-8 first, which matters on Windows: the console starts
    in cp1252 and would mangle any non-ASCII product name.
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
                # "" is end of file. "\n" would be an empty line.
                logger.debug("stdin closed by the client")
                return None
            # Strip a BOM too: some clients concatenate files that carry one.
            line = line.strip().lstrip("﻿").strip()
            if not line:
                continue
            logger.debug("<-- %s", line)
            return line

    def write_message(self, text: str) -> None:
        if "\n" in text or "\r" in text:
            # This would break framing: the client would read two half messages.
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
            # Stream already torn down by the interpreter.
            pass


def _prepare_stdin(stream: TextIO) -> TextIO:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        # utf-8-sig reads plain UTF-8 the same way but also drops a leading
        # BOM. PowerShell adds one when piping a file into the server.
        # newline is left at its default so CRLF input still gives clean lines.
        reconfigure(encoding="utf-8-sig", errors="replace")
    return stream


def _prepare_stdout(stream: TextIO) -> TextIO:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        # newline="\n" turns off the Windows LF -> CRLF translation, so each
        # message ends with exactly one delimiter byte.
        reconfigure(encoding="utf-8", errors="strict", newline="\n")
    return stream


def configure_logging(level: int = logging.INFO, stream: TextIO | None = None) -> None:
    """Send diagnostics to stderr so stdout stays protocol only."""
    logging.basicConfig(
        level=level,
        stream=stream if stream is not None else sys.stderr,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
