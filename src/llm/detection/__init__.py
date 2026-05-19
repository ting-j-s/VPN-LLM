"""LLM Detection-Adversarial Workflow (Phase 4).

Modules:
- detector_report: DetectionReport / DetectionMetric dataclasses
- gate: DetectionThresholds and evaluation functions
- countermeasure_policy: Metric → countermeasure hint mapping
- prompt_builder: Adversarial patch prompt generation
- patch_loop: End-to-end detection → patch prompt CLI
"""
