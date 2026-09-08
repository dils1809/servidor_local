"""Demo: the chatbot driving Anthropic's official Filesystem and Git servers.

The assignment asks for a scenario where the chatbot creates a repository,
writes a README, adds it and commits. This runs exactly that, end to end,
through the model -- no hard-coded tool calls. The model reads the request in
plain language and decides which tools to use.

One caveat, stated because it matters: the official Git server
(mcp-server-git) exposes no git_init tool. Its twelve tools all operate on a
repository that already exists, and it is launched with --repository pointing
at one. So this script creates the directory and runs git init itself, then
hands the repository to the chatbot. Everything after that -- writing the
README, staging it, committing it, reading the log back -- is done by the
model through MCP.

Run it from the repository root:

    python tests/demo_official_servers.py

It works in a temporary directory and prints the transcript plus the MCP log,
so the output is the evidence. Nothing in this repository is touched.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from chatbot import ui  # noqa: E402
from chatbot.credentials import find_api_key  # noqa: E402
from chatbot.host import Host, ServerConfig  # noqa: E402
from chatbot.logbook import Logbook  # noqa: E402
from chatbot.session import Session  # noqa: E402

import anthropic  # noqa: E402

# What the user asks for, in one message. The model works out the steps.
SCENARIO = """\
In the repository you have git access to, do the following, in order:

1. Create a file called README.md containing a title "Demo Repository" and one
   sentence saying it was created by an MCP chatbot.
2. Stage that file.
3. Commit it with the message "docs: add README created over MCP".
4. Show me the commit log to prove it worked.

Use the filesystem tools to write the file and the git tools for the rest.
Report what you did at each step."""


def prepare_repository() -> Path:
    """Make an empty git repository in a temporary directory.

    git init is done here because the official Git MCP server has no tool for
    it. This is the one step of the scenario that MCP cannot perform.
    """
    workspace = Path(tempfile.mkdtemp(prefix="mcp-demo-"))
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    # A commit needs an identity, and the demo must not depend on the global
    # git config being set.
    for key, value in (("user.name", "MCP Demo"), ("user.email", "demo@example.com")):
        subprocess.run(
            ["git", "config", key, value], cwd=workspace, check=True, capture_output=True
        )
    return workspace


def build_host(workspace: Path, logbook: Logbook) -> Host:
    """Only the two official servers. The VIBBO server is not part of this."""
    configs = [
        ServerConfig(
            name="filesystem",
            description="Official Anthropic Filesystem server.",
            command=[
                "npx",
                "-y",
                "@modelcontextprotocol/server-filesystem",
                "{workspace}",
            ],
        ),
        ServerConfig(
            name="git",
            description="Official Anthropic Git server.",
            command=[
                sys.executable,
                "-m",
                "mcp_server_git",
                "--repository",
                "{workspace}",
            ],
        ),
    ]
    return Host(configs, workspace, logbook)


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    ui.enable()

    api_key, source = find_api_key(REPO_ROOT)
    if not api_key:
        ui.error("No Anthropic API key found. See the README.")
        return 1

    workspace = prepare_repository()
    ui.note(f"  temporary repository: {workspace}")

    logbook = Logbook()
    host = build_host(workspace, logbook)
    host.start()

    if len(host.clients) < 2:
        ui.error(f"Only {len(host.clients)} of 2 servers started: {host.failures}")
        shutil.rmtree(workspace, ignore_errors=True)
        return 1

    ui.banner(host.clients, host.failures, len(host.tools_for_llm()))

    session = Session(host, client=anthropic.Anthropic(api_key=api_key))
    ui.heading("the request")
    print(SCENARIO)
    print()

    ui.heading("what the model did")
    result = session.send(
        SCENARIO,
        on_tool_call=lambda name, arguments: ui.tool_call(name, arguments),
        on_tool_result=lambda _n, output, failed: ui.tool_result(output, failed),
    )
    ui.assistant(result.text)

    host.stop()

    # Verify independently of anything the model said about itself.
    ui.heading("independent verification")
    _verify(workspace)

    ui.heading("MCP log")
    for entry in logbook.entries():
        print(f"  {entry.sequence:>3}. {entry.summary}")
    print()
    counts = logbook.counts()
    ui.note("  ".join(f"{key}: {value}" for key, value in sorted(counts.items())))
    print()
    ui.note(f"cost: ${result.cost:.4f}")

    shutil.rmtree(workspace, ignore_errors=True)
    return 0


def _verify(workspace: Path) -> None:
    """Check the repository on disk, not the model's report of it."""
    readme = workspace / "README.md"
    print(
        "  README.md exists          "
        + (ui.paint("yes", ui.GREEN) if readme.is_file() else ui.paint("no", ui.RED))
    )

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=workspace, capture_output=True, text=True
    )
    lines = [line for line in log.stdout.splitlines() if line.strip()]
    print(
        "  commits in the repository "
        + (ui.paint(str(len(lines)), ui.GREEN) if lines else ui.paint("0", ui.RED))
    )
    for line in lines:
        print("    " + ui.paint(line, ui.GREY))

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=workspace, capture_output=True, text=True
    )
    files = [line for line in tracked.stdout.splitlines() if line.strip()]
    print("  tracked files             " + ui.paint(", ".join(files) or "none", ui.WHITE))
    print()


if __name__ == "__main__":
    sys.exit(main())
