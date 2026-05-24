"""Task-type-specific mandatory file rules.

Provides deterministic required-file lists per task type so the pipeline
does not rely solely on keyword-based recall for registration points
(factory.py, config.py) that must always be edited.

The analyze_implementation_level() function now delegates to
intent_contract.infer_intent_contract() for richer task-type support.
"""

from __future__ import annotations

import os
import re

from src.llm.intent_contract import infer_intent_contract


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

# Additional files for runtime upgrade of existing skeleton
RUNTIME_UPGRADE_MUST_CREATE = [
    "config/examples/{name}_transport.yaml",
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


# ---------------------------------------------------------------------------
# Implementation level detection (skeleton vs runtime)
# ---------------------------------------------------------------------------

def analyze_implementation_level(request: str, task_type: str = "") -> dict:
    """Determine implementation level from request text.

    Delegates to intent_contract.infer_intent_contract() for richer
    classification covering skeleton/runtime/docs_only/config_only/
    evaluation_only/bugfix/refactor and more.

    Returns a backward-compatible dict plus the full IntentContract
    under the ``intent_contract`` key for callers that want richer data.
    """
    from src.llm.intent_contract import analyze_implementation_level_v2
    return analyze_implementation_level_v2(request, task_type=task_type)


# ---------------------------------------------------------------------------
# Runtime transport required validation rules
# ---------------------------------------------------------------------------

# Additional must_create files for runtime transport (beyond skeleton baseline)
RUNTIME_TRANSPORT_MUST_CREATE = []

# Test classes/methods that must exist for a runtime transport
RUNTIME_REQUIRED_TEST_PATTERNS = [
    "roundtrip", "send_recv", "client_server",
]


def get_transport_addition_required_files(transport_name: str, root_dir: str = ".") -> dict[str, list[str]]:
    """Return must_edit and must_create file lists for a new transport.

    When the target transport file already exists on disk (e.g. skeleton),
    it is moved from must_create to must_edit so the LLM upgrades it instead
    of creating a parallel bypass file.

    Args:
        transport_name: Lowercased transport name (e.g. "socks5").
        root_dir: Repository root directory.

    Returns:
        Dict with keys ``must_edit``, ``must_create``, ``may_edit``.
    """
    must_edit = list(TRANSPORT_ADDITION_MUST_EDIT)
    must_create: list[str] = []
    may_edit = list(TRANSPORT_ADDITION_MAY_EDIT)

    transport_file = f"src/transport/{transport_name}_transport.py"
    test_file = f"tests/test_{transport_name}_transport.py"
    doc_file = f"docs/transports/{transport_name}.md"
    config_example = f"config/examples/{transport_name}_transport.yaml"

    # Check whether each "must_create" file already exists on disk
    for tmpl_path in TRANSPORT_ADDITION_MUST_CREATE:
        resolved = tmpl_path.format(name=transport_name)
        full_path = os.path.join(root_dir, resolved)
        if os.path.isfile(full_path):
            must_edit.append(resolved)
        else:
            must_create.append(resolved)

    # Runtime upgrade: config example
    for tmpl_path in RUNTIME_UPGRADE_MUST_CREATE:
        resolved = tmpl_path.format(name=transport_name)
        full_path = os.path.join(root_dir, resolved)
        if os.path.isfile(full_path):
            must_edit.append(resolved)
        else:
            must_create.append(resolved)

    return {
        "must_edit": must_edit,
        "must_create": must_create,
        "may_edit": may_edit,
    }


def detect_existing_transport_file(transport_name: str, root_dir: str = ".") -> str | None:
    """Return the path of an existing transport file, or None.

    Args:
        transport_name: Lowercased transport name.
        root_dir: Repository root.

    Returns:
        File path string if the transport file exists, else None.
    """
    transport_file = f"src/transport/{transport_name}_transport.py"
    full_path = os.path.join(root_dir, transport_file)
    if os.path.isfile(full_path):
        return transport_file
    return None


def has_existing_transport_skeleton(transport_name: str, root_dir: str = ".") -> bool:
    """Check whether a transport skeleton file already exists on disk."""
    return detect_existing_transport_file(transport_name, root_dir) is not None
