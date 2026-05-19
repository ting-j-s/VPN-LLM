"""Tests for src.llm.detection.patch_loop — PatchLoop CLI and logic."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.detection.patch_loop import (
    PatchLoopInput,
    PatchLoopResult,
    build_patch_loop_input,
    prepare_next_patch_prompt,
    main as patch_loop_main,
)
from src.llm.detection.gate import DetectionThresholds


def _run_main(*args: str) -> int:
    try:
        patch_loop_main(list(args))
    except SystemExit as e:
        return int(e.code)
    return 0


def _make_report_json(packet_count=100, risk_score=0.3, risk_level="low",
                      repeated_length_ratio=0.2, small_packet_ratio=0.1,
                      ngram_entropy=2.5, dominant_ngram_ratio=0.15,
                      dominant_burst_direction_ratio=0.5):
    return {
        "packet_count": packet_count,
        "risk_level": risk_level,
        "fingerprint_risk_score": risk_score,
        "small_packet_ratio": small_packet_ratio,
        "repeated_length_ratio": repeated_length_ratio,
        "ngram_entropy": ngram_entropy,
        "dominant_ngram_ratio": dominant_ngram_ratio,
        "dominant_burst_direction_ratio": dominant_burst_direction_ratio,
        "burst_count": 5,
        "max_burst_size": 1000,
        "avg_inter_arrival_ms": 10.0,
        "notes": [],
    }


# ---------------------------------------------------------------------------
# PatchLoopInput / PatchLoopResult
# ---------------------------------------------------------------------------

class TestPatchLoopInput:
    def test_defaults(self):
        inp = PatchLoopInput(user_request="test", report_paths=["/tmp/r.json"])
        assert inp.max_iterations == 1

    def test_to_dict(self):
        inp = PatchLoopInput(
            user_request="test",
            report_paths=["/tmp/r.json"],
            thresholds=DetectionThresholds(max_risk_score=0.5),
        )
        d = inp.to_dict()
        assert d["user_request"] == "test"


class TestPatchLoopResult:
    def test_to_dict(self):
        result = PatchLoopResult(
            should_request_patch=True,
            prompt="test prompt",
            summary={"total": 1, "failed": 1},
        )
        d = result.to_dict()
        assert d["should_request_patch"] is True
        assert d["prompt_length"] > 0


# ---------------------------------------------------------------------------
# build_patch_loop_input
# ---------------------------------------------------------------------------

class TestBuildPatchLoopInput:
    def test_builds_with_defaults(self):
        inp = build_patch_loop_input(
            user_request="test",
            report_paths=["/tmp/r.json"],
        )
        assert inp.user_request == "test"
        assert len(inp.report_paths) == 1

    def test_repo_status_from_file(self, tmp_path: Path):
        sf = tmp_path / "status.txt"
        sf.write_text("clean")
        inp = build_patch_loop_input(
            user_request="test",
            report_paths=[],
            repo_status="clean",
        )
        assert inp.repo_status == "clean"


# ---------------------------------------------------------------------------
# prepare_next_patch_prompt
# ---------------------------------------------------------------------------

class TestPrepareNextPatchPrompt:
    def test_all_pass_returns_no_patch(self, tmp_path: Path):
        p1 = tmp_path / "pass1.report.json"
        p1.write_text(json.dumps(_make_report_json(risk_score=0.3)))
        p2 = tmp_path / "pass2.report.json"
        p2.write_text(json.dumps(_make_report_json(risk_score=0.2)))

        inp = PatchLoopInput(
            user_request="test",
            repo_status="clean",
            functional_test_summary="all passed",
            report_paths=[str(p1), str(p2)],
            thresholds=DetectionThresholds(max_risk_score=0.70),
        )
        result = prepare_next_patch_prompt(inp)
        assert result.should_request_patch is False
        assert result.prompt == ""
        assert result.summary["passed"] == 2
        assert result.summary["failed"] == 0

    def test_failure_generates_prompt(self, tmp_path: Path):
        p1 = tmp_path / "pass.report.json"
        p1.write_text(json.dumps(_make_report_json(risk_score=0.3)))
        p2 = tmp_path / "fail.report.json"
        p2.write_text(json.dumps(_make_report_json(risk_score=0.95, risk_level="high")))

        inp = PatchLoopInput(
            user_request="reduce fingerprint risk",
            repo_status="clean",
            functional_test_summary="pytest passed",
            report_paths=[str(p1), str(p2)],
            thresholds=DetectionThresholds(max_risk_score=0.70),
        )
        result = prepare_next_patch_prompt(inp)
        assert result.should_request_patch is True
        assert len(result.prompt) > 100
        assert "FILE:" in result.prompt
        assert result.summary["passed"] == 1
        assert result.summary["failed"] == 1

    def test_multiple_mixed_reports(self, tmp_path: Path):
        p1 = tmp_path / "a.report.json"
        p1.write_text(json.dumps(_make_report_json(risk_score=0.3)))
        p2 = tmp_path / "b.report.json"
        p2.write_text(json.dumps(_make_report_json(risk_score=0.8, risk_level="high")))
        p3 = tmp_path / "c.report.json"
        p3.write_text(json.dumps(_make_report_json(risk_score=0.25)))

        inp = PatchLoopInput(
            user_request="reduce risk",
            report_paths=[str(p1), str(p2), str(p3)],
            thresholds=DetectionThresholds(max_risk_score=0.50),
        )
        result = prepare_next_patch_prompt(inp)
        assert result.should_request_patch is True
        assert result.summary["passed"] == 2
        assert result.summary["failed"] == 1

    def test_hints_generated_for_failed(self, tmp_path: Path):
        p = tmp_path / "fail.report.json"
        p.write_text(json.dumps(_make_report_json(
            risk_score=0.85, risk_level="high",
            repeated_length_ratio=0.85,
            small_packet_ratio=0.75,
        )))

        inp = PatchLoopInput(
            user_request="reduce fingerprint",
            report_paths=[str(p)],
            thresholds=DetectionThresholds(
                max_risk_score=0.70,
                max_repeated_length_ratio=0.60,
                max_small_packet_ratio=0.60,
            ),
        )
        result = prepare_next_patch_prompt(inp)
        assert result.should_request_patch is True
        assert len(result.hints) > 0
        hint_names = {h.metric_name for h in result.hints}
        assert "repeated_length_ratio" in hint_names or "fingerprint_risk_score" in hint_names


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestPatchLoopCli:
    def test_all_pass_exit_0(self, tmp_path: Path):
        p = tmp_path / "pass.report.json"
        p.write_text(json.dumps(_make_report_json(risk_score=0.3)))
        rc = _run_main(
            "--user-request", "test",
            "--report", str(p),
            "--max-risk-score", "0.70",
        )
        assert rc == 0

    def test_fail_exit_1(self, tmp_path: Path):
        p = tmp_path / "fail.report.json"
        p.write_text(json.dumps(_make_report_json(risk_score=0.95, risk_level="high")))
        rc = _run_main(
            "--user-request", "test",
            "--report", str(p),
            "--max-risk-score", "0.70",
        )
        assert rc == 1

    def test_missing_report_exit_2(self, tmp_path: Path):
        rc = _run_main(
            "--user-request", "test",
            "--report", str(tmp_path / "nonexistent.json"),
        )
        assert rc == 2

    def test_no_reports_exit_2(self):
        rc = _run_main(
            "--user-request", "test",
        )
        assert rc == 2

    def test_writes_prompt_file(self, tmp_path: Path):
        p = tmp_path / "fail.report.json"
        p.write_text(json.dumps(_make_report_json(risk_score=0.95, risk_level="high")))
        prompt_out = tmp_path / "prompt.txt"
        json_out = tmp_path / "result.json"

        rc = _run_main(
            "--user-request", "reduce fingerprint risk",
            "--report", str(p),
            "--max-risk-score", "0.70",
            "--output-prompt", str(prompt_out),
            "--output-json", str(json_out),
        )
        assert rc == 1
        assert prompt_out.is_file()
        content = prompt_out.read_text()
        assert "FILE:" in content
        assert "reduce fingerprint risk" in content

    def test_writes_json_output(self, tmp_path: Path):
        p = tmp_path / "fail.report.json"
        p.write_text(json.dumps(_make_report_json(risk_score=0.95, risk_level="high")))
        json_out = tmp_path / "result.json"

        rc = _run_main(
            "--user-request", "reduce fingerprint risk",
            "--report", str(p),
            "--max-risk-score", "0.70",
            "--output-json", str(json_out),
        )
        assert rc == 1
        data = json.loads(json_out.read_text())
        assert data["should_request_patch"] is True
        assert "failed_reports" in data
        assert "passed_reports" in data
        assert "hints" in data
        assert "summary" in data
        assert "input_config" in data

    def test_custom_thresholds(self, tmp_path: Path):
        p = tmp_path / "report.json"
        p.write_text(json.dumps(_make_report_json(
            risk_score=0.5,
            repeated_length_ratio=0.7,
            small_packet_ratio=0.7,
        )))
        rc = _run_main(
            "--user-request", "test",
            "--report", str(p),
            "--max-risk-score", "0.80",
            "--max-repeated-length-ratio", "0.50",
            "--max-small-packet-ratio", "0.50",
        )
        assert rc == 1  # fails due to repeated_length_ratio and small_packet_ratio
