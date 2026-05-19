"""FileRetriever — multi-strategy file recall from RepoIndex.

Given a user request, a TaskPlan, and a RepoIndex, retrieves candidate files
using multiple strategies:

1. Path/filename keyword matching
2. Python symbol matching
3. Config key matching
4. Task-type rule-based recall
5. Test file mapping
6. Doc file mapping

LLM candidate_files from the planner are treated as **hints only** and merged
into the retrieval — they never become the final file list on their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.llm.repo_indexer import RepoIndex, FileInfo


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CandidateFile:
    """A file retrieved by the multi-strategy retriever."""

    path: str
    score: float
    reasons: list[str]
    sources: list[str]  # strategy names, e.g. "keyword", "symbol", "task_type"
    action: str  # edit, review, test, doc

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "score": self.score,
            "reasons": self.reasons,
            "sources": self.sources,
            "action": self.action,
        }


# ---------------------------------------------------------------------------
# Task type → affected areas mapping
# ---------------------------------------------------------------------------

_TASK_TYPE_RULES: dict[str, dict[str, list[str]]] = {
    "transport": {
        "patterns": ["src/transport/"],
        "keywords": ["transport", "tcp", "tls", "websocket", "ssh", "mock"],
        "test_patterns": ["test_transport", "test_tcp", "test_tls", "test_websocket", "test_ssh"],
        "doc_patterns": ["docs/", "README.md"],
        "config_patterns": ["config/", "src/common/config.py"],
        "script_patterns": ["scripts/smoke_replacement_matrix.py"],
    },
    "core": {
        "patterns": ["src/core/", "src/common/frame.py", "src/common/session.py"],
        "keywords": ["core", "frame", "session", "forwarding", "nat", "route"],
        "test_patterns": ["test_core.py", "test_frame.py", "test_session_id_config.py"],
        "doc_patterns": ["docs/", "README.md"],
        "config_patterns": ["config/", "src/common/config.py"],
        "script_patterns": [],
    },
    "tun": {
        "patterns": ["src/tun/"],
        "keywords": ["tun", "tun_device", "linux_tun", "mock_tun"],
        "test_patterns": ["test_tun_device.py"],
        "doc_patterns": ["docs/", "README.md"],
        "config_patterns": ["config/"],
        "script_patterns": [],
    },
    "config": {
        "patterns": ["config/", "src/common/config.py"],
        "keywords": ["config", "yaml", "configuration"],
        "test_patterns": ["test_config.py"],
        "doc_patterns": ["docs/", "README.md"],
        "config_patterns": ["config/"],
        "script_patterns": [],
    },
    "llm_agent": {
        "patterns": ["src/llm/", "scripts/llm_task.py"],
        "keywords": ["llm", "agent", "planner", "patch", "task", "validation"],
        "test_patterns": ["test_llm_", "test_task_", "test_patch_", "test_safety_",
                          "test_validation_", "test_report_", "test_commit_",
                          "test_code_task_", "test_replacement_"],
        "doc_patterns": ["docs/", "README.md"],
        "config_patterns": ["config/llm_agent.yaml", "config/llm_agent.yaml.example"],
        "script_patterns": ["scripts/llm_task.py", "scripts/validate_llm_task.sh"],
    },
    "validation": {
        "patterns": ["src/llm/replacement_validator.py", "src/llm/validation_runner.py",
                     "scripts/smoke_replacement_matrix.py"],
        "keywords": ["validation", "smoke", "replacement", "gate"],
        "test_patterns": ["test_validation", "test_smoke", "test_replacement"],
        "doc_patterns": ["docs/", "README.md"],
        "config_patterns": [],
        "script_patterns": ["scripts/"],
    },
    "test": {
        "patterns": ["tests/"],
        "keywords": ["test", "pytest", "unit test"],
        "test_patterns": ["tests/"],
        "doc_patterns": [],
        "config_patterns": [],
        "script_patterns": [],
    },
    "docs": {
        "patterns": ["docs/", "README.md"],
        "keywords": ["doc", "readme", "documentation", "文档"],
        "test_patterns": [],
        "doc_patterns": ["docs/", "README.md"],
        "config_patterns": [],
        "script_patterns": [],
    },
    "security": {
        "patterns": ["src/llm/safety_guard.py"],
        "keywords": ["security", "safety", "block", "secret"],
        "test_patterns": ["test_safety_guard.py"],
        "doc_patterns": ["docs/"],
        "config_patterns": [],
        "script_patterns": [],
    },
    "fingerprint_evaluation": {
        "patterns": [
            "src/evaluation/fingerprint/",
            "scripts/trace_capture.py",
            "scripts/run_trace_scenarios.py",
            "scripts/summarize_fingerprint_reports.py",
        ],
        "keywords": ["fingerprint", "trace", "pcap", "burst", "ngram", "detection",
                     "small_packet", "repeated_length", "risk_score"],
        "test_patterns": ["test_fingerprint_", "test_trace_", "test_summarize_"],
        "doc_patterns": ["docs/fingerprint_", "docs/trace_capture.md"],
        "config_patterns": [],
        "script_patterns": ["scripts/trace_capture.py", "scripts/summarize_fingerprint_reports.py"],
    },
    "llm_detection": {
        "patterns": [
            "src/llm/detection/",
            "src/llm/",
            "scripts/llm_task.py",
        ],
        "keywords": ["detection", "gate", "countermeasure", "adversarial",
                     "patch_loop", "prompt_builder", "detector_report",
                     "fingerprint_mitigation", "probe_resistance"],
        "test_patterns": ["test_llm_detection_", "test_llm_countermeasure_"],
        "doc_patterns": ["docs/llm_detection_"],
        "config_patterns": [],
        "script_patterns": ["scripts/llm_task.py"],
    },
    "traffic_shaping": {
        "patterns": ["src/shaping/"],
        "keywords": ["shaping", "shaper", "padding", "pacing", "jitter",
                     "fragmentation", "scheduling", "countermeasure"],
        "test_patterns": ["test_shaping", "test_shaper"],
        "doc_patterns": ["docs/shaping", "docs/shaper"],
        "config_patterns": [],
        "script_patterns": [],
    },
    "ci": {
        "patterns": [],
        "keywords": ["ci", "github", "workflow", "action"],
        "test_patterns": [],
        "doc_patterns": [],
        "config_patterns": [],
        "script_patterns": [],
    },
    "mixed_feature": {
        "patterns": ["src/", "tests/"],
        "keywords": [],
        "test_patterns": ["tests/"],
        "doc_patterns": ["docs/", "README.md"],
        "config_patterns": ["config/"],
        "script_patterns": [],
    },
}


# Mapping from task_type to affected area keys
_TASK_TO_AREA: dict[str, list[str]] = {
    "transport_change": ["transport", "config", "test", "docs"],
    "transport_addition": ["transport", "config", "test", "docs", "transport_addition"],
    "core_change": ["core", "test", "docs"],
    "config_change": ["config", "test", "docs"],
    "test_addition": ["test"],
    "docs_update": ["docs"],
    "feature_addition": ["transport", "config", "test", "docs", "transport_addition"],
    "mixed_feature_change": ["transport", "config", "test", "docs", "core", "llm_agent"],
    "bugfix": ["transport", "core", "tun", "config", "llm_agent", "test", "docs"],
    "refactor": ["transport", "core", "tun", "config", "test", "docs"],
    "fingerprint_mitigation": ["fingerprint_evaluation", "llm_detection", "traffic_shaping", "test", "docs"],
    "traffic_shaping": ["traffic_shaping", "transport", "test", "docs"],
    "llm_detection": ["llm_detection", "fingerprint_evaluation", "test", "docs"],
    "probe_resistance": ["llm_detection", "test", "docs"],
    "rtt_evaluation": ["fingerprint_evaluation", "llm_detection", "test", "docs"],
    "unknown": ["mixed_feature"],
}


# Score weights per strategy
_SCORE_KEYWORD = 0.9
_SCORE_SYMBOL = 0.85
_SCORE_CONFIG = 0.8
_SCORE_TASK_TYPE = 0.7
_SCORE_HINT = 0.6
_SCORE_TEST_MAP = 0.5
_SCORE_DOC_MAP = 0.4


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------

class FileRetriever:
    """Multi-strategy file retriever using RepoIndex + TaskPlan + request text.

    Usage:
        retriever = FileRetriever(repo_index)
        candidates = retriever.retrieve(request="add http2 transport",
                                        plan=task_plan,
                                        affected_areas=["transport", "mixed_feature"])
        for c in candidates:
            print(f"{c.path} score={c.score} action={c.action}")
    """

    def __init__(self, repo_index: RepoIndex):
        self._index = repo_index

    def retrieve(
        self,
        request: str,
        plan=None,
        affected_areas: list[str] | None = None,
    ) -> list[CandidateFile]:
        """Run all retrieval strategies and return deduplicated, scored candidates.

        Args:
            request: Original user request string (for keyword extraction).
            plan: TaskPlan or LLMTaskPlan instance.
            affected_areas: Override for affected area keys (derived from plan if None).

        Returns:
            List of CandidateFile sorted by descending score.
        """
        request_lower = request.lower()

        # Determine affected area keys
        if affected_areas is not None:
            area_keys = affected_areas
        elif plan is not None:
            area_keys = self._plan_to_area_keys(plan)
        else:
            # Infer from request text
            area_keys = self._infer_areas_from_request(request_lower)

        seen: dict[str, CandidateFile] = {}

        # Strategy 1: keyword matching
        for c in self._retrieve_by_keywords(request_lower, area_keys):
            self._merge(seen, c)

        # Strategy 2: symbol matching
        for c in self._retrieve_by_symbols(request_lower, area_keys):
            self._merge(seen, c)

        # Strategy 3: config key matching
        for c in self._retrieve_by_config_keys(request_lower):
            self._merge(seen, c)

        # Strategy 4: task-type rule recall
        for c in self._retrieve_by_task_type(area_keys):
            self._merge(seen, c)

        # Strategy 5: test file mapping
        for c in self._retrieve_test_files(area_keys):
            self._merge(seen, c)

        # Strategy 6: doc file mapping
        for c in self._retrieve_doc_files(area_keys):
            self._merge(seen, c)

        # Merge LLM hints — higher score if the file actually exists on disk
        if plan is not None:
            hints = getattr(plan, "candidate_files", []) or []
            for hint in hints:
                exists_on_disk = hint in self._index.files
                if exists_on_disk:
                    fi = self._index.files[hint]
                    self._merge(seen, CandidateFile(
                        path=hint,
                        score=_SCORE_HINT,
                        reasons=[f"LLM planner hint (exists on disk): {hint}"],
                        sources=["planner_hint"],
                        action="edit" if not fi.is_test and not fi.is_doc else self._infer_action(fi),
                    ))
                else:
                    # Track as a hint that didn't match — will be rejected later
                    self._merge(seen, CandidateFile(
                        path=hint,
                        score=_SCORE_HINT - 0.2,
                        reasons=[f"LLM planner hint (NOT on disk): {hint}"],
                        sources=["planner_hint"],
                        action="review",
                    ))

        # Sort by score descending
        result = sorted(seen.values(), key=lambda c: c.score, reverse=True)
        return result

    # ------------------------------------------------------------------
    # Strategy 1: keyword matching
    # ------------------------------------------------------------------

    def _retrieve_by_keywords(self, request_lower: str, area_keys: list[str]) -> list[CandidateFile]:
        candidates: list[CandidateFile] = []
        keywords = self._collect_keywords(area_keys)

        for path, fi in self._index.files.items():
            path_lower = path.lower()
            matched = []
            for kw in keywords:
                if kw in path_lower:
                    matched.append(kw)
            if matched:
                candidates.append(CandidateFile(
                    path=path,
                    score=_SCORE_KEYWORD + 0.01 * len(matched),
                    reasons=[f"Path matches keyword(s): {', '.join(matched)}"],
                    sources=["keyword"],
                    action=self._infer_action(fi),
                ))
        return candidates

    # ------------------------------------------------------------------
    # Strategy 2: symbol matching
    # ------------------------------------------------------------------

    def _retrieve_by_symbols(self, request_lower: str, area_keys: list[str]) -> list[CandidateFile]:
        candidates: list[CandidateFile] = []
        keywords = self._collect_keywords(area_keys)

        for path, fi in self._index.files.items():
            if fi.file_type != "python":
                continue
            matched_syms = []
            for sym in fi.symbols:
                sym_lower = sym.lower()
                for kw in keywords:
                    if kw in sym_lower:
                        matched_syms.append(sym)
                        break
            matched_imps = []
            for imp in fi.imports:
                imp_lower = imp.lower()
                for kw in keywords:
                    if kw in imp_lower:
                        matched_imps.append(imp)
                        break
            if matched_syms or matched_imps:
                reasons = []
                if matched_syms:
                    reasons.append(f"Symbols: {', '.join(matched_syms[:5])}")
                if matched_imps:
                    reasons.append(f"Imports: {', '.join(matched_imps[:5])}")
                candidates.append(CandidateFile(
                    path=path,
                    score=_SCORE_SYMBOL,
                    reasons=reasons,
                    sources=["symbol"],
                    action=self._infer_action(fi),
                ))
        return candidates

    # ------------------------------------------------------------------
    # Strategy 3: config key matching
    # ------------------------------------------------------------------

    def _retrieve_by_config_keys(self, request_lower: str) -> list[CandidateFile]:
        candidates: list[CandidateFile] = []
        for path, fi in self._index.files.items():
            if not fi.config_keys:
                continue
            matched = []
            for ck in fi.config_keys:
                if ck.lower() in request_lower:
                    matched.append(ck)
            if matched:
                candidates.append(CandidateFile(
                    path=path,
                    score=_SCORE_CONFIG,
                    reasons=[f"Config keys: {', '.join(matched)}"],
                    sources=["config_key"],
                    action="edit",
                ))
        return candidates

    # ------------------------------------------------------------------
    # Strategy 4: task-type rule recall
    # ------------------------------------------------------------------

    def _retrieve_by_task_type(self, area_keys: list[str]) -> list[CandidateFile]:
        candidates: list[CandidateFile] = []
        collected_patterns: set[str] = set()

        for area_key in area_keys:
            rules = _TASK_TYPE_RULES.get(area_key, {})
            for pat in rules.get("patterns", []):
                collected_patterns.add(pat)

        for path, fi in self._index.files.items():
            matched = []
            for pat in collected_patterns:
                if path.startswith(pat) or pat in path:
                    matched.append(pat)
            if matched:
                candidates.append(CandidateFile(
                    path=path,
                    score=_SCORE_TASK_TYPE,
                    reasons=[f"Task-type rule: matches {', '.join(matched[:3])}"],
                    sources=["task_type"],
                    action=self._infer_action(fi),
                ))
        return candidates

    # ------------------------------------------------------------------
    # Strategy 5: test file mapping
    # ------------------------------------------------------------------

    def _retrieve_test_files(self, area_keys: list[str]) -> list[CandidateFile]:
        candidates: list[CandidateFile] = []
        collected_test_patterns: set[str] = set()

        for area_key in area_keys:
            rules = _TASK_TYPE_RULES.get(area_key, {})
            for tp in rules.get("test_patterns", []):
                collected_test_patterns.add(tp)

        for path, fi in self._index.files.items():
            if not fi.is_test:
                continue
            matched = []
            for tp in collected_test_patterns:
                if tp in path:
                    matched.append(tp)
            if matched:
                candidates.append(CandidateFile(
                    path=path,
                    score=_SCORE_TEST_MAP,
                    reasons=[f"Test file for: {', '.join(matched)}"],
                    sources=["test_map"],
                    action="test",
                ))
        # Broader: any test file matching area_key patterns
        if not candidates:
            for path, fi in self._index.files.items():
                if not fi.is_test:
                    continue
                for area_key in area_keys:
                    if area_key in path.lower():
                        candidates.append(CandidateFile(
                            path=path,
                            score=_SCORE_TEST_MAP,
                            reasons=[f"Test file matches area: {area_key}"],
                            sources=["test_map"],
                            action="test",
                        ))
                        break
        return candidates

    # ------------------------------------------------------------------
    # Strategy 6: doc file mapping
    # ------------------------------------------------------------------

    def _retrieve_doc_files(self, area_keys: list[str]) -> list[CandidateFile]:
        candidates: list[CandidateFile] = []
        collected_doc_patterns: set[str] = set()

        for area_key in area_keys:
            rules = _TASK_TYPE_RULES.get(area_key, {})
            for dp in rules.get("doc_patterns", []):
                collected_doc_patterns.add(dp)

        for path, fi in self._index.files.items():
            if not fi.is_doc and path != "README.md":
                continue
            matched = []
            for dp in collected_doc_patterns:
                if path.startswith(dp) or dp in path:
                    matched.append(dp)
            if matched:
                candidates.append(CandidateFile(
                    path=path,
                    score=_SCORE_DOC_MAP,
                    reasons=[f"Doc file for: {', '.join(matched)}"],
                    sources=["doc_map"],
                    action="doc",
                ))
        return candidates

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _merge(seen: dict[str, CandidateFile], candidate: CandidateFile) -> None:
        """Merge a candidate into the seen dict, taking the higher score."""
        if candidate.path in seen:
            existing = seen[candidate.path]
            existing.reasons.extend(candidate.reasons)
            existing.sources.extend(candidate.sources)
            existing.score = max(existing.score, candidate.score)
            # Prefer edit over review over test over doc
            action_rank = {"edit": 4, "review": 3, "test": 2, "doc": 1}
            if action_rank.get(candidate.action, 0) > action_rank.get(existing.action, 0):
                existing.action = candidate.action
        else:
            seen[candidate.path] = candidate

    @staticmethod
    def _plan_to_area_keys(plan) -> list[str]:
        """Map a TaskPlan or LLMTaskPlan to affected area keys."""
        task_type = getattr(plan, "task_type", "unknown")
        return _TASK_TO_AREA.get(task_type, ["mixed_feature"])

    @staticmethod
    def _infer_areas_from_request(request_lower: str) -> list[str]:
        """Infer affected area keys from request text."""
        areas = []
        area_keywords = {
            "transport": ["transport", "tcp", "tls", "websocket", "ssh", "mock"],
            "core": ["core", "内核", "session", "frame", "forwarding", "tun", "nat"],
            "config": ["config", "配置", "setting"],
            "test": ["test", "测试"],
            "docs": ["doc", "readme", "文档", "documentation"],
            "llm_agent": ["llm", "agent", "planner", "patch", "task"],
            "validation": ["validation", "smoke", "replacement", "gate"],
            "security": ["security", "safety", "secret"],
            "fingerprint_evaluation": ["fingerprint", "trace", "pcap", "burst", "ngram",
                                        "small_packet", "repeated_length", "risk_score"],
            "llm_detection": ["detection", "gate", "countermeasure", "adversarial",
                            "patch_loop", "prompt_builder", "detector_report",
                            "fingerprint_mitigation", "probe_resistance"],
            "traffic_shaping": ["shaping", "shaper", "padding", "pacing", "jitter",
                              "fragmentation", "scheduling"],
        }
        for area, kws in area_keywords.items():
            if any(kw in request_lower for kw in kws):
                areas.append(area)
        if not areas:
            areas.append("mixed_feature")
        return areas

    @staticmethod
    def _collect_keywords(area_keys: list[str]) -> list[str]:
        """Collect keywords from all active area rules."""
        kws: list[str] = []
        for area_key in area_keys:
            rules = _TASK_TYPE_RULES.get(area_key, {})
            kws.extend(rules.get("keywords", []))
        return kws

    @staticmethod
    def _infer_action(fi: FileInfo) -> str:
        if fi.is_test:
            return "test"
        if fi.is_doc:
            return "doc"
        return "edit"
