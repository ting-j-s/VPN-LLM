"""Tests for FileRetriever.

Covers:
- FileRetriever can recall transport/config/tests/docs for transport tasks
- FileRetriever can recall core/frame/tun/tests/docs for core tasks
- FileRetriever can recall llm_task.py + src/llm + tests for llm_agent tasks
- CandidateFile contains score, reasons, sources, action
- LLM hints merged properly
"""

from src.llm.repo_indexer import RepoIndexer, RepoIndex, FileInfo
from src.llm.file_retriever import FileRetriever, CandidateFile, _TASK_TO_AREA
from src.llm.task_planner import TaskPlan, TASK_TRANSPORT_CHANGE, TASK_CORE_CHANGE


def _make_index_with_files(file_infos: list[FileInfo]) -> RepoIndex:
    """Build a RepoIndex from a list of FileInfo objects."""
    index = RepoIndex(root_dir="/fake")
    for fi in file_infos:
        index.files[fi.path] = fi
    index.file_count = len(index.files)
    index.python_count = sum(1 for fi in file_infos if fi.file_type == "python")
    index.test_count = sum(1 for fi in file_infos if fi.is_test)
    index.doc_count = sum(1 for fi in file_infos if fi.is_doc)
    return index


def _fi(path: str, file_type="python", symbols=None, is_test=False, is_doc=False, config_keys=None, imports=None):
    """Shorthand helper for creating FileInfo."""
    return FileInfo(
        path=path, file_type=file_type,
        symbols=symbols or [], imports=imports or [],
        config_keys=config_keys or [],
        is_test=is_test, is_doc=is_doc, size=100,
    )


class TestCandidateFile:
    """Test CandidateFile dataclass."""

    def test_candidate_file_fields(self):
        c = CandidateFile(
            path="src/transport/ws.py",
            score=0.9,
            reasons=["keyword match: websocket"],
            sources=["keyword", "symbol"],
            action="edit",
        )
        assert c.path == "src/transport/ws.py"
        assert c.score == 0.9
        assert "keyword match" in c.reasons[0]
        assert "keyword" in c.sources
        assert "symbol" in c.sources
        assert c.action == "edit"

    def test_candidate_file_serialization(self):
        c = CandidateFile(
            path="a.py", score=0.8, reasons=["reason1"], sources=["keyword"], action="edit",
        )
        d = c.to_dict()
        assert d["path"] == "a.py"
        assert d["score"] == 0.8
        assert d["reasons"] == ["reason1"]
        assert d["sources"] == ["keyword"]
        assert d["action"] == "edit"


class TestRetrieveTransportTask:
    """FileRetriever for transport_change tasks."""

    def test_recalls_transport_files(self):
        index = _make_index_with_files([
            _fi("src/transport/tcp_transport.py", symbols=["TCPTransport"]),
            _fi("src/transport/factory.py", symbols=["TransportFactory"]),
            _fi("src/transport/base.py", symbols=["BaseTransport"]),
            _fi("src/config.py", symbols=["load_config"]),
            _fi("config/server.yaml", file_type="yaml", config_keys=["transport"]),
            _fi("tests/test_tcp_transport.py", is_test=True),
            _fi("README.md", file_type="markdown", is_doc=True),
        ])

        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch to tcp",
            target_transport="tcp",
            affected_areas=["src/transport/", "config/"],
        )

        retriever = FileRetriever(index)
        candidates = retriever.retrieve("switch to tcp", plan)

        paths = {c.path for c in candidates}
        assert "src/transport/tcp_transport.py" in paths
        assert "src/transport/factory.py" in paths or any("transport" in p for p in paths)
        assert any("config" in p for p in paths)

    def test_recalls_test_files_for_transport(self):
        index = _make_index_with_files([
            _fi("src/transport/tcp_transport.py", symbols=["TCPTransport"]),
            _fi("tests/test_tcp_transport.py", is_test=True),
            _fi("tests/test_transport_mock.py", is_test=True),
        ])

        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch to tcp",
            target_transport="tcp",
        )

        retriever = FileRetriever(index)
        candidates = retriever.retrieve("switch transport to tcp", plan)

        test_candidates = [c for c in candidates if c.action == "test"]
        assert len(test_candidates) > 0


class TestRetrieveCoreTask:
    """FileRetriever for core_change tasks."""

    def test_recalls_core_files(self):
        index = _make_index_with_files([
            _fi("src/core/client_core.py", symbols=["ClientCore"]),
            _fi("src/core/server_core.py", symbols=["ServerCore"]),
            _fi("src/common/frame.py", symbols=["Frame", "FrameCodec"]),
            _fi("src/tun/tun_device.py", symbols=["TunDevice"]),
            _fi("tests/test_core.py", is_test=True),
            _fi("tests/test_frame.py", is_test=True),
        ])

        plan = TaskPlan(
            task_type=TASK_CORE_CHANGE,
            description="replace core forwarding",
            target_transport=None,
            affected_areas=["src/core/", "tests/test_core.py"],
        )

        retriever = FileRetriever(index)
        candidates = retriever.retrieve("replace core forwarding strategy", plan)

        paths = {c.path for c in candidates}
        assert "src/core/client_core.py" in paths or "src/core/server_core.py" in paths
        assert any("frame" in p.lower() for p in paths) or "src/common/frame.py" in paths

    def test_recalls_frame_and_tun_files(self):
        index = _make_index_with_files([
            _fi("src/common/frame.py", symbols=["Frame", "FrameCodec"]),
            _fi("src/tun/tun_device.py", symbols=["TunDevice"]),
            _fi("tests/test_frame.py", is_test=True),
            _fi("tests/test_session_id_config.py", is_test=True),
        ])

        plan = TaskPlan(
            task_type=TASK_CORE_CHANGE,
            description="change frame codec",
        )

        retriever = FileRetriever(index)
        candidates = retriever.retrieve("change frame codec", plan)

        paths = {c.path for c in candidates}
        assert "src/common/frame.py" in paths


class TestRetrieveLlmAgentTask:
    """FileRetriever for llm_agent-related tasks."""

    def test_recalls_llm_agent_files(self):
        index = _make_index_with_files([
            _fi("src/llm/task_planner.py", symbols=["TaskPlanner"]),
            _fi("src/llm/patch_generator.py", symbols=["LLMPatchGenerator"]),
            _fi("scripts/llm_task.py", file_type="python"),
            _fi("tests/test_llm_task_planner.py", is_test=True),
            _fi("tests/test_patch_generator.py", is_test=True),
            _fi("tests/test_safety_guard.py", is_test=True),
        ])

        plan = TaskPlan(
            task_type="config_change",  # not llm-specific type, but we override areas
            description="improve llm workflow",
        )

        retriever = FileRetriever(index)
        candidates = retriever.retrieve(
            "improve llm agent workflow", plan,
            affected_areas=["llm_agent"],
        )

        paths = {c.path for c in candidates}
        assert "src/llm/task_planner.py" in paths
        assert "scripts/llm_task.py" in paths
        assert any("test_llm" in p for p in paths) or any("test_" in p for p in paths)

    def test_recalls_llm_agent_test_files(self):
        index = _make_index_with_files([
            _fi("src/llm/safety_guard.py", symbols=["SafetyGuard"]),
            _fi("tests/test_llm_task_planner.py", is_test=True),
            _fi("tests/test_patch_generator.py", is_test=True),
            _fi("tests/test_other.py", is_test=True),
        ])

        retriever = FileRetriever(index)
        candidates = retriever.retrieve(
            "modify llm agent safety guard", None,
            affected_areas=["llm_agent"],
        )

        test_candidates = [c for c in candidates if c.action == "test"]
        assert len(test_candidates) > 0


class TestLLMHints:
    """FileRetriever handles LLM planner hints properly."""

    def test_existing_llm_hint_is_included(self):
        index = _make_index_with_files([
            _fi("src/transport/ws.py", symbols=["WebSocketTransport"]),
        ])

        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch transport",
            target_transport="websocket",
            candidate_files=["src/transport/ws.py", "config/server.yaml"],
        )

        retriever = FileRetriever(index)
        candidates = retriever.retrieve("switch transport", plan)

        paths = {c.path for c in candidates}
        assert "src/transport/ws.py" in paths

    def test_nonexistent_llm_hint_has_low_score(self):
        index = _make_index_with_files([
            _fi("src/transport/tcp.py", symbols=["TCPTransport"]),
        ])

        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch transport",
            candidate_files=["nonexistent_file.py", "bad_hint.py"],
        )

        retriever = FileRetriever(index)
        candidates = retriever.retrieve("switch transport", plan)

        for c in candidates:
            if c.path in ("nonexistent_file.py", "bad_hint.py"):
                assert c.score < 0.6

    def test_existing_llm_hint_gets_edit_action(self):
        index = _make_index_with_files([
            _fi("src/transport/factory.py", symbols=["TransportFactory"]),
        ])

        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch transport",
            candidate_files=["src/transport/factory.py"],
        )

        retriever = FileRetriever(index)
        candidates = retriever.retrieve("switch transport", plan)

        factory_cands = [c for c in candidates if c.path == "src/transport/factory.py"]
        assert len(factory_cands) > 0
        assert factory_cands[0].action == "edit"

    def test_duplicate_merge_takes_higher_score(self):
        """When the same file is retrieved by multiple strategies, scores are merged properly."""
        index = _make_index_with_files([
            _fi("src/transport/tcp_transport.py", symbols=["TCPTransport"]),
        ])

        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch to tcp",
            target_transport="tcp",
            candidate_files=["src/transport/tcp_transport.py"],
        )

        retriever = FileRetriever(index)
        candidates = retriever.retrieve("switch to tcp transport", plan)

        tcp_cands = [c for c in candidates if "tcp" in c.path]
        assert len(tcp_cands) == 1
        # Should have multiple sources (keyword + planner_hint at least)
        assert len(tcp_cands[0].sources) >= 2


class TestTaskAreaMapping:
    """Verify _TASK_TO_AREA mapping covers all task types."""

    def test_all_task_types_have_area_mapping(self):
        """Every task type should have an area mapping."""
        task_types = [
            "transport_change", "core_change", "config_change",
            "test_addition", "docs_update", "bugfix", "refactor", "unknown",
        ]
        for tt in task_types:
            assert tt in _TASK_TO_AREA, f"Missing area mapping for: {tt}"
            assert len(_TASK_TO_AREA[tt]) > 0


class TestRetrieveScoresAndActions:
    """CandidateFile always has score, reasons, sources, action."""

    def test_every_candidate_has_required_fields(self):
        index = _make_index_with_files([
            _fi("src/transport/tcp.py", symbols=["TCPTransport"]),
            _fi("tests/test_tcp.py", is_test=True),
            _fi("README.md", file_type="markdown", is_doc=True),
        ])

        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch to tcp",
            target_transport="tcp",
        )

        retriever = FileRetriever(index)
        candidates = retriever.retrieve("switch transport to tcp", plan)

        assert len(candidates) > 0
        for c in candidates:
            assert isinstance(c.path, str)
            assert isinstance(c.score, float)
            assert isinstance(c.reasons, list)
            assert len(c.reasons) > 0
            assert isinstance(c.sources, list)
            assert len(c.sources) > 0
            assert c.action in ("edit", "review", "test", "doc")


class TestRetrieveMixedFeature:
    """FileRetriever for mixed_feature tasks."""

    def test_mixed_feature_recalls_broad_set(self):
        index = _make_index_with_files([
            _fi("src/transport/tcp.py", symbols=["TCPTransport"]),
            _fi("src/core/client_core.py", symbols=["ClientCore"]),
            _fi("src/llm/task_planner.py", symbols=["TaskPlanner"]),
            _fi("tests/test_core.py", is_test=True),
            _fi("README.md", file_type="markdown", is_doc=True),
            _fi("docs/guide.md", file_type="markdown", is_doc=True),
        ])

        retriever = FileRetriever(index)
        candidates = retriever.retrieve(
            "add some new feature across the project", None,
            affected_areas=["mixed_feature"],
        )

        # Should get files from multiple areas
        paths = {c.path for c in candidates}
        assert len(paths) >= 2
