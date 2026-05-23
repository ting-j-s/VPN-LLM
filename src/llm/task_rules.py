"""Task-type-specific mandatory file rules.

Provides deterministic required-file lists per task type so the pipeline
does not rely solely on keyword-based recall for registration points
(factory.py, config.py) that must always be edited.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Required files for transport_addition (new {name} transport)
# ---------------------------------------------------------------------------

# Files that MUST be edited (exist on disk).
TRANSPORT_ADDITION_MUST_EDIT = [
    "src/transport/factory.py",
    "src/common/config.py",
]

# Files that MUST be created (do NOT exist yet).
TRANSPORT_ADDITION_MUST_CREATE = [
    "src/transport/{name}_transport.py",
    "tests/test_{name}_transport.py",
    "docs/transports/{name}.md",
]

# Files that MAY need editing depending on project conventions.
TRANSPORT_ADDITION_MAY_EDIT = [
    "src/transport/__init__.py",
]

# ---------------------------------------------------------------------------
# Required files for default_transport_change (future use)
# ---------------------------------------------------------------------------

DEFAULT_TRANSPORT_CHANGE_MUST_EDIT = [
    "config/client.yaml",
    "config/server.yaml",
]

DEFAULT_TRANSPORT_CHANGE_MAY_EDIT = [
    "README.md",
]


# ---------------------------------------------------------------------------
# Transport name detection
# ---------------------------------------------------------------------------

def detect_transport_name(request: str) -> str | None:
    """Extract a new transport name from a natural-language request.

    Matches patterns like:
      - "add socks5 transport"
      - "new http3 transport"
      - "implement quic protocol"
      - "create a new outer protocol: socks5"

    Returns the lowercased transport name or None.
    """
    text = request.lower().strip()

    patterns = [
        # "add/new/create/implement <name> transport/protocol"
        r'(?:add|new|create|implement)\s+(?:a\s+)?(?:new\s+)?(\w[\w.-]*?)\s+(?:transport|protocol|外层协议)',
        # "<name> transport/protocol" after a verb
        r'(?:generate|use|采用|使用|生成)\s+(?:a\s+)?(?:new\s+)?(\w[\w.-]*?)\s+(?:transport|protocol|外层协议)',
        # Chinese: "外层协议<name>" (no spaces between Chinese chars and name)
        r'外层协议\s*(\w[\w.-]+)',
    ]

    for pat in patterns:
        m = re.search(pat, text)
        if m:
            name = m.group(1).strip().lower()
            # Filter out common false positives
            if name in ("the", "this", "that", "and", "for", "with", "from",
                        "default", "current", "existing", "new", "a", "an"):
                continue
            return name

    return None


def get_transport_addition_required_files(transport_name: str) -> dict[str, list[str]]:
    """Return must_edit and must_create file lists for a new transport.

    Args:
        transport_name: Lowercased transport name (e.g. "socks5").

    Returns:
        Dict with keys ``must_edit``, ``must_create``, ``may_edit``.
    """
    return {
        "must_edit": list(TRANSPORT_ADDITION_MUST_EDIT),
        "must_create": [
            tmpl.format(name=transport_name)
            for tmpl in TRANSPORT_ADDITION_MUST_CREATE
        ],
        "may_edit": list(TRANSPORT_ADDITION_MAY_EDIT),
    }
