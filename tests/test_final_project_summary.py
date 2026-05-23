"""Tests for scripts/generate_final_project_summary.py (Phase 11)."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.generate_final_project_summary import (
    generate_summary,
    render_markdown,
    _git_head,
    _collect_coverage_matrix,
    _collect_final_report,
    _collect_phase9_results,
    _collect_phase10_results,
    _collect_integrated_summary,
)


def _make_mock_popen(return_value=None):
    def _mock_check_output(*args, **kwargs):
        if return_value is not None:
            return return_value
        return "mock-output"
    return _mock_check_output


# --- Test 1: missing outputs does not crash ---

def test_generate_summary_no_crash_with_missing_outputs(tmp_path, monkeypatch):
    """generate_summary must not crash when no output data exists."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "scripts.generate_final_project_summary.REPO_ROOT", tmp_path
    )

    mock_rev = MagicMock(return_value="a" * 40)
    with patch("subprocess.check_output", mock_rev):
        summary = generate_summary(output_dir=str(tmp_path))

    assert "generated_at" in summary
    assert summary["git"]["commit"] != "unknown"
    assert summary["evaluation"]["phase9_real_trace_matrix"]["status"] == "missing"
    assert summary["evaluation"]["integrated_summary"]["status"] == "missing"


# --- Test 2: JSON output structure ---

def test_generate_summary_json_structure(tmp_path, monkeypatch):
    """JSON output must have required top-level keys."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "scripts.generate_final_project_summary.REPO_ROOT", tmp_path
    )

    mock_rev = MagicMock(return_value="b" * 40)
    with patch("subprocess.check_output", mock_rev):
        summary = generate_summary(output_dir=str(tmp_path))

    required_keys = [
        "generated_at", "generated_by", "phase", "git",
        "docs", "evaluation", "test_baseline", "future_work", "safety_boundary",
    ]
    for key in required_keys:
        assert key in summary, f"Missing key: {key}"

    assert summary["phase"] == "Phase 11 — Final Integrated Report and Project Convergence"
    assert isinstance(summary["future_work"], list)
    assert isinstance(summary["safety_boundary"], list)


# --- Test 3: Markdown contains LLM workflow ---

def test_render_markdown_contains_llm_workflow(tmp_path, monkeypatch):
    """Markdown output must mention LLM workflow."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "scripts.generate_final_project_summary.REPO_ROOT", tmp_path
    )

    mock_rev = MagicMock(return_value="c" * 40)
    with patch("subprocess.check_output", mock_rev):
        summary = generate_summary(output_dir=str(tmp_path))

    md = render_markdown(summary)
    assert "LLM Workflow" in md
    assert "DetectionReport" in md
    assert "LLM" in md


# --- Test 4: Markdown contains evaluation gates ---

def test_render_markdown_contains_evaluation_gates(tmp_path, monkeypatch):
    """Markdown output must list evaluation gates."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "scripts.generate_final_project_summary.REPO_ROOT", tmp_path
    )

    mock_rev = MagicMock(return_value="d" * 40)
    with patch("subprocess.check_output", mock_rev):
        summary = generate_summary(output_dir=str(tmp_path))

    md = render_markdown(summary)
    assert "Fingerprint Gate" in md
    assert "Active Probe Gate" in md
    assert "Cross-Layer RTT Gate" in md
    assert "Real Trace Matrix" in md


# --- Test 5: Markdown contains HTTP/2 phases ---

def test_render_markdown_contains_http2_phases(tmp_path, monkeypatch):
    """Markdown output must mention HTTP/2 Phase 10 progression."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "scripts.generate_final_project_summary.REPO_ROOT", tmp_path
    )

    mock_rev = MagicMock(return_value="e" * 40)
    with patch("subprocess.check_output", mock_rev):
        summary = generate_summary(output_dir=str(tmp_path))

    md = render_markdown(summary)
    assert "10C" in md
    assert "10D" in md
    assert "10E-A" in md
    assert "10E-B" in md


# --- Test 6: Git metadata can be mocked ---

def test_git_head_mocked():
    """_git_head must return mocked values when subprocess is patched."""
    def mock_check_output(args, **kwargs):
        cmd = args[0] if isinstance(args, list) else args
        if "rev-parse" in str(args) and "--short" in str(args):
            return "abc1234\n"
        elif "rev-parse" in str(args) and "--abbrev-ref" in str(args):
            return "test-branch\n"
        elif "rev-list" in str(args):
            return "42\n"
        elif "log" in str(args):
            return "Mock commit message\n"
        return "a" * 40 + "\n"

    with patch("subprocess.check_output", side_effect=mock_check_output):
        result = _git_head()

    assert result["commit"] == "a" * 40
    assert result["commit_short"] == "abc1234"
    assert result["branch"] == "test-branch"
    assert result["commit_message"] == "Mock commit message"
    assert result["total_commits"] == 42


def test_git_head_handles_subprocess_error():
    """_git_head must return 'unknown' when subprocess fails."""
    def mock_check_output(*args, **kwargs):
        raise OSError("git not found")

    with patch("subprocess.check_output", side_effect=mock_check_output):
        result = _git_head()

    assert result["commit"] == "unknown"
    assert result["branch"] == "unknown"
    assert result["total_commits"] == "unknown"


# --- Test 7: future work appears in both JSON and Markdown ---

def test_future_work_in_json_and_markdown(tmp_path, monkeypatch):
    """future_work must appear in JSON output and Markdown rendering."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "scripts.generate_final_project_summary.REPO_ROOT", tmp_path
    )

    mock_rev = MagicMock(return_value="f" * 40)
    with patch("subprocess.check_output", mock_rev):
        summary = generate_summary(output_dir=str(tmp_path))

    # JSON
    assert len(summary["future_work"]) > 0
    assert any("HPACK" in item for item in summary["future_work"])

    # Markdown
    md = render_markdown(summary)
    assert "Future Work" in md
    assert "HPACK" in md


# --- Test 8: safety boundary appears in both JSON and Markdown ---

def test_safety_boundary_in_json_and_markdown(tmp_path, monkeypatch):
    """safety_boundary must appear in JSON output and Markdown rendering."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "scripts.generate_final_project_summary.REPO_ROOT", tmp_path
    )

    mock_rev = MagicMock(return_value="g" * 40)
    with patch("subprocess.check_output", mock_rev):
        summary = generate_summary(output_dir=str(tmp_path))

    # JSON
    assert len(summary["safety_boundary"]) > 0
    assert any("undetectability" in item for item in summary["safety_boundary"])

    # Markdown
    md = render_markdown(summary)
    assert "Safety Boundary" in md
    assert "undetectability" in md


# --- Test 9: coverage matrix collection works when file exists ---

def test_collect_coverage_matrix_exists(monkeypatch):
    """_collect_coverage_matrix returns ok when the file exists."""
    import scripts.generate_final_project_summary as mod
    monkeypatch.setattr(
        mod, "REPO_ROOT", Path(__file__).resolve().parent.parent
    )
    result = _collect_coverage_matrix()
    assert result["status"] == "ok"
    assert result["size_bytes"] is not None


# --- Test 10: final report collection works when file exists ---

def test_collect_final_report_exists(monkeypatch):
    """_collect_final_report returns ok when the file exists."""
    import scripts.generate_final_project_summary as mod
    monkeypatch.setattr(
        mod, "REPO_ROOT", Path(__file__).resolve().parent.parent
    )
    result = _collect_final_report()
    assert result["status"] == "ok"
    assert result["size_bytes"] is not None


# --- Test 11: phase 9 collection with missing outputs ---

def test_collect_phase9_missing(tmp_path, monkeypatch):
    """_collect_phase9_results returns missing when no outputs exist."""
    monkeypatch.setattr(
        "scripts.generate_final_project_summary.REPO_ROOT", tmp_path
    )
    result = _collect_phase9_results()
    assert result["status"] == "missing"
    assert result["total_entries"] == 0


# --- Test 12: phase 10 collection with missing outputs ---

def test_collect_phase10_missing(tmp_path, monkeypatch):
    """_collect_phase10_results returns missing for all phases."""
    monkeypatch.setattr(
        "scripts.generate_final_project_summary.REPO_ROOT", tmp_path
    )
    result = _collect_phase10_results()
    for label in ["10C", "10D", "10E-A", "10E-B"]:
        assert label in result
        assert result[label]["status"] == "missing"


# --- Test 13: integrated summary with missing outputs ---

def test_collect_integrated_summary_missing(tmp_path, monkeypatch):
    """_collect_integrated_summary returns missing when no output."""
    monkeypatch.setattr(
        "scripts.generate_final_project_summary.REPO_ROOT", tmp_path
    )
    result = _collect_integrated_summary()
    assert result["status"] == "missing"
