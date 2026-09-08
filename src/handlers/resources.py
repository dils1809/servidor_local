"""The resources capability: what the model is allowed to read.

Resources are read-only documents. The model pulls them in when it needs the
wording of a policy instead of guessing at it.

The URI is never turned into a path. RESOURCES is a fixed table, so a request
for vibbo://policies/../../secrets does not resolve to anything: it is simply
not a key in the table.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from ..jsonrpc import InternalError, InvalidParams

logger = logging.getLogger(__name__)

CAPABILITY_NAME = "resources"
# subscribe: the client cannot ask to be notified about one resource.
# listChanged: the list is fixed at startup, so it never changes.
CAPABILITY_CONFIG: dict[str, Any] = {"subscribe": False, "listChanged": False}

MIME_TYPE = "text/markdown"

# Files live next to the database, in data/policies/.
POLICIES_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"

# The whole security model of this module: URI -> filename, nothing computed.
RESOURCE_DEFINITIONS: list[dict[str, Any]] = [
    {
        "uri": "vibbo://policies/shipping",
        "name": "shipping_policy",
        "title": "Shipping policy",
        "description": (
            "Processing times, delivery estimates by destination, carriers, "
            "how shipping is charged, customs, and what to do about a lost or "
            "misaddressed package."
        ),
        "mimeType": MIME_TYPE,
    },
    {
        "uri": "vibbo://policies/returns",
        "name": "returns_policy",
        "title": "Returns and refunds policy",
        "description": (
            "The 30-day return window, the 70%-remaining rule, who pays return "
            "shipping, refund timing, damaged items and cancellations."
        ),
        "mimeType": MIME_TYPE,
    },
    {
        "uri": "vibbo://policies/support-faq",
        "name": "support_faq",
        "title": "Customer support FAQ",
        "description": (
            "Approved answers to the questions customers ask most, and the "
            "list of things the assistant must never say or invent."
        ),
        "mimeType": MIME_TYPE,
    },
    {
        "uri": "vibbo://catalog/brewing",
        "name": "brewing_guide",
        "title": "Brewing guide and ingredients",
        "description": (
            "Steeping times per blend, the full ingredient list, allergens, "
            "caffeine content and storage."
        ),
        "mimeType": MIME_TYPE,
    },
]

# uri -> filename. Filenames are literals; nothing here is built from input.
RESOURCE_FILES: dict[str, str] = {
    "vibbo://policies/shipping": "shipping.md",
    "vibbo://policies/returns": "returns.md",
    "vibbo://policies/support-faq": "support-faq.md",
    "vibbo://catalog/brewing": "brewing.md",
}


class ResourceHandlers:
    """Implements resources/list and resources/read.

    The directory is injected so tests can point at a scratch folder.
    Files are read on every call, not cached, so editing a policy shows up
    without restarting the server.
    """

    def __init__(self, policies_dir: Path = POLICIES_DIR) -> None:
        self._dir = policies_dir

    def methods(self) -> dict[str, Callable[[dict[str, Any]], Any]]:
        return {
            "resources/list": self.list_resources,
            "resources/read": self.read_resource,
        }

    # -- resources/list ----------------------------------------------------
    def list_resources(self, params: dict[str, Any]) -> dict[str, Any]:
        # Four resources fit in one page, so no pagination cursor.
        return {"resources": RESOURCE_DEFINITIONS}

    # -- resources/read ----------------------------------------------------
    def read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise InvalidParams("'uri' is required and must be a string")

        filename = RESOURCE_FILES.get(uri)
        if filename is None:
            # Unknown URI is the client's mistake, so a protocol error.
            raise InvalidParams(
                "Unknown resource URI: " + uri,
                data={"uri": uri, "available": sorted(RESOURCE_FILES)},
            )

        path = self._dir / filename
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            # The URI is valid but the file is missing or unreadable. That is
            # a server-side problem, not something the client can fix.
            logger.error("cannot read resource %s from %s: %s", uri, path, exc)
            raise InternalError(
                "Resource is registered but could not be read",
                data={"uri": uri},
            ) from exc

        logger.debug("read resource %s (%d chars)", uri, len(text))
        return {
            "contents": [
                {"uri": uri, "name": _name_for(uri), "mimeType": MIME_TYPE, "text": text}
            ]
        }


def _name_for(uri: str) -> str:
    """Look up the short name declared in resources/list."""
    for definition in RESOURCE_DEFINITIONS:
        if definition["uri"] == uri:
            return definition["name"]
    return uri
