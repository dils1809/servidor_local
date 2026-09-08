"""A record of every MCP message the chatbot sends or receives.

The assignment asks for a log of all interactions with the MCP servers. This
keeps them in memory so the user can page through them during the session,
and appends them to a JSONL file so the session can be reviewed afterwards.

Every entry keeps the raw message. That matters for the Wireshark part of the
project: what is written here is byte for byte what went over the wire, so a
capture can be lined up against it.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

Direction = Literal["sent", "received"]
Kind = Literal["request", "response", "notification", "error"]


@dataclass
class Entry:
    """One message, in one direction, at one moment."""

    sequence: int
    timestamp: str
    server: str
    direction: Direction
    kind: Kind
    method: str | None
    message_id: Any
    payload: dict[str, Any] = field(repr=False)

    @property
    def summary(self) -> str:
        """One line, for the live view."""
        arrow = "-->" if self.direction == "sent" else "<--"
        label = self.method or ""

        if self.kind == "error":
            error = self.payload.get("error", {})
            label = f"error {error.get('code')}: {error.get('message', '')}"
        elif self.kind == "response":
            label = _describe_result(self.payload.get("result"))

        marker = "" if self.message_id is None else f"id={self.message_id} "
        return f"{arrow} {self.server:<12} {marker}{label}"


class Logbook:
    """Collects entries. Safe to call from the reader threads."""

    def __init__(self, path: Path | None = None) -> None:
        self._entries: list[Entry] = []
        self._lock = threading.Lock()
        self._path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # A new file per run, so one session is one file.
            path.write_text("", encoding="utf-8")

    # -- recording ---------------------------------------------------------
    def record_request(self, server: str, message: dict[str, Any]) -> Entry:
        return self._add(server, "sent", "request", message)

    def record_notification(self, server: str, message: dict[str, Any]) -> Entry:
        return self._add(server, "sent", "notification", message)

    def record_response(self, server: str, message: dict[str, Any]) -> Entry:
        kind: Kind = "error" if "error" in message else "response"
        return self._add(server, "received", kind, message)

    def _add(
        self,
        server: str,
        direction: Direction,
        kind: Kind,
        message: dict[str, Any],
    ) -> Entry:
        with self._lock:
            entry = Entry(
                sequence=len(self._entries) + 1,
                timestamp=datetime.now().isoformat(timespec="milliseconds"),
                server=server,
                direction=direction,
                kind=kind,
                method=message.get("method"),
                message_id=message.get("id"),
                payload=message,
            )
            self._entries.append(entry)
            if self._path is not None:
                self._append_to_file(entry)
            return entry

    def _append_to_file(self, entry: Entry) -> None:
        record = {
            "sequence": entry.sequence,
            "timestamp": entry.timestamp,
            "server": entry.server,
            "direction": entry.direction,
            "kind": entry.kind,
            "message": entry.payload,
        }
        assert self._path is not None
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    # -- reading -----------------------------------------------------------
    def entries(self, *, server: str | None = None, limit: int | None = None):
        with self._lock:
            found = list(self._entries)
        if server is not None:
            found = [entry for entry in found if entry.server == server]
        if limit is not None:
            found = found[-limit:]
        return found

    def counts(self) -> dict[str, int]:
        """Totals by kind, for the session summary."""
        with self._lock:
            totals: dict[str, int] = {}
            for entry in self._entries:
                totals[entry.kind] = totals.get(entry.kind, 0) + 1
            totals["total"] = len(self._entries)
            return totals

    @property
    def path(self) -> Path | None:
        return self._path

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def _describe_result(result: Any) -> str:
    """Say what a result holds without dumping the whole thing."""
    if result is None:
        return "result"
    if result == {}:
        return "empty result"
    if not isinstance(result, dict):
        return "result"

    for key in ("tools", "resources", "prompts", "contents", "messages"):
        if key in result and isinstance(result[key], list):
            return f"{len(result[key])} {key}"
    if "capabilities" in result:
        return "initialized, capabilities: " + ", ".join(sorted(result["capabilities"]))
    if "content" in result:
        return "tool error" if result.get("isError") else "tool result"
    return "result"
