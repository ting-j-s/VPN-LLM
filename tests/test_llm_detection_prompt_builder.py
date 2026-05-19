"""Tests for src.llm.detection.prompt_builder — adversarial patch prompt generation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.detection.detector_report import DetectionMetric, DetectionReport
from src.llm.detection.countermeasure_policy import CountermeasureHint
from src.llm.detection.prompt_builder import build_adversarial_patch_prompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_failed_report():
    return DetectionReport(
        detector_name="fingerprint",
        source_path="traces/tcp/idle.report.json",
        transport="tcp",
        scenario="idle",
        passed=False,
        risk_score=0.85,
        risk_level="medium",
        trace_type="real",
        metrics=[
            DetectionMetric(name="repeated_length_ratio", value=0.85, threshold=0.60,
                           passed=False, severity="fail",
                           explanation="repeated_length_ratio=0.85 > max=0.60"),
            DetectionMetric(name="small_packet_ratio", value=0.75, threshold=0.60,
                           passed=False, severity="fail",
                           explanation="small_packet_ratio=0.75 > max=0.60"),
            DetectionMetric(name="ngram_entropy", value=1.5, threshold=None,
                           passed=True, severity="info",
                           explanation="ngram_entropy=1.5"),
        ],
        notes=["high repeated-length ratio"],
    )


def _make_hints():
    return [
        CountermeasureHint(
            metric_name="repeated_length_ratio",
            problem="High repeated-length ratio",
            recommended_changes=["random padding", "length bucket randomization"],
            affected_layers=["frame codec", "traffic shaper"],
            tradeoffs=["bandwidth overhead"],
            avoid=["fixed-size padding only"],
        ),
        CountermeasureHint(
            metric_name="small_packet_ratio",
            problem="High small-packet ratio",
            recommended_changes=["frame aggregation", "delayed flush"],
            affected_layers=["traffic scheduler"],
            tradeoffs=["latency increase"],
            avoid=["deterministic flush interval"],
        ),
    ]


# ---------------------------------------------------------------------------
# Prompt content tests
# ---------------------------------------------------------------------------

class TestBuildAdversarialPatchPrompt:
    def test_prompt_contains_user_request(self):
        prompt = build_adversarial_patch_prompt(
            user_request="reduce fingerprint risk",
            repo_status="clean",
            functional_test_summary="all passed",
            detection_report=_make_failed_report(),
            countermeasure_hints=_make_hints(),
        )
        assert "reduce fingerprint risk" in prompt

    def test_prompt_contains_repo_status(self):
        prompt = build_adversarial_patch_prompt(
            user_request="test",
            repo_status="On branch test-2\nnothing to commit",
            functional_test_summary="passed",
            detection_report=_make_failed_report(),
            countermeasure_hints=_make_hints(),
        )
        assert "On branch test-2" in prompt

    def test_prompt_contains_functional_test_summary(self):
        prompt = build_adversarial_patch_prompt(
            user_request="test",
            repo_status="clean",
            functional_test_summary="compileall passed; pytest 874 passed",
            detection_report=_make_failed_report(),
            countermeasure_hints=_make_hints(),
        )
        assert "874 passed" in prompt

    def test_prompt_contains_failed_metrics(self):
        prompt = build_adversarial_patch_prompt(
            user_request="test",
            repo_status="clean",
            functional_test_summary="passed",
            detection_report=_make_failed_report(),
            countermeasure_hints=_make_hints(),
        )
        assert "repeated_length_ratio" in prompt
        assert "small_packet_ratio" in prompt
        assert "FAIL" in prompt

    def test_prompt_contains_countermeasure_hints(self):
        prompt = build_adversarial_patch_prompt(
            user_request="test",
            repo_status="clean",
            functional_test_summary="passed",
            detection_report=_make_failed_report(),
            countermeasure_hints=_make_hints(),
        )
        assert "random padding" in prompt
        assert "frame aggregation" in prompt

    def test_prompt_requires_patch_output(self):
        prompt = build_adversarial_patch_prompt(
            user_request="test",
            repo_status="clean",
            functional_test_summary="passed",
            detection_report=_make_failed_report(),
            countermeasure_hints=_make_hints(),
        )
        assert "FILE:" in prompt
        assert "ACTION:" in prompt

    def test_prompt_requires_tests(self):
        prompt = build_adversarial_patch_prompt(
            user_request="test",
            repo_status="clean",
            functional_test_summary="passed",
            detection_report=_make_failed_report(),
            countermeasure_hints=_make_hints(),
        )
        assert "tests" in prompt.lower()

    def test_prompt_contains_safety_boundaries(self):
        prompt = build_adversarial_patch_prompt(
            user_request="test",
            repo_status="clean",
            functional_test_summary="passed",
            detection_report=_make_failed_report(),
            countermeasure_hints=_make_hints(),
        )
        assert "safety" in prompt.lower()
        assert "third-party" in prompt or "third party" in prompt

    def test_prompt_contains_patch_protocol(self):
        prompt = build_adversarial_patch_prompt(
            user_request="test",
            repo_status="clean",
            functional_test_summary="passed",
            detection_report=_make_failed_report(),
            countermeasure_hints=_make_hints(),
        )
        assert "<<<FIND" in prompt
        assert "<<<REPLACE" in prompt

    def test_prompt_does_not_ask_to_scan_third_parties(self):
        prompt = build_adversarial_patch_prompt(
            user_request="test",
            repo_status="clean",
            functional_test_summary="passed",
            detection_report=_make_failed_report(),
            countermeasure_hints=_make_hints(),
        )
        assert "scan" not in prompt.lower() or "not scan" in prompt.lower() or "not generate code that scans" in prompt.lower()

    def test_prompt_with_custom_patch_protocol(self):
        custom_protocol = "CUSTOM PROTOCOL HERE"
        prompt = build_adversarial_patch_prompt(
            user_request="test",
            repo_status="clean",
            functional_test_summary="passed",
            detection_report=_make_failed_report(),
            countermeasure_hints=_make_hints(),
            patch_protocol=custom_protocol,
        )
        assert custom_protocol in prompt

    def test_prompt_contains_paper_mapping(self):
        prompt = build_adversarial_patch_prompt(
            user_request="test",
            repo_status="clean",
            functional_test_summary="passed",
            detection_report=_make_failed_report(),
            countermeasure_hints=_make_hints(),
        )
        assert "OpenVPN" in prompt or "Encapsulated" in prompt

    def test_no_hints_generates_empty_section(self):
        report = DetectionReport(
            detector_name="fingerprint",
            passed=True,
            metrics=[],
        )
        prompt = build_adversarial_patch_prompt(
            user_request="test",
            repo_status="clean",
            functional_test_summary="passed",
            detection_report=report,
            countermeasure_hints=[],
        )
        assert "no countermeasure hints" in prompt.lower()
        # Still has protocol requirements
        assert "FILE:" in prompt
