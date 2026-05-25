"""User Intent Validator — validates patch against IntentContract.

Runs after PatchGenerator and CompletenessChecker to verify that the
generated patch actually satisfies the user's real intent, not just
structural/syntax requirements.

Supports diverse task types: transport, shaping, evaluation, config,
docs, bugfix, refactor, script, and more.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from src.llm.intent_contract import IntentContract, AcceptanceCriterion


@dataclass
class AcceptanceEvidence:
    """Evidence gathered for a single acceptance criterion."""

    criterion_name: str
    satisfied: bool
    evidence: str = ""
    detail: str = ""


@dataclass
class UserIntentValidationResult:
    """Result of validating a patch against the user's IntentContract.

    Three validation layers:
    1. patch_integrity: required files, no truncation, syntax OK
    2. functional_validation: compileall, tests pass
    3. user_intent: acceptance criteria satisfied
    """

    # --- Status flags ---
    patch_integrity_status: str = "not_run"   # passed / failed / not_run
    functional_validation_status: str = "not_run"
    user_intent_status: str = "not_run"  # passed / failed / partial

    # --- Details ---
    unmet_acceptance_criteria: list[str] = field(default_factory=list)
    evidence: list[AcceptanceEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    was_downgraded: bool = False
    downgrade_allowed: bool = True
    downgrade_detail: str = ""

    # --- Module boundary validation ---
    module_boundary_status: str = "not_run"  # passed / failed / not_run
    forbidden_file_changes: list[str] = field(default_factory=list)
    missing_required_files: list[str] = field(default_factory=list)
    missing_required_evidence: list[str] = field(default_factory=list)
    module_completion_allowed: bool = True

    # --- Final task status ---
    final_task_status: str = "not_evaluated"
    # One of: completed, completed_with_warnings, intent_not_satisfied,
    #         patch_generation_failed, validation_failed

    def to_dict(self) -> dict:
        return {
            "patch_integrity_status": self.patch_integrity_status,
            "functional_validation_status": self.functional_validation_status,
            "user_intent_status": self.user_intent_status,
            "unmet_acceptance_criteria": self.unmet_acceptance_criteria,
            "evidence": [
                {"criterion_name": e.criterion_name, "satisfied": e.satisfied,
                 "evidence": e.evidence, "detail": e.detail}
                for e in self.evidence
            ],
            "errors": self.errors,
            "warnings": self.warnings,
            "was_downgraded": self.was_downgraded,
            "downgrade_allowed": self.downgrade_allowed,
            "downgrade_detail": self.downgrade_detail,
            "module_boundary_status": self.module_boundary_status,
            "forbidden_file_changes": self.forbidden_file_changes,
            "missing_required_files": self.missing_required_files,
            "missing_required_evidence": self.missing_required_evidence,
            "module_completion_allowed": self.module_completion_allowed,
            "final_task_status": self.final_task_status,
        }


class UserIntentValidator:
    """Validate a generated patch against the user's IntentContract.

    Usage:
        validator = UserIntentValidator()
        result = validator.validate(
            patch_text=patch_text,
            intent_contract=plan.intent_contract,
            patch_file_paths=["src/transport/x_transport.py", ...],
            completeness_result=completeness,
            runtime_check_result=runtime_check,
            compile_ok=True,
            tests_ok=True,
        )
    """

    # Known bypass file name patterns
    _BYPASS_FILE_PATTERNS = [
        re.compile(rf'src/transport/(\w+)_full_transport\.py'),
        re.compile(rf'src/transport/(\w+)_runtime_transport\.py'),
        re.compile(rf'src/transport/(\w+)_new_transport\.py'),
        re.compile(rf'src/transport/(\w+)_v2_transport\.py'),
        re.compile(rf'src/transport/(\w+)_real_transport\.py'),
    ]

    def validate(
        self,
        patch_text: str | None,
        intent_contract: IntentContract | None,
        patch_file_paths: list[str] | None = None,
        completeness_result=None,
        runtime_check_result=None,
        compile_ok: bool = True,
        tests_ok: bool = True,
        git_apply_check_ok: bool | None = None,
        tunnel_smoke_result=None,
        module_resolution=None,
    ) -> UserIntentValidationResult:
        """Validate the patch against the user's IntentContract.

        Args:
            patch_text: The generated unified diff text (or None if generation failed).
            intent_contract: The IntentContract from the Planner.
            patch_file_paths: Files referenced in the patch.
            completeness_result: PatchCompletenessResult from completeness check.
            runtime_check_result: RuntimeTransportCheckResult from runtime check.
            compile_ok: Whether compileall passed.
            tests_ok: Whether tests passed.
            git_apply_check_ok: Whether git apply --check passed (None if not run).
            tunnel_smoke_result: TunnelSmokeValidation from tunnel smoke check.

        Returns:
            UserIntentValidationResult with full status details.
        """
        if patch_text is None and intent_contract is None:
            return UserIntentValidationResult(
                patch_integrity_status="failed",
                functional_validation_status="not_run",
                user_intent_status="not_run",
                final_task_status="patch_generation_failed",
                errors=["No patch text and no intent contract provided"],
            )

        if patch_text is None:
            return UserIntentValidationResult(
                patch_integrity_status="failed",
                functional_validation_status="not_run",
                user_intent_status="not_run",
                final_task_status="patch_generation_failed",
                errors=["Patch generation failed — no patch text available"],
            )

        if intent_contract is None:
            return UserIntentValidationResult(
                patch_integrity_status="passed",
                functional_validation_status="passed" if compile_ok and tests_ok else "failed",
                user_intent_status="not_run",
                final_task_status="completed_with_warnings",
                warnings=["No IntentContract available — user intent not validated"],
            )

        patch_paths = patch_file_paths or []
        result = UserIntentValidationResult()

        # ---- Layer 0: Module boundary validation ----
        if module_resolution is not None:
            from src.llm.task_modules import check_module_boundary
            boundary = check_module_boundary(module_resolution, patch_paths)
            result.module_boundary_status = boundary["module_boundary_status"]
            result.forbidden_file_changes = boundary["forbidden_file_changes"]
            result.missing_required_files = boundary["missing_required_files"]
            result.missing_required_evidence = boundary["missing_required_evidence"]
            result.module_completion_allowed = boundary["module_completion_allowed"]

            if boundary["forbidden_file_changes"]:
                result.errors.append(
                    f"Forbidden files modified: {boundary['forbidden_file_changes']}"
                )
            if boundary["missing_required_files"]:
                result.errors.append(
                    f"Required files missing: {boundary['missing_required_files']}"
                )

        # ---- Layer 1: Patch integrity ----
        integrity_ok = self._check_patch_integrity(
            result, patch_text, completeness_result, git_apply_check_ok,
        )
        result.patch_integrity_status = "passed" if integrity_ok else "failed"

        # ---- Layer 2: Functional validation ----
        func_ok = compile_ok and tests_ok
        if not compile_ok:
            result.errors.append("Compile check failed")
        if not tests_ok:
            result.errors.append("Test suite has failures")
        result.functional_validation_status = "passed" if func_ok else "failed"

        # ---- Layer 3: User intent validation ----
        self._check_user_intent(result, patch_text, patch_paths, intent_contract,
                                runtime_check_result)

        # ---- Detect downgrade ----
        self._check_downgrade(result, intent_contract, runtime_check_result)

        # ---- Tunnel smoke validation ----
        if tunnel_smoke_result is not None:
            self._check_tunnel_smoke(result, intent_contract, tunnel_smoke_result)

        # ---- Determine final task status ----
        result.final_task_status = self._determine_final_status(
            result, intent_contract, tunnel_smoke_result
        )

        return result

    # ------------------------------------------------------------------
    # Layer 1: Patch integrity
    # ------------------------------------------------------------------

    @staticmethod
    def _check_patch_integrity(
        result: UserIntentValidationResult,
        patch_text: str,
        completeness_result,
        git_apply_check_ok: bool | None,
    ) -> bool:
        ok = True

        if completeness_result is not None:
            if not completeness_result.passed:
                ok = False
                if completeness_result.missing_files:
                    result.errors.append(
                        f"Missing files in patch: {completeness_result.missing_files}"
                    )
                if completeness_result.truncated_files:
                    result.errors.append(
                        f"Truncated files in patch: {completeness_result.truncated_files}"
                    )
                if completeness_result.syntax_errors:
                    result.errors.append(
                        f"Syntax errors: {completeness_result.syntax_errors}"
                    )

        if git_apply_check_ok is False:
            ok = False
            result.errors.append("git apply --check failed")

        # Basic patch structure check
        if "diff --git" not in patch_text and "FILE:" not in patch_text:
            ok = False
            result.errors.append("Patch text has no recognizable diff structure")

        return ok

    # ------------------------------------------------------------------
    # Layer 3: User intent
    # ------------------------------------------------------------------

    def _check_user_intent(
        self,
        result: UserIntentValidationResult,
        patch_text: str,
        patch_paths: list[str],
        contract: IntentContract,
        runtime_check_result,
    ) -> None:
        """Check each acceptance criterion against the patch."""
        all_satisfied = True
        any_satisfied = False

        for criterion in contract.acceptance_criteria:
            evidence = self._evaluate_criterion(
                criterion, patch_text, patch_paths, contract, runtime_check_result,
            )
            result.evidence.append(evidence)

            if evidence.satisfied:
                any_satisfied = True
            elif criterion.required:
                all_satisfied = False
                result.unmet_acceptance_criteria.append(
                    f"[{criterion.category}] {criterion.name}: {criterion.failure_message}"
                )

        # Also check forbidden degradations
        self._check_forbidden_degradations(result, patch_text, patch_paths, contract)

        # Check for bypass files (e.g. socks5_full_transport.py)
        if contract.runtime_wiring_required:
            bypass_paths = self._check_bypass_files(patch_paths, contract)
            if bypass_paths:
                result.errors.append(
                    f"RUNTIME WIRING VIOLATION: Bypass file(s) detected that are "
                    f"not wired to the requested transport name: {bypass_paths}. "
                    f"Use the canonical path src/transport/{contract.target_transport}_transport.py."
                )
                # Force user_intent_status to failed if end-to-end required
                if contract.end_to_end_required:
                    all_satisfied = False

        if not result.evidence:
            result.user_intent_status = "not_run"
        elif all_satisfied:
            result.user_intent_status = "passed"
        elif any_satisfied:
            result.user_intent_status = "partial"
        else:
            result.user_intent_status = "failed"

    def _evaluate_criterion(
        self,
        criterion: AcceptanceCriterion,
        patch_text: str,
        patch_paths: list[str],
        contract: IntentContract,
        runtime_check_result,
    ) -> AcceptanceEvidence:
        """Evaluate a single acceptance criterion against the patch."""
        category = criterion.category
        name = criterion.name

        if category == "syntax":
            return self._eval_syntax(name, patch_text)

        if category == "structure":
            return self._eval_structure(name, criterion, patch_text, patch_paths, contract)

        if category == "runtime":
            return self._eval_runtime(name, criterion, patch_text, runtime_check_result)

        if category == "unit_test":
            return self._eval_unit_test(name, criterion, patch_text, patch_paths)

        if category == "config":
            return self._eval_config(name, criterion, patch_text, patch_paths)

        if category == "cli":
            return self._eval_cli(name, criterion, patch_text, patch_paths)

        if category == "evaluation":
            return self._eval_evaluation(name, criterion, patch_text, patch_paths)

        if category == "backward_compatibility":
            return self._eval_backward_compat(name, criterion, patch_text, patch_paths)

        if category == "security_boundary":
            return self._eval_security_boundary(name, criterion, patch_text, runtime_check_result)

        if category == "docs":
            return self._eval_docs(name, criterion, patch_text, patch_paths)

        # Unknown category — accept by default but warn
        return AcceptanceEvidence(
            criterion_name=name,
            satisfied=True,
            evidence="unknown category, accepted by default",
            detail=f"Category '{category}' has no evaluator",
        )

    # ------------------------------------------------------------------
    # Per-category evaluators
    # ------------------------------------------------------------------

    @staticmethod
    def _eval_syntax(name: str, patch_text: str) -> AcceptanceEvidence:
        # Syntax is checked by completeness checker; here we trust it
        return AcceptanceEvidence(
            criterion_name=name,
            satisfied=True,
            evidence="delegated to completeness checker / compileall",
            detail="",
        )

    @staticmethod
    def _eval_structure(
        name: str, criterion: AcceptanceCriterion,
        patch_text: str, patch_paths: list[str], contract: IntentContract,
    ) -> AcceptanceEvidence:
        transport = contract.target_transport or _extract_transport_from_patch(patch_text)

        if name == "importable_and_constructable":
            transport_file = f"src/transport/{transport}_transport.py" if transport else None
            if transport_file and transport_file in patch_paths:
                return AcceptanceEvidence(
                    criterion_name=name, satisfied=True,
                    evidence=f"Transport file {transport_file} present in patch",
                    detail="",
                )
            return AcceptanceEvidence(
                criterion_name=name, satisfied=False,
                evidence="",
                detail=f"Transport implementation file not found in patch paths: {patch_paths}",
            )

        if name == "factory_registered":
            factory_in_patch = any("factory.py" in p for p in patch_paths)
            config_in_patch = any("config.py" in p for p in patch_paths)
            satisfied = factory_in_patch or config_in_patch
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=satisfied,
                evidence=f"factory.py in patch: {factory_in_patch}, config.py in patch: {config_in_patch}",
                detail="" if satisfied else "Transport not registered in factory or config",
            )

        if name == "wired_correctly":
            # Verify the transport file is at the expected canonical path,
            # not at a bypass path like socks5_full_transport.py
            bypass_paths = _detect_bypass_files(patch_paths, transport)
            has_canonical = transport and any(
                p == f"src/transport/{transport}_transport.py" for p in patch_paths
            )
            satisfied = has_canonical and not bypass_paths
            detail = ""
            if bypass_paths:
                detail = (f"Bypass file(s) detected: {bypass_paths}. "
                          f"Runtime implementation is not wired to requested transport name.")
            elif not has_canonical:
                detail = (f"Canonical transport file src/transport/{transport}_transport.py "
                          f"not found in patch paths: {patch_paths}")
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=satisfied,
                evidence=f"canonical path: {has_canonical}, bypass files: {bypass_paths}",
                detail=detail,
            )

        return AcceptanceEvidence(
            criterion_name=name, satisfied=True,
            evidence="structure criterion — no specific check",
            detail="",
        )

    @staticmethod
    def _eval_runtime(
        name: str, criterion: AcceptanceCriterion,
        patch_text: str, runtime_check_result,
    ) -> AcceptanceEvidence:
        if name == "skeleton_error_on_connect":
            # Skeleton should raise TransportError on connect
            has_transport_error = bool(re.search(
                r'raise\s+TransportError',
                patch_text,
            ))
            has_not_implemented = bool(re.search(
                r'(?:skeleton|not\s+(?:yet\s+)?implemented)',
                patch_text, re.IGNORECASE,
            ))
            satisfied = has_transport_error or has_not_implemented
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=satisfied,
                evidence=f"TransportError raise: {has_transport_error}, not-implemented msg: {has_not_implemented}",
                detail="" if satisfied else "Skeleton should clearly raise TransportError on connect",
            )

        if name == "runtime_connect":
            if runtime_check_result is not None:
                satisfied = not runtime_check_result.connect_raises_error
                return AcceptanceEvidence(
                    criterion_name=name,
                    satisfied=satisfied,
                    evidence=f"connect_raises_error={runtime_check_result.connect_raises_error}",
                    detail="" if satisfied else "connect() raises an error — must establish real connection",
                )
            return AcceptanceEvidence(
                criterion_name=name, satisfied=False,
                evidence="no runtime check result available",
                detail="",
            )

        if name == "runtime_send_recv":
            if runtime_check_result is not None:
                satisfied = (
                    not runtime_check_result.send_raises_error
                    and not runtime_check_result.recv_raises_error
                )
                return AcceptanceEvidence(
                    criterion_name=name,
                    satisfied=satisfied,
                    evidence=f"send_raises_error={runtime_check_result.send_raises_error}, recv_raises_error={runtime_check_result.recv_raises_error}",
                    detail="" if satisfied else "send()/recv() raise errors — must transmit actual data",
                )
            return AcceptanceEvidence(
                criterion_name=name, satisfied=False,
                evidence="no runtime check result available",
                detail="",
            )

        return AcceptanceEvidence(
            criterion_name=name, satisfied=True,
            evidence=f"runtime criterion '{name}' — no specific check",
            detail="",
        )

    @staticmethod
    def _eval_unit_test(
        name: str, criterion: AcceptanceCriterion,
        patch_text: str, patch_paths: list[str],
    ) -> AcceptanceEvidence:
        test_paths = [p for p in patch_paths if p.startswith("tests/test_")]

        if name == "roundtrip_test":
            has_roundtrip = bool(re.search(
                r'(?:roundtrip|round_trip|RoundTrip|send_recv|SendRecv|'
                r'client_server|ClientServer|smoke|Smoke)',
                patch_text,
            ))
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=has_roundtrip,
                evidence=f"test paths: {test_paths}, roundtrip pattern found: {has_roundtrip}",
                detail="" if has_roundtrip else "No roundtrip test found",
            )

        if name == "regression_test":
            has_test_change = len(test_paths) > 0
            # Also check for test function definitions in the patch
            has_test_modification = any(
                line.startswith("+") and re.search(r'def\s+test_\w+', line)
                for line in patch_text.splitlines()
                if line.startswith("+")
            )
            satisfied = has_test_change or has_test_modification
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=satisfied,
                evidence=f"test files: {test_paths}, test modifications found: {has_test_modification}",
                detail="" if satisfied else "No regression test added or updated",
            )

        if name == "metric_tests":
            has_metric_test = bool(re.search(
                r'(?:metric|score|parse|extract|evaluate)',
                patch_text, re.IGNORECASE,
            ))
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=has_metric_test and len(test_paths) > 0,
                evidence=f"test paths: {test_paths}, metric content: {has_metric_test}",
                detail="" if (has_metric_test and test_paths) else "No metric tests found",
            )

        # Generic test check
        satisfied = len(test_paths) > 0
        return AcceptanceEvidence(
            criterion_name=name,
            satisfied=satisfied,
            evidence=f"test paths in patch: {test_paths}",
            detail="" if satisfied else "No test files in patch",
        )

    @staticmethod
    def _eval_config(
        name: str, criterion: AcceptanceCriterion,
        patch_text: str, patch_paths: list[str],
    ) -> AcceptanceEvidence:
        config_paths = [p for p in patch_paths if "config" in p or p.endswith(".yaml")]

        if name == "config_loads":
            has_config = len(config_paths) > 0
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=has_config,
                evidence=f"config paths: {config_paths}",
                detail="" if has_config else "No config files in patch",
            )

        if name == "default_config_load":
            has_default_config = any(
                p in ("config/client.yaml", "config/server.yaml") for p in patch_paths
            )
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=has_default_config,
                evidence=f"default config in patch: {has_default_config}",
                detail="" if has_default_config else "Default config not found in patch",
            )

        if name == "config_example_exists":
            example_paths = [p for p in patch_paths if p.startswith("config/examples/")]
            satisfied = len(example_paths) > 0
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=satisfied,
                evidence=f"config example paths: {example_paths}",
                detail="" if satisfied else "No config example file for transport",
            )

        satisfied = len(config_paths) > 0
        return AcceptanceEvidence(
            criterion_name=name,
            satisfied=satisfied,
            evidence=f"config paths: {config_paths}",
            detail="" if satisfied else "No config evidence in patch",
        )

    @staticmethod
    def _eval_cli(
        name: str, criterion: AcceptanceCriterion,
        patch_text: str, patch_paths: list[str],
    ) -> AcceptanceEvidence:
        script_paths = [p for p in patch_paths if p.startswith("scripts/")]

        if name == "help_works":
            has_argparse = "argparse" in patch_text.lower() or "--help" in patch_text
            satisfied = len(script_paths) > 0 or has_argparse
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=satisfied,
                evidence=f"script paths: {script_paths}, argparse/--help in patch: {has_argparse}",
                detail="" if satisfied else "No --help or argparse found in patch",
            )

        if name == "sample_io":
            has_main = "if __name__" in patch_text or "def main" in patch_text
            satisfied = len(script_paths) > 0 and has_main
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=satisfied,
                evidence=f"script paths: {script_paths}, main entry: {has_main}",
                detail="" if satisfied else "Script missing main entry point",
            )

        satisfied = len(script_paths) > 0
        return AcceptanceEvidence(
            criterion_name=name,
            satisfied=satisfied,
            evidence=f"script paths: {script_paths}",
            detail="" if satisfied else "No script files in patch",
        )

    @staticmethod
    def _eval_evaluation(
        name: str, criterion: AcceptanceCriterion,
        patch_text: str, patch_paths: list[str],
    ) -> AcceptanceEvidence:
        eval_paths = [p for p in patch_paths if "evaluation" in p or "fingerprint" in p]

        if name == "metric_in_output":
            has_report_fields = bool(re.search(
                r'(?:report|metric|score|risk|field)',
                patch_text, re.IGNORECASE,
            ))
            satisfied = len(eval_paths) > 0 or has_report_fields
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=satisfied,
                evidence=f"evaluation paths: {eval_paths}, report/metric content: {has_report_fields}",
                detail="" if satisfied else "No report/metric fields found",
            )

        if name == "before_after_evidence":
            has_comparison = bool(re.search(
                r'(?:before|after|comparison|compare|delta|diff|change)',
                patch_text, re.IGNORECASE,
            ))
            # This criterion is optional (required=False in contract)
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=has_comparison,
                evidence=f"before/after comparison found: {has_comparison}",
                detail="" if has_comparison else "No before/after evidence (optional)",
            )

        satisfied = len(eval_paths) > 0
        return AcceptanceEvidence(
            criterion_name=name,
            satisfied=satisfied,
            evidence=f"evaluation paths: {eval_paths}",
            detail="" if satisfied else "No evaluation files in patch",
        )

    @staticmethod
    def _eval_backward_compat(
        name: str, criterion: AcceptanceCriterion,
        patch_text: str, patch_paths: list[str],
    ) -> AcceptanceEvidence:
        if name == "no_source_changes":
            py_paths = [p for p in patch_paths if p.endswith(".py")]
            doc_only_paths = [p for p in patch_paths if p.endswith(".md") or p.startswith("docs/")]
            non_doc_changes = [p for p in py_paths if p not in doc_only_paths]
            satisfied = len(non_doc_changes) == 0
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=satisfied,
                evidence=f"python files modified: {non_doc_changes}",
                detail="" if satisfied else f"Source code was modified in docs-only task: {non_doc_changes}",
            )

        if name == "existing_tests_pass":
            # This is verified by the test runner, not patch inspection
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=True,  # Assume tests pass (verified separately)
                evidence="delegated to test runner",
                detail="",
            )

        return AcceptanceEvidence(
            criterion_name=name, satisfied=True,
            evidence="backward compat — delegated",
            detail="",
        )

    @staticmethod
    def _eval_security_boundary(
        name: str, criterion: AcceptanceCriterion,
        patch_text: str, runtime_check_result,
    ) -> AcceptanceEvidence:
        if name == "default_switch_forbidden":
            config_modified = bool(re.search(
                r'(?:config/client\.yaml|config/server\.yaml).*?(?:default|type)',
                patch_text,
            ))
            satisfied = not config_modified
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=satisfied,
                evidence=f"default config modified: {config_modified}",
                detail="" if satisfied else "Skeleton transport must not be set as default",
            )

        if name == "runtime_gate_for_default":
            if runtime_check_result is not None:
                satisfied = (
                    not runtime_check_result.is_skeleton
                    and runtime_check_result.has_roundtrip_test
                )
                return AcceptanceEvidence(
                    criterion_name=name,
                    satisfied=satisfied,
                    evidence=f"is_skeleton={runtime_check_result.is_skeleton}, has_roundtrip_test={runtime_check_result.has_roundtrip_test}",
                    detail="" if satisfied else "Cannot switch default to skeleton transport",
                )
            return AcceptanceEvidence(
                criterion_name=name, satisfied=False,
                evidence="no runtime check result",
                detail="",
            )

        return AcceptanceEvidence(
            criterion_name=name, satisfied=True,
            evidence="security boundary — no specific check",
            detail="",
        )

    @staticmethod
    def _eval_docs(
        name: str, criterion: AcceptanceCriterion,
        patch_text: str, patch_paths: list[str],
    ) -> AcceptanceEvidence:
        doc_paths = [p for p in patch_paths if p.endswith(".md") or p.startswith("docs/")]

        if name == "docs_updated":
            satisfied = len(doc_paths) > 0
            return AcceptanceEvidence(
                criterion_name=name,
                satisfied=satisfied,
                evidence=f"doc paths in patch: {doc_paths}",
                detail="" if satisfied else "No transport documentation updated in patch",
            )

        satisfied = len(doc_paths) > 0
        return AcceptanceEvidence(
            criterion_name=name,
            satisfied=satisfied,
            evidence=f"doc paths: {doc_paths}",
            detail="" if satisfied else "No documentation files in patch",
        )

    # ------------------------------------------------------------------
    # Forbidden degradation checks
    # ------------------------------------------------------------------

    def _check_forbidden_degradations(
        self,
        result: UserIntentValidationResult,
        patch_text: str,
        patch_paths: list[str],
        contract: IntentContract,
    ) -> None:
        """Check each forbidden degradation against the patch."""
        for degradation in contract.forbidden_degradations:
            if "SILENT DOWNGRADE" in degradation:
                self._check_silent_downgrade(result, patch_text, contract)
            elif "DEFAULT SWITCH TO SKELETON" in degradation:
                self._check_default_switch_to_skeleton(result, patch_text, contract)
            elif "BEHAVIOR CHANGE" in degradation:
                self._check_behavior_change(result, patch_paths, contract)
            elif "SOURCE CODE CHANGE" in degradation:
                self._check_source_code_change(result, patch_paths)
            elif "NO REGRESSION TEST" in degradation:
                self._check_regression_test(result, patch_text, patch_paths)

    @staticmethod
    def _check_silent_downgrade(
        result: UserIntentValidationResult,
        patch_text: str,
        contract: IntentContract,
    ) -> None:
        """Check if runtime request was silently downgraded to skeleton."""
        has_skeleton_pattern = bool(re.search(
            r'(?:skeleton|not\s+(?:yet\s+)?implemented|not\s+fully\s+implemented)',
            patch_text, re.IGNORECASE,
        ))
        # Also check for NotImplementedError
        has_not_implemented = "NotImplementedError" in patch_text or "raise TransportError" in patch_text

        if has_skeleton_pattern or has_not_implemented:
            if contract.runtime_required:
                result.was_downgraded = True
                result.downgrade_allowed = contract.allow_stub
                result.downgrade_detail = (
                    "Patch contains skeleton patterns but runtime behavior was required"
                )
                if not contract.allow_stub:
                    result.errors.append(
                        "SILENT DOWNGRADE: Runtime was required but patch contains "
                        "skeleton/not-implemented patterns. User intent NOT satisfied."
                    )

    @staticmethod
    def _check_default_switch_to_skeleton(
        result: UserIntentValidationResult,
        patch_text: str,
        contract: IntentContract,
    ) -> None:
        """Block default switch to skeleton transport."""
        if not contract.requires_default_change:
            return

        config_changed = bool(re.search(
            r'(?:config/client\.yaml|config/server\.yaml|default.*transport)',
            patch_text,
        ))
        has_skeleton = bool(re.search(
            r'(?:skeleton|not\s+(?:yet\s+)?implemented)',
            patch_text, re.IGNORECASE,
        ))

        if config_changed and has_skeleton:
            result.errors.append(
                "DEFAULT SWITCH BLOCKED: Cannot set skeleton transport as default. "
                "Transport must be runtime-usable before switching defaults."
            )

    @staticmethod
    def _check_behavior_change(
        result: UserIntentValidationResult,
        patch_paths: list[str],
        contract: IntentContract,
    ) -> None:
        """Check if behavior was changed when it shouldn't be."""
        if not contract.requires_no_behavior_change:
            return

        # For refactor/docs-only/config-only, significant source changes are suspect
        source_changes = [
            p for p in patch_paths
            if p.endswith(".py") and not p.startswith("tests/") and not p.startswith("docs/")
        ]
        if source_changes and contract.implementation_level == "docs_only":
            result.warnings.append(
                f"Docs-only task modified source files: {source_changes}"
            )

    @staticmethod
    def _check_source_code_change(
        result: UserIntentValidationResult,
        patch_paths: list[str],
    ) -> None:
        """Docs-only task should not modify .py files."""
        py_changes = [p for p in patch_paths if p.endswith(".py")]
        if py_changes:
            result.errors.append(
                f"SOURCE CODE CHANGE in docs-only task: {py_changes}. "
                "Only documentation files should be modified."
            )

    @staticmethod
    def _check_regression_test(
        result: UserIntentValidationResult,
        patch_text: str,
        patch_paths: list[str],
    ) -> None:
        """Bugfix must include a regression test."""
        test_changes = [p for p in patch_paths if p.startswith("tests/")]
        has_new_test = bool(re.search(
            r'^\+.*def\s+test_',
            patch_text, re.MULTILINE,
        ))
        if not test_changes and not has_new_test:
            result.errors.append(
                "NO REGRESSION TEST: Bugfix must include a test that verifies the fix."
            )

    # ------------------------------------------------------------------
    # Downgrade detection
    # ------------------------------------------------------------------

    @staticmethod
    def _check_downgrade(
        result: UserIntentValidationResult,
        contract: IntentContract,
        runtime_check_result,
    ) -> None:
        """Detect if the generated patch was downgraded from what was requested."""
        if contract.runtime_required and runtime_check_result is not None:
            if runtime_check_result.is_skeleton:
                result.was_downgraded = True
                result.downgrade_allowed = contract.allow_stub
                result.downgrade_detail = (
                    "Runtime transport was requested but skeleton was generated. "
                    "User intent NOT satisfied."
                )
                if not contract.allow_stub:
                    result.errors.append(
                        f"DOWNGRADE DETECTED: Request required runtime ({contract.implementation_level}) "
                        f"but generated patch is skeleton-only."
                    )

        if contract.requires_default_change and runtime_check_result is not None:
            if runtime_check_result.is_skeleton:
                result.was_downgraded = True
                result.downgrade_allowed = False
                result.downgrade_detail = (
                    "Default switch was requested but target is skeleton-only. "
                    "Cannot switch default to skeleton transport."
                )

    # ------------------------------------------------------------------
    # Bypass file detection
    # ------------------------------------------------------------------

    def _check_bypass_files(
        self, patch_paths: list[str], contract: IntentContract,
    ) -> list[str]:
        """Detect bypass files like socks5_full_transport.py.

        Returns a list of bypass file paths found in the patch.
        """
        if not contract.target_transport:
            return []
        bypass = []
        for pp in patch_paths:
            for pat in self._BYPASS_FILE_PATTERNS:
                if pat.match(pp):
                    bypass.append(pp)
                    break
        return bypass

    # ------------------------------------------------------------------
    # Tunnel smoke check
    # ------------------------------------------------------------------

    def _check_tunnel_smoke(
        self,
        result: UserIntentValidationResult,
        contract: IntentContract,
        tunnel_smoke_result,
    ) -> None:
        """Evaluate tunnel smoke evidence and add it to the validation result.

        Tunnel smoke is MANDATORY for:
        - Runtime/end-to-end contracts
        - Transport/shaping/core/config tasks
        """
        from src.llm.tunnel_smoke_validator import task_needs_tunnel_smoke

        needs_smoke = task_needs_tunnel_smoke(None, contract)

        if tunnel_smoke_result.mock_tun_smoke is not None:
            mock = tunnel_smoke_result.mock_tun_smoke
            if mock.success:
                result.evidence.append(AcceptanceEvidence(
                    criterion_name="tunnel_smoke_mock_tun",
                    satisfied=True,
                    evidence=f"Mock-TUN tunnel smoke passed with transport={mock.transport}",
                    detail=f"duration={mock.duration_sec}s, log_dir={mock.log_dir}",
                ))
            else:
                result.evidence.append(AcceptanceEvidence(
                    criterion_name="tunnel_smoke_mock_tun",
                    satisfied=False,
                    evidence=f"Mock-TUN tunnel smoke FAILED: {mock.error}",
                    detail=f"transport={mock.transport}",
                ))
                if needs_smoke:
                    result.errors.append(
                        f"Tunnel smoke failed: {mock.error}"
                    )
                    if contract.runtime_required or contract.end_to_end_required:
                        result.user_intent_status = "failed"
                        result.unmet_acceptance_criteria.append("tunnel_smoke_mock_tun")
        else:
            if needs_smoke:
                result.warnings.append(
                    "Tunnel smoke was not run for a task that requires it"
                )
                result.unmet_acceptance_criteria.append("tunnel_smoke_mock_tun")

        # Phase 9 real-TUN/netns
        if tunnel_smoke_result.phase9_passed:
            result.evidence.append(AcceptanceEvidence(
                criterion_name="tunnel_smoke_phase9",
                satisfied=True,
                evidence="Phase 9 real-TUN/netns trace passed",
            ))
        elif not tunnel_smoke_result.phase9_skipped and tunnel_smoke_result.phase9_smoke is not None:
            # Phase 9 was attempted but failed
            err = tunnel_smoke_result.phase9_smoke.get("error", "unknown")
            result.evidence.append(AcceptanceEvidence(
                criterion_name="tunnel_smoke_phase9",
                satisfied=False,
                evidence=f"Phase 9 real-TUN/netns trace FAILED: {err}",
            ))
            if needs_smoke and contract.runtime_required:
                result.errors.append(f"Phase 9 trace failed: {err}")
        elif tunnel_smoke_result.phase9_skipped:
            reason = tunnel_smoke_result.phase9_skip_reason
            result.evidence.append(AcceptanceEvidence(
                criterion_name="tunnel_smoke_phase9",
                satisfied=True,  # skipped is not a failure
                evidence=f"Phase 9 skipped: {reason[:200]}",
                detail="real_netns not available or not required",
            ))

    # ------------------------------------------------------------------
    # Final status determination
    # ------------------------------------------------------------------

    def _determine_final_status(
        self,
        result: UserIntentValidationResult,
        contract: IntentContract | None = None,
        tunnel_smoke_result=None,
    ) -> str:
        """Determine the final task status from all layers + tunnel smoke.

        Enforces the End-to-End Intent Fulfillment Gate:
        - If end_to_end_required=true, completed_with_warnings is NOT allowed.
        - Any required criterion failure → intent_not_satisfied.
        - Tunnel smoke failure for runtime tasks → intent_not_satisfied.
        """
        if result.errors and any(
            "patch generation failed" in e.lower() or "no patch text" in e.lower()
            for e in result.errors
        ):
            return "patch_generation_failed"

        if result.patch_integrity_status == "failed":
            return "validation_failed"

        if result.functional_validation_status == "failed":
            return "validation_failed"

        if result.user_intent_status == "failed":
            return "intent_not_satisfied"

        # Tunnel smoke gate: mock-tun smoke failure → intent_not_satisfied for runtime
        if tunnel_smoke_result is not None and tunnel_smoke_result.mock_tun_smoke is not None:
            if not tunnel_smoke_result.mock_tun_smoke.success:
                if contract is not None and (contract.runtime_required or contract.end_to_end_required):
                    return "intent_not_satisfied"

        # End-to-end gate: partial is NOT acceptable when end_to_end_required
        if result.user_intent_status == "partial":
            if contract is not None and contract.end_to_end_required:
                return "intent_not_satisfied"
            return "completed_with_warnings"

        # End-to-end gate: warnings are NOT acceptable when must_pass_without_warnings
        if result.warnings:
            if contract is not None and contract.must_pass_without_warnings:
                return "intent_not_satisfied"
            return "completed_with_warnings"

        return "completed"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _extract_transport_from_patch(patch_text: str) -> str | None:
    """Extract a transport name from patch file paths.

    Prefers canonical paths (e.g. socks5_transport.py) over bypass paths
    (e.g. socks5_full_transport.py).
    """
    # Try canonical path first (single word before _transport.py)
    m = re.search(r'src/transport/(\w+)_transport\.py', patch_text)
    if not m:
        return None
    name = m.group(1)
    # If the match is a bypass variant (e.g. socks5_full), try to extract
    # the real transport name by stripping known bypass suffixes
    for suffix in ("_full", "_runtime", "_new", "_v2", "_real"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


_BYPASS_PATTERNS = [
    re.compile(r'src/transport/(\w+)_full_transport\.py'),
    re.compile(r'src/transport/(\w+)_runtime_transport\.py'),
    re.compile(r'src/transport/(\w+)_new_transport\.py'),
    re.compile(r'src/transport/(\w+)_v2_transport\.py'),
    re.compile(r'src/transport/(\w+)_real_transport\.py'),
]


def _detect_bypass_files(patch_paths: list[str], transport: str | None) -> list[str]:
    """Detect bypass files like socks5_full_transport.py in patch paths.

    A bypass file is a transport implementation at a non-canonical path that
    suggests the LLM created a parallel file instead of wiring through the
    expected registration path.
    """
    if not transport or not patch_paths:
        return []
    bypass = []
    for pp in patch_paths:
        for pat in _BYPASS_PATTERNS:
            m = pat.match(pp)
            if not m:
                continue
            # Even if the name matches the target, a *_full_transport.py
            # is still a bypass — the canonical path is {name}_transport.py
            canonical = f"src/transport/{transport}_transport.py"
            if pp != canonical:
                bypass.append(pp)
            break
    return bypass
