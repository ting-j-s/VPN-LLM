"""Active Probe Resistance Evaluation.

Local-only controlled testing of VPN-LLM server behavior under malformed
or unexpected input. Inspired by OpenVPN active probing fingerprinting.

This module:
- Defines probe scenarios (malformed frames, unexpected data)
- Runs probes against a local server instance
- Produces a report with response variance metrics
- Feeds into the DetectionGate / CountermeasurePolicy / LLM patch loop

SECURITY: All probe payloads are for local 127.0.0.1 / ::1 testing only.
No third-party scanning is implemented or permitted.
"""

from .probe_scenarios import ProbeScenario, PROBE_SCENARIOS, get_scenario_by_name
from .probe_runner import (
    ProbeResult,
    MockProbeRunner,
    LocalSocketProbeRunner,
    create_probe_runner,
)

__all__ = [
    "ProbeScenario",
    "PROBE_SCENARIOS",
    "get_scenario_by_name",
    "ProbeResult",
    "MockProbeRunner",
    "LocalSocketProbeRunner",
    "create_probe_runner",
]
