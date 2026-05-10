#!/usr/bin/env bash
# Validate the LLM Agent framework modules.
# Runs: compileall, targeted tests, full tests, git status.

set -euo pipefail

echo "=== compileall ==="
python3 -m compileall src tests

echo ""
echo "=== targeted tests (LLM Agent modules) ==="
python3 -m pytest tests/test_validation_runner.py tests/test_safety_guard.py tests/test_task_planner.py tests/test_task_record.py tests/test_report_writer.py -v

echo ""
echo "=== full test suite ==="
python3 -m pytest tests/ -v

echo ""
echo "=== git status ==="
git status --short

echo ""
echo "=== validation complete ==="
