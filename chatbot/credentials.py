"""Finding the API key.

Three places are checked, in order:

1. The ANTHROPIC_API_KEY environment variable.
2. A .env file in the repository root.
3. On Windows, the user-scope variable read straight from the registry.

The third one exists because of a real trap: setting a user variable with
SetEnvironmentVariable persists it, but a terminal that was already open
never sees it. Reading it directly means the chatbot works without the user
having to understand why they needed a new terminal.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_VAR = "ANTHROPIC_API_KEY"


def find_api_key(root: Path) -> tuple[str | None, str]:
    """Return (key, where it came from). The key is None if nothing was found."""
    key = os.environ.get(ENV_VAR)
    if key:
        return key.strip(), "environment"

    key = _from_env_file(root / ".env")
    if key:
        return key, ".env file"

    if os.name == "nt":
        key = _from_windows_user_scope()
        if key:
            return key, "Windows user variables"

    return None, "nowhere"


def _from_env_file(path: Path) -> str | None:
    """Read KEY=value out of a .env file. No quoting rules beyond stripping."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == ENV_VAR:
            return value.strip().strip("'\"") or None
    return None


def _from_windows_user_scope() -> str | None:
    """Ask Windows for the persisted user variable this process did not inherit."""
    try:
        finished = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f'[Environment]::GetEnvironmentVariable("{ENV_VAR}","User")',
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("could not read user-scope variable: %s", exc)
        return None

    value = finished.stdout.strip()
    return value or None
