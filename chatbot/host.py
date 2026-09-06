"""The host: one process, several MCP clients.

The host owns a client per server, starts them all, and presents their tools
to the model as one flat list. This is the role Claude Desktop plays for a
normal MCP setup; here the chatbot plays it.

Tool names are namespaced as ``server__tool`` because two servers can easily
both offer ``search``, and the model needs to be able to name one of them
unambiguously. Splitting the name back apart is how a call gets routed.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src import jsonrpc

from .logbook import Logbook
from .mcp_client import MCPClient, MCPClientError

logger = logging.getLogger(__name__)

NAME_SEPARATOR = "__"
# Synthetic tool name: not an MCP method, invented by the host.
RESOURCE_TOOL = "read_resource"
DEFAULT_CONFIG = Path(__file__).resolve().parent / "servers.json"

# Anthropic tool names allow letters, digits, underscore and dash, up to 128.
MAX_TOOL_NAME = 128


@dataclass
class ServerConfig:
    name: str
    description: str
    command: list[str]
    enabled: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ServerConfig:
        missing = {"name", "command"} - set(raw)
        if missing:
            raise ValueError(f"server entry is missing {sorted(missing)}")
        return cls(
            name=raw["name"],
            description=raw.get("description", ""),
            command=list(raw["command"]),
            enabled=bool(raw.get("enabled", True)),
        )


def load_config(path: Path = DEFAULT_CONFIG) -> list[ServerConfig]:
    """Read servers.json."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MCPClientError(f"Cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MCPClientError(f"{path} is not valid JSON: {exc}") from exc

    return [ServerConfig.from_dict(entry) for entry in raw.get("servers", [])]


def resolve_command(command: list[str], workspace: Path) -> list[str]:
    """Turn a configured command into one this machine can actually run.

    Two things happen here. ``{workspace}`` is substituted, and the executable
    is looked up on PATH. The lookup matters on Windows, where ``npx`` is
    ``npx.cmd`` and Popen will not find it without the extension.
    """
    resolved = [part.replace("{workspace}", str(workspace)) for part in command]

    executable = resolved[0]
    found = shutil.which(executable)
    if found is None and os.name == "nt":
        for extension in (".cmd", ".exe", ".bat"):
            found = shutil.which(executable + extension)
            if found:
                break
    if found is not None:
        resolved[0] = found

    return resolved


class Host:
    """Starts every configured server and routes tool calls to the right one."""

    def __init__(
        self,
        configs: list[ServerConfig],
        workspace: Path,
        logbook: Logbook | None = None,
    ) -> None:
        self._configs = [config for config in configs if config.enabled]
        self._workspace = workspace
        self._logbook = logbook

        self.clients: dict[str, MCPClient] = {}
        self.failures: dict[str, str] = {}
        # namespaced name -> (server name, bare tool name)
        self._routes: dict[str, tuple[str, str]] = {}
        self._tools: list[dict[str, Any]] = []

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        """Connect to every server. One failure does not stop the others."""
        for config in self._configs:
            command = resolve_command(config.command, self._workspace)
            client = MCPClient(
                config.name,
                command,
                cwd=str(self._workspace),
                logbook=self._logbook,
            )
            try:
                client.start()
            except (MCPClientError, jsonrpc.JsonRpcError, jsonrpc.RemoteError) as exc:
                # A missing server is worth reporting but not worth aborting
                # the session: the others may be all the user needs.
                logger.warning("could not start %s: %s", config.name, exc)
                self.failures[config.name] = str(exc)
                continue
            self.clients[config.name] = client

        self._collect_tools()

    def stop(self) -> None:
        for client in self.clients.values():
            try:
                client.stop()
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                logger.debug("error stopping %s: %s", client.name, exc)
        self.clients.clear()

    # -- tools -------------------------------------------------------------
    def _collect_tools(self) -> None:
        """Ask every server what it offers and build the routing table."""
        self._tools = []
        self._routes = {}

        for name, client in self.clients.items():
            try:
                tools = client.list_tools()
            except (MCPClientError, jsonrpc.RemoteError) as exc:
                logger.warning("%s could not list tools: %s", name, exc)
                continue

            for tool in tools:
                namespaced = f"{name}{NAME_SEPARATOR}{tool['name']}"[:MAX_TOOL_NAME]
                self._routes[namespaced] = (name, tool["name"])
                self._tools.append(
                    {
                        "name": namespaced,
                        "description": tool.get("description", ""),
                        "input_schema": tool.get(
                            "inputSchema", {"type": "object", "properties": {}}
                        ),
                    }
                )

        self._add_resource_tools()
        logger.info(
            "%d tools from %d servers", len(self._tools), len(self.clients)
        )

    def _add_resource_tools(self) -> None:
        """Give the model a way to read resources.

        In MCP, resources are user-driven: the host decides what to attach,
        and the model has no method for fetching one. That is the wrong shape
        for a chatbot, where the model is the one that realises it needs the
        returns policy halfway through a sentence.

        So each server that has resources gets a synthetic read tool, with the
        registered URIs as an enum. The model can only ask for a URI the
        server already declared, so this adds reach without widening what is
        reachable.
        """
        for name, client in self.clients.items():
            try:
                resources = client.list_resources()
            except (MCPClientError, jsonrpc.RemoteError) as exc:
                logger.debug("%s could not list resources: %s", name, exc)
                continue
            if not resources:
                continue

            catalogue = "\n".join(
                f"- {resource['uri']}: {resource.get('description', '')}"
                for resource in resources
            )
            tool_name = f"{name}{NAME_SEPARATOR}{RESOURCE_TOOL}"
            self._routes[tool_name] = (name, RESOURCE_TOOL)
            self._tools.append(
                {
                    "name": tool_name,
                    "description": (
                        f"Read one of the reference documents published by the "
                        f"{name} server. Read the relevant one before stating a "
                        f"policy or a rule. Available documents:\n{catalogue}"
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "uri": {
                                "type": "string",
                                "enum": [r["uri"] for r in resources],
                                "description": "Which document to read.",
                            }
                        },
                        "required": ["uri"],
                    },
                }
            )

    def tools_for_llm(self) -> list[dict[str, Any]]:
        """The tool list in the shape the Anthropic API expects."""
        return self._tools

    def call_tool(self, namespaced: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run a tool. Returns the text for the model and whether it failed.

        Every failure is turned into text rather than an exception, because
        the model has to read it in order to recover. An exception here would
        kill the turn instead.
        """
        route = self._routes.get(namespaced)
        if route is None:
            return f"No such tool: {namespaced}", True

        server_name, tool_name = route
        client = self.clients.get(server_name)
        if client is None:
            return f"Server {server_name} is not connected", True

        if tool_name == RESOURCE_TOOL:
            return self._read_resource_as_tool(client, arguments)

        try:
            result = client.call_tool(tool_name, arguments)
        except jsonrpc.RemoteError as exc:
            # A protocol error: the call itself was malformed.
            return f"{server_name} rejected the call: {exc}", True
        except MCPClientError as exc:
            return f"{server_name} is unreachable: {exc}", True

        return render_tool_result(result), bool(result.get("isError"))

    def _read_resource_as_tool(
        self, client: MCPClient, arguments: dict[str, Any]
    ) -> tuple[str, bool]:
        """Back the synthetic read tool with a real resources/read call."""
        uri = arguments.get("uri")
        if not isinstance(uri, str) or not uri:
            return "The 'uri' argument is required.", True
        try:
            contents = client.read_resource(uri)
        except jsonrpc.RemoteError as exc:
            return f"{client.name} rejected the read: {exc}", True
        except MCPClientError as exc:
            return f"{client.name} is unreachable: {exc}", True

        text = "\n\n".join(item.get("text", "") for item in contents)
        return text or "(the document is empty)", False

    # -- resources and prompts --------------------------------------------
    def list_resources(self) -> list[tuple[str, dict[str, Any]]]:
        found: list[tuple[str, dict[str, Any]]] = []
        for name, client in self.clients.items():
            try:
                found.extend((name, resource) for resource in client.list_resources())
            except (MCPClientError, jsonrpc.RemoteError) as exc:
                logger.debug("%s could not list resources: %s", name, exc)
        return found

    def list_prompts(self) -> list[tuple[str, dict[str, Any]]]:
        found: list[tuple[str, dict[str, Any]]] = []
        for name, client in self.clients.items():
            try:
                found.extend((name, prompt) for prompt in client.list_prompts())
            except (MCPClientError, jsonrpc.RemoteError) as exc:
                logger.debug("%s could not list prompts: %s", name, exc)
        return found

    def read_resource(self, server: str, uri: str) -> str:
        client = self.clients.get(server)
        if client is None:
            raise MCPClientError(f"Server {server} is not connected")
        contents = client.read_resource(uri)
        return "\n\n".join(item.get("text", "") for item in contents)

    def get_prompt(
        self, server: str, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        client = self.clients.get(server)
        if client is None:
            raise MCPClientError(f"Server {server} is not connected")
        return client.get_prompt(name, arguments)

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> Host:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def render_tool_result(result: dict[str, Any]) -> str:
    """Flatten an MCP tool result into text the model can read.

    MCP returns a list of content blocks plus, on newer servers, a
    structuredContent object. The structured form is preferred when present
    because it is unambiguous; the text blocks are the fallback.
    """
    if "structuredContent" in result:
        return json.dumps(result["structuredContent"], ensure_ascii=False, indent=2)

    pieces: list[str] = []
    for block in result.get("content", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            pieces.append(block.get("text", ""))
        elif block.get("type") == "resource":
            resource = block.get("resource", {})
            pieces.append(resource.get("text", f"[resource {resource.get('uri', '')}]"))
        else:
            pieces.append(f"[{block.get('type', 'unknown')} content]")

    return "\n".join(pieces) if pieces else "(the tool returned nothing)"
