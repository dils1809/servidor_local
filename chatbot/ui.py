"""Terminal presentation.

Colour here is not decoration, it is a code the user learns in one screen:

* teal   -- the assistant speaking
* amber  -- a tool being invoked, the one moment the chatbot leaves the model
* grey   -- protocol detail: ids, servers, token counts
* red    -- something failed
* green  -- something succeeded

Everything the user types is plain white, so the eye can find the last thing
it wrote in a long scrollback. Each MCP server keeps its own accent colour so
"which server answered that" is readable without parsing a line.

ANSI only, no dependencies. On Windows the terminal has to be told to
interpret escapes; enable() does that and falls back to plain text if it
cannot.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Any

# -- palette ---------------------------------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

TEAL = "\033[38;5;44m"
AMBER = "\033[38;5;214m"
GREY = "\033[38;5;245m"
RED = "\033[38;5;203m"
GREEN = "\033[38;5;114m"
WHITE = "\033[38;5;255m"
VIOLET = "\033[38;5;141m"
BLUE = "\033[38;5;75m"

# One accent per server, assigned in order of connection.
SERVER_COLOURS = (VIOLET, BLUE, GREEN, AMBER)

_enabled = True


def use_utf8() -> None:
    """Make stdout and stderr accept anything the model writes.

    The Windows console defaults to cp1252, so a bullet or a dash raises
    UnicodeEncodeError mid-print. This lives here rather than in one entry
    point so every script that prints gets it.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def enable() -> bool:
    """Turn on UTF-8 and ANSI handling.

    Returns False if the terminal cannot do colour. Encoding is fixed either
    way: colour is optional, not crashing is not.
    """
    global _enabled
    use_utf8()
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        _enabled = False
        return False
    if os.name == "nt":
        # Windows 10 needs virtual terminal processing switched on explicitly.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:  # noqa: BLE001 - colour is optional, never fatal
            _enabled = False
            return False
    return True


def paint(text: str, *codes: str) -> str:
    if not _enabled or not codes:
        return text
    return "".join(codes) + text + RESET


def width() -> int:
    return min(shutil.get_terminal_size((80, 24)).columns, 100)


# -- blocks ----------------------------------------------------------------
def rule(char: str = "─") -> str:
    return paint(char * width(), GREY)


def banner(servers: dict[str, Any], failures: dict[str, str], tools: int) -> None:
    """The opening screen: what is connected, and what to type."""
    print()
    print(paint("  VIBBO", BOLD, TEAL) + paint("  MCP chatbot", BOLD, WHITE))
    print(paint("  Model Context Protocol host · CC3067 Redes", GREY))
    print()

    for index, (name, client) in enumerate(servers.items()):
        colour = SERVER_COLOURS[index % len(SERVER_COLOURS)]
        info = client.server_info
        caps = ", ".join(sorted(client.capabilities)) or "none"
        print(
            "  "
            + paint("●", GREEN)
            + " "
            + paint(f"{name:<12}", colour)
            + paint(
                f"{info.get('name', '?')} {info.get('version', '')}".ljust(30), WHITE
            )
            + paint(f"MCP {client.protocol_version}  ·  {caps}", GREY)
        )

    for name, reason in failures.items():
        print(
            "  "
            + paint("●", RED)
            + " "
            + paint(f"{name:<12}", RED)
            + paint(_first_line(reason), GREY)
        )

    print()
    print(
        paint(f"  {tools} tools available.", GREY)
        + paint("  /help", TEAL)
        + paint(" for commands, ", GREY)
        + paint("/quit", TEAL)
        + paint(" to exit.", GREY)
    )
    print()


def prompt() -> str:
    """Read one line from the user."""
    return input(paint("  you ", BOLD, WHITE) + paint("› ", GREY))


def assistant(text: str) -> None:
    print()
    label = paint("  claude ", BOLD, TEAL)
    for index, line in enumerate(text.splitlines() or [""]):
        prefix = label if index == 0 else " " * 10
        print(prefix + paint(line, WHITE))
    print()


def tool_call(name: str, arguments: dict[str, Any], colour: str = AMBER) -> None:
    """Announce a tool call before it runs."""
    server, _, bare = name.partition("__")
    shown = _shorten(_format_arguments(arguments), width() - len(name) - 20)
    print(
        paint("  ⚙ ", AMBER)
        + paint(server, colour)
        + paint(" · ", GREY)
        + paint(bare or name, AMBER)
        + paint(f"({shown})", GREY)
    )


def tool_result(output: str, failed: bool) -> None:
    """Show what came back, trimmed. The model sees all of it."""
    marker = paint("    ✗ ", RED) if failed else paint("    ✓ ", GREEN)
    first = _first_line(output)
    print(marker + paint(_shorten(first, width() - 8), GREY))


def error(message: str) -> None:
    print(paint("  ! ", RED) + paint(message, RED))


def note(message: str) -> None:
    print(paint("  " + message, GREY))


def heading(text: str) -> None:
    print()
    print(paint("  " + text, BOLD, WHITE))
    print(rule())


def footer(entries: int, cost: float) -> None:
    print(
        paint(f"  {entries} MCP messages logged", GREY)
        + paint("  ·  ", GREY)
        + paint(f"${cost:.4f} spent", GREY)
    )


# -- helpers ---------------------------------------------------------------
def _format_arguments(arguments: dict[str, Any]) -> str:
    parts = []
    for key, value in arguments.items():
        rendered = value if isinstance(value, str) else repr(value)
        parts.append(f"{key}={_shorten(str(rendered), 40)}")
    return ", ".join(parts)


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if limit < 8 or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return text.strip()
