"""Entry point: python -m chatbot

The read-eval-print loop. Lines starting with "/" are commands handled here;
everything else goes to the model.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import anthropic

from . import ui
from .credentials import find_api_key
from .host import Host, load_config
from .logbook import Logbook
from .mcp_client import MCPClientError
from .session import MODEL, Session

WORKSPACE = Path(__file__).resolve().parents[1]
LOG_DIR = WORKSPACE / "logs"

COMMANDS = {
    "/help": "show this list",
    "/servers": "connected MCP servers and their capabilities",
    "/tools": "every tool the model can call",
    "/resources": "documents the servers expose",
    "/prompts": "templates the servers expose",
    "/use": "/use <server> <prompt> — load a prompt into the conversation",
    "/read": "/read <server> <uri> — read a resource into the conversation",
    "/log": "/log [n] — the last n MCP messages (default 20)",
    "/save": "where the full session log is being written",
    "/clear": "forget the conversation, keep the servers",
    "/cost": "tokens and dollars used this session",
    "/quit": "exit",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m chatbot",
        description="Terminal chatbot that hosts MCP servers and talks to Claude.",
    )
    parser.add_argument("--model", default=MODEL, help="model id (default: %(default)s)")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="verbosity on stderr (default: %(default)s)",
    )
    parser.add_argument("--no-color", action="store_true", help="disable colour")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.no_color:
        # Still fix the encoding: only the colour is being turned off.
        ui.use_utf8()
    else:
        ui.enable()

    api_key, source = find_api_key(WORKSPACE)
    if not api_key:
        ui.error("No Anthropic API key found.")
        print()
        ui.note("Get one at console.anthropic.com, then set it:")
        ui.note('  [Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")')
        print()
        ui.note("Then open a NEW terminal. A program only sees variables that")
        ui.note("existed when it started.")
        print()
        return 1

    client = anthropic.Anthropic(api_key=api_key)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logbook = Logbook(LOG_DIR / f"session-{stamp}.jsonl")

    try:
        configs = load_config()
    except MCPClientError as exc:
        ui.error(str(exc))
        return 1

    host = Host(configs, WORKSPACE, logbook)
    ui.note("  starting MCP servers…")
    host.start()

    if not host.clients:
        ui.error("No MCP server started. Nothing to do.")
        return 1

    session = Session(host, client=client, model=args.model)
    colours = {
        name: ui.SERVER_COLOURS[index % len(ui.SERVER_COLOURS)]
        for index, name in enumerate(host.clients)
    }

    ui.banner(host.clients, host.failures, len(host.tools_for_llm()))
    if source != "environment":
        ui.note(f"  api key read from {source}")
        print()

    try:
        _loop(host, session, logbook, colours)
    finally:
        host.stop()
        print()
        ui.footer(len(logbook), session.total_cost)
        if logbook.path:
            ui.note(f"log: {logbook.path}")
        print()

    return 0


def _loop(host: Host, session: Session, logbook: Logbook, colours: dict) -> None:
    while True:
        try:
            line = ui.prompt().strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not line:
            continue

        if line.startswith("/"):
            if _command(line, host, session, logbook) is False:
                return
            continue

        try:
            result = session.send(
                line,
                on_tool_call=lambda name, arguments: ui.tool_call(
                    name, arguments, colours.get(name.split("__")[0], ui.AMBER)
                ),
                on_tool_result=lambda _name, output, failed: ui.tool_result(
                    output, failed
                ),
            )
        except anthropic.AuthenticationError:
            ui.error("The API key was rejected. Check ANTHROPIC_API_KEY.")
            continue
        except anthropic.RateLimitError:
            ui.error("Rate limited. Wait a moment and try again.")
            continue
        except anthropic.APIStatusError as exc:
            ui.error(f"API error {exc.status_code}: {exc.message}")
            continue
        except anthropic.APIConnectionError:
            ui.error("Could not reach the API. Check the network.")
            continue
        except TypeError as exc:
            ui.error(f"The API client is not configured: {exc}")
            continue

        ui.assistant(result.text or "(no answer)")


def _command(line: str, host: Host, session: Session, logbook: Logbook) -> bool:
    """Handle a slash command. Returns False to quit."""
    parts = line.split()
    name, arguments = parts[0].lower(), parts[1:]

    if name in ("/quit", "/exit"):
        return False

    if name == "/help":
        ui.heading("commands")
        for command, description in COMMANDS.items():
            print("  " + ui.paint(f"{command:<12}", ui.TEAL) + ui.paint(description, ui.GREY))
        print()

    elif name == "/servers":
        ui.heading("servers")
        for server_name, connected in host.clients.items():
            info = connected.server_info
            print(
                "  "
                + ui.paint(f"{server_name:<12}", ui.WHITE)
                + ui.paint(
                    f"{info.get('name', '?')} {info.get('version', '')}".ljust(28),
                    ui.GREY,
                )
                + ui.paint(f"MCP {connected.protocol_version}", ui.GREY)
                + ui.paint("  " + ", ".join(sorted(connected.capabilities)), ui.GREY)
            )
        for failed_name, reason in host.failures.items():
            print("  " + ui.paint(f"{failed_name:<12}", ui.RED) + ui.paint(reason, ui.GREY))
        print()

    elif name == "/tools":
        tools = host.tools_for_llm()
        ui.heading(f"tools ({len(tools)})")
        for tool in tools:
            server, _, bare = tool["name"].partition("__")
            print(
                "  "
                + ui.paint(f"{server:<12}", ui.VIOLET)
                + ui.paint(f"{bare:<28}", ui.WHITE)
                + ui.paint(ui._shorten(tool["description"], ui.width() - 46), ui.GREY)
            )
        print()

    elif name == "/resources":
        found = host.list_resources()
        ui.heading(f"resources ({len(found)})")
        for server, resource in found:
            print(
                "  "
                + ui.paint(f"{server:<12}", ui.VIOLET)
                + ui.paint(f"{resource['uri']:<34}", ui.WHITE)
                + ui.paint(resource.get("title", ""), ui.GREY)
            )
        print()

    elif name == "/prompts":
        found = host.list_prompts()
        ui.heading(f"prompts ({len(found)})")
        for server, prompt_info in found:
            required = [
                argument["name"]
                for argument in prompt_info.get("arguments", [])
                if argument.get("required")
            ]
            print(
                "  "
                + ui.paint(f"{server:<12}", ui.VIOLET)
                + ui.paint(f"{prompt_info['name']:<24}", ui.WHITE)
                + ui.paint("requires: " + ", ".join(required), ui.GREY)
            )
        print()

    elif name == "/read":
        if len(arguments) < 2:
            ui.error("usage: /read <server> <uri>")
            return True
        try:
            text = host.read_resource(arguments[0], arguments[1])
        except Exception as exc:  # noqa: BLE001
            ui.error(str(exc))
            return True
        session.add_user_text(
            f"Here is the content of {arguments[1]}:\n\n{text}"
        )
        ui.note(f"loaded {len(text)} characters into the conversation")

    elif name == "/use":
        if len(arguments) < 2:
            ui.error("usage: /use <server> <prompt>")
            return True
        _load_prompt(host, session, arguments[0], arguments[1])

    elif name == "/log":
        count = int(arguments[0]) if arguments and arguments[0].isdigit() else 20
        entries = logbook.entries(limit=count)
        ui.heading(f"MCP log (last {len(entries)} of {len(logbook)})")
        for entry in entries:
            colour = ui.RED if entry.kind == "error" else ui.GREY
            print("  " + ui.paint(f"{entry.sequence:>3}. ", ui.GREY) + ui.paint(entry.summary, colour))
        counts = logbook.counts()
        print()
        ui.note(
            "  ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
        )
        print()

    elif name == "/save":
        ui.note(f"writing to {logbook.path}")

    elif name == "/clear":
        session.clear()
        ui.note("conversation cleared, servers still connected")

    elif name == "/cost":
        ui.heading("usage")
        print(
            "  "
            + ui.paint(f"input  {session.total_input_tokens:>8,} tokens", ui.WHITE)
        )
        print(
            "  "
            + ui.paint(f"output {session.total_output_tokens:>8,} tokens", ui.WHITE)
        )
        print("  " + ui.paint(f"cost   ${session.total_cost:>11.4f}", ui.GREEN))
        print()

    else:
        ui.error(f"unknown command: {name}   (/help)")

    return True


def _load_prompt(host: Host, session: Session, server: str, prompt_name: str) -> None:
    """Ask the user for each argument the prompt declares, then load it."""
    prompts = {
        info["name"]: (owner, info)
        for owner, info in host.list_prompts()
        if owner == server
    }
    entry = prompts.get(prompt_name)
    if entry is None:
        ui.error(f"{server} has no prompt named {prompt_name}")
        return

    _, info = entry
    values: dict[str, str] = {}
    ui.note(f"filling {prompt_name} — press Enter to skip an optional field")
    for argument in info.get("arguments", []):
        mark = "*" if argument.get("required") else " "
        try:
            answer = input(
                ui.paint(f"    {mark} {argument['name']}: ", ui.AMBER)
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if answer:
            values[argument["name"]] = answer

    try:
        result = host.get_prompt(server, prompt_name, values)
    except Exception as exc:  # noqa: BLE001
        ui.error(str(exc))
        return

    session.load_prompt_messages(result.get("messages", []))
    ui.note(
        f"loaded {len(result.get('messages', []))} messages — send a message to continue"
    )


if __name__ == "__main__":
    sys.exit(main())
