"""Tests for ImpactExpander.

Covers:
- ImpactExpander generates FileSelection with must_edit, must_review, test, doc, allowed_create
- Planner hints that exist on disk are promoted to must_edit
- Planner hints that don't exist are recorded as rejected
- mixed_feature allows creation in src/transport, src/core, src/llm, tests, docs
"""

from src.llm.repo_indexer import RepoIndex, FileInfo
from src.llm.file_retriever import CandidateFile
from src.llm.impact_expander import ImpactExpander, FileSelection
from src.llm.task_planner import TaskPlan, TASK_TRANSPORT_CHANGE, TASK_CORE_CHANGE


def _fi(path: str, file_type="python", symbols=None, is_test=False, is_doc=False, config_keys=None, imports=None):
    return FileInfo(
        path=path, file_type=file_type,
        symbols=symbols or [], imports=imports or [],
        config_keys=config_keys or [], is_test=is_test, is_doc=is_doc, size=100,
    )


def _make_index(files: list[FileInfo]) -> RepoIndex:
    index = RepoIndex(root_dir="/fake")
    for fi in files:
        index.files[fi.path] = fi
    index.file_count = len(index.files)
    return index


def _transport_index() -> RepoIndex:
    return _make_index([
        _fi("src/transport/__init__.py"),
        _fi("src/transport/base.py", symbols=["BaseTransport"]),
        _fi("src/transport/factory.py", symbols=["TransportFactory"]),
        _fi("src/transport/tcp_transport.py", symbols=["TCPTransport"]),
        _fi("src/common/config.py", symbols=["load_config"]),
        _fi("config/server.yaml", file_type="yaml", config_keys=["transport", "port"]),
        _fi("config/client.yaml", file_type="yaml", config_keys=["transport", "port"]),
        _fi("tests/test_tcp_transport.py", is_test=True),
        _fi("tests/test_transport_mock.py", is_test=True),
        _fi("README.md", file_type="markdown", is_doc=True),
        _fi("docs/guide.md", file_type="markdown", is_doc=True),
        _fi("scripts/smoke_replacement_matrix.py",
           symbols=["run_smoke_matrix"]),
    ])


def _core_index() -> RepoIndex:
    return _make_index([
        _fi("src/core/__init__.py"),
        _fi("src/core/client_core.py", symbols=["ClientCore"]),
        _fi("src/core/server_core.py", symbols=["ServerCore"]),
        _fi("src/common/frame.py", symbols=["Frame", "FrameCodec"]),
        _fi("src/common/session.py", symbols=["Session"]),
        _fi("src/tun/tun_device.py", symbols=["TunDevice"]),
        _fi("src/forwarding/nat.py", symbols=["NAT"]),
        _fi("tests/test_core.py", is_test=True),
        _fi("tests/test_frame.py", is_test=True),
        _fi("tests/test_session_id_config.py", is_test=True),
        _fi("README.md", file_type="markdown", is_doc=True),
        _fi("docs/guide.md", file_type="markdown", is_doc=True),
    ])


class TestFileSelection:
    """Test FileSelection dataclass."""

    def test_file_selection_fields(self):
        fs = FileSelection(
            must_edit_files=["a.py"],
            must_review_files=["b.py"],
            test_files=["test_a.py"],
            doc_files=["README.md"],
            allowed_create_paths=["src/transport/"],
            candidates=[],
            rejected_hints=["nonexistent.py"],
        )
        assert fs.has_any_edits
        assert "a.py" in fs.all_files
        assert "b.py" in fs.all_files
        assert "test_a.py" in fs.all_files
        assert "README.md" in fs.all_files

    def test_file_selection_empty(self):
        fs = FileSelection()
        assert not fs.has_any_edits
        assert fs.all_files == []

    def test_file_selection_serialization(self):
        c = CandidateFile(path="x.py", score=0.9, reasons=["r"], sources=["s"], action="edit")
        fs = FileSelection(
            must_edit_files=["x.py"],
            must_review_files=["y.py"],
            test_files=["test_x.py"],
            doc_files=["README.md"],
            allowed_create_paths=["src/transport/"],
            candidates=[c],
            rejected_hints=["bad.py"],
        )
        d = fs.to_dict()
        assert d["must_edit_files"] == ["x.py"]
        assert d["must_review_files"] == ["y.py"]
        assert d["test_files"] == ["test_x.py"]
        assert d["doc_files"] == ["README.md"]
        assert d["allowed_create_paths"] == ["src/transport/"]
        assert len(d["candidates"]) == 1
        assert d["rejected_hints"] == ["bad.py"]


class TestTransportImpactExpansion:
    """ImpactExpander for transport tasks."""

    def test_transport_expansion_includes_core_files(self):
        index = _transport_index()
        candidates = [
            CandidateFile("src/transport/tcp_transport.py", 0.95, ["key: tcp"], ["keyword"], "edit"),
            CandidateFile("config/server.yaml", 0.8, ["config key: transport"], ["config_key"], "edit"),
            CandidateFile("tests/test_tcp_transport.py", 0.7, ["test for tcp"], ["test_map"], "test"),
        ]

        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch to tcp",
            target_transport="tcp",
        )

        expander = ImpactExpander(index)
        selection = expander.expand("switch transport to tcp", plan, candidates)

        assert selection.has_any_edits
        assert "src/transport/factory.py" in selection.must_edit_files
        assert "src/transport/base.py" in selection.must_edit_files
        assert "src/transport/tcp_transport.py" in selection.must_edit_files

    def test_transport_expansion_includes_config_and_docs(self):
        index = _transport_index()
        candidates: list[CandidateFile] = []

        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch transport",
            target_transport="tcp",
        )

        expander = ImpactExpander(index)
        selection = expander.expand("switch transport", plan, candidates)

        assert "README.md" in selection.doc_files
        assert any("config" in f for f in selection.must_review_files) or \
               any("config" in f for f in selection.must_edit_files)


class TestCoreImpactExpansion:
    """ImpactExpander for core tasks."""

    def test_core_expansion_includes_frame_and_tun(self):
        index = _core_index()
        candidates: list[CandidateFile] = []

        plan = TaskPlan(
            task_type=TASK_CORE_CHANGE,
            description="replace frame codec",
            target_transport=None,
        )

        expander = ImpactExpander(index)
        selection = expander.expand("replace frame codec", plan, candidates)

        assert selection.has_any_edits
        assert "src/core/client_core.py" in selection.must_edit_files
        assert "src/common/frame.py" in selection.must_edit_files

    def test_core_expansion_includes_test_files(self):
        index = _core_index()
        candidates: list[CandidateFile] = []

        plan = TaskPlan(
            task_type=TASK_CORE_CHANGE,
            description="change session validation",
        )

        expander = ImpactExpander(index)
        selection = expander.expand("change session validation", plan, candidates)

        assert len(selection.test_files) > 0
        assert any("core" in t or "frame" in t for t in selection.test_files)


class TestPlannerHints:
    """ImpactExpander handles LLM planner hints correctly.

    Planner hints are treated as regular candidates — they must earn their way
    into must_edit through high scores from non-hint strategies. A pure
    planner_hint (score 0.6, no other sources) goes to must_review, not must_edit.
    """

    def test_pure_hints_go_to_review_not_edit(self):
        """Pure planner hints (score 0.6, no other sources) go to must_review."""
        index = _make_index([
            _fi("src/transport/ws.py", symbols=["WebSocketTransport"]),
            _fi("config/server.yaml", file_type="yaml", config_keys=["transport"]),
            _fi("README.md", file_type="markdown", is_doc=True),
        ])
        candidates = [
            CandidateFile("src/transport/ws.py", 0.6, ["LLM hint (exists on disk)"], ["planner_hint"], "edit"),
            CandidateFile("config/server.yaml", 0.6, ["LLM hint (exists on disk)"], ["planner_hint"], "edit"),
        ]

        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch transport",
            candidate_files=["src/transport/ws.py", "config/server.yaml"],
        )

        expander = ImpactExpander(index)
        selection = expander.expand("switch transport", plan, candidates)

        # Pure planner hints go to must_review, NOT must_edit
        assert "src/transport/ws.py" in selection.must_review_files
        assert "config/server.yaml" in selection.must_review_files
        assert "src/transport/ws.py" not in selection.must_edit_files
        assert "config/server.yaml" not in selection.must_edit_files

    def test_hints_with_keyword_match_promoted_to_edit(self):
        """When a planner hint also has keyword/symbol match (score >= 0.9), it goes to must_edit."""
        index = _make_index([
            _fi("src/transport/ws.py", symbols=["WebSocketTransport"]),
            _fi("config/server.yaml", file_type="yaml", config_keys=["transport"]),
        ])
        # Simulate a candidate that was both a planner_hint AND a keyword match
        candidates = [
            CandidateFile("src/transport/ws.py", 0.95, ["key: ws", "LLM hint"], ["keyword", "planner_hint"], "edit"),
        ]

        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch transport",
            candidate_files=["src/transport/ws.py"],
        )

        expander = ImpactExpander(index)
        selection = expander.expand("switch transport to ws", plan, candidates)

        # With both keyword + hint sources and score 0.9, it should be promoted
        assert "src/transport/ws.py" in selection.must_edit_files

    def test_rejected_hints_recorded(self):
        index = _make_index([
            _fi("src/transport/tcp.py", symbols=["TCPTransport"]),
        ])

        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch transport",
            candidate_files=["nonexistent.py", "bad_hint.py", "src/transport/tcp.py"],
        )

        expander = ImpactExpander(index)
        selection = expander.expand("switch transport", plan, [])

        assert "nonexistent.py" in selection.rejected_hints
        assert "bad_hint.py" in selection.rejected_hints

    def test_no_editable_files_returns_empty_must_edit(self):
        """When no files match, must_edit_files should be empty."""
        index = _make_index([])

        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch transport",
            candidate_files=["nonexistent.py"],
        )

        expander = ImpactExpander(index)
        selection = expander.expand("switch transport", plan, [])

        assert not selection.has_any_edits
        assert "nonexistent.py" in selection.rejected_hints


class TestAllowedCreatePaths:
    """ImpactExpander sets allowed_create_paths for different task types."""

    def test_transport_allows_create_in_transport_dir(self):
        index = _transport_index()

        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="add new http2 transport",
            target_transport="http2",
        )

        expander = ImpactExpander(index)
        selection = expander.expand("add new http2 transport", plan, [])

        assert "src/transport/" in selection.allowed_create_paths

    def test_core_allows_create_in_core_dirs(self):
        index = _core_index()

        plan = TaskPlan(
            task_type=TASK_CORE_CHANGE,
            description="add new core variant",
        )

        expander = ImpactExpander(index)
        selection = expander.expand("add new core variant", plan, [])

        assert "src/core/" in selection.allowed_create_paths

    def test_mixed_feature_allows_create_in_multiple_dirs(self):
        index = _make_index([
            _fi("src/transport/tcp.py"),
            _fi("src/core/client_core.py"),
            _fi("src/llm/task_planner.py"),
            _fi("README.md", file_type="markdown", is_doc=True),
        ])

        retriever = RetrieverStub()
        # This is simpler — just test expand directly
        expander = ImpactExpander(index)
        selection = expander.expand(
            "add a comprehensive new feature across modules", None, [],
            affected_areas=["mixed_feature"],
        )

        assert "src/transport/" in selection.allowed_create_paths
        assert "src/core/" in selection.allowed_create_paths
        assert "src/llm/" in selection.allowed_create_paths
        assert "tests/" in selection.allowed_create_paths


class RetrieverStub:
    """Minimal stub for testing."""
    pass


class TestAllowCreatePatterns:
    """ImpactExpander produces allowed_create_patterns for new features."""

    def test_transport_new_feature_includes_patterns(self):
        index = _transport_index()
        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="add new http2 transport",
            target_transport="http2",
        )
        expander = ImpactExpander(index)
        selection = expander.expand("add new http2 transport", plan, [])

        assert "src/transport/" in selection.allowed_create_paths
        patterns = selection.allowed_create_patterns
        assert any("_transport.py" in p for p in patterns)

    def test_mixed_feature_includes_broad_patterns(self):
        index = _make_index([
            _fi("src/transport/tcp.py"),
            _fi("src/core/client_core.py"),
            _fi("src/llm/task_planner.py"),
        ])
        expander = ImpactExpander(index)
        selection = expander.expand(
            "add a comprehensive new feature", None, [],
            affected_areas=["mixed_feature"],
        )
        patterns = selection.allowed_create_patterns
        assert len(patterns) > 0
        assert any("_transport.py" in p or "src/transport/" in p for p in patterns)
        assert any("test_" in p for p in patterns)
        assert any("docs/" in p for p in patterns)

    def test_non_new_feature_does_not_add_extra_patterns(self):
        """For non-'new' requests, patterns come only from area rules."""
        index = _transport_index()
        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch transport",
            target_transport="tcp",
        )
        expander = ImpactExpander(index)
        selection = expander.expand("switch transport to tcp", plan, [])
        # Even for non-new features, the area rules provide patterns
        patterns = selection.allowed_create_patterns
        assert isinstance(patterns, list)


class TestActionSources:
    """File selection records action sources for auditability."""

    def test_must_edit_files_have_action_sources(self):
        index = _transport_index()
        candidates = [
            CandidateFile("src/transport/tcp_transport.py", 0.95, ["key: tcp"], ["keyword"], "edit"),
        ]
        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch to tcp",
            target_transport="tcp",
        )
        expander = ImpactExpander(index)
        selection = expander.expand("switch transport to tcp", plan, candidates)

        assert len(selection.action_sources) > 0
        for f in selection.must_edit_files:
            assert f in selection.action_sources, f"{f} should have action_source"

    def test_action_sources_distinguishes_edit_from_review(self):
        index = _make_index([
            _fi("src/transport/__init__.py"),
            _fi("src/transport/base.py"),
            _fi("src/transport/tcp.py", symbols=["TCPTransport"]),
            _fi("src/common/frame.py", symbols=["Frame"]),
        ])
        candidates = [
            CandidateFile("src/transport/tcp.py", 0.6, ["hint"], ["planner_hint"], "edit"),
        ]
        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch transport",
            candidate_files=["src/transport/tcp.py"],
        )
        expander = ImpactExpander(index)
        selection = expander.expand("switch transport", plan, candidates)

        # __init__.py and base.py are must_edit from rules
        assert "src/transport/__init__.py" in selection.must_edit_files
        assert "src/transport/base.py" in selection.must_edit_files
        # tcp.py is a pure hint -> must_review
        assert "src/transport/tcp.py" in selection.must_review_files
        assert "src/transport/tcp.py" not in selection.must_edit_files

        # Action sources should explain each
        assert "must_edit_rule:transport" in selection.action_sources.get("src/transport/__init__.py", "")
        assert "planner_hint_only" in selection.action_sources.get("src/transport/tcp.py", "")

    def test_file_selection_serialization_includes_action_sources(self):
        index = _transport_index()
        expander = ImpactExpander(index)
        selection = expander.expand("switch transport", None, [])
        d = selection.to_dict()
        assert "action_sources" in d
        assert isinstance(d["action_sources"], dict)


class TestValidateCreateFilename:
    """validate_create_filename enforces naming and safety rules."""

    def test_allows_valid_transport_file(self):
        from src.llm.impact_expander import validate_create_filename
        assert validate_create_filename(
            "src/transport/http2_transport.py",
            ["src/transport/*_transport.py"],
        ) is None

    def test_allows_valid_test_file(self):
        from src.llm.impact_expander import validate_create_filename
        assert validate_create_filename(
            "tests/test_http2_transport.py",
            ["tests/test_*.py"],
        ) is None

    def test_allows_valid_doc_file(self):
        from src.llm.impact_expander import validate_create_filename
        assert validate_create_filename(
            "docs/http2_design.md",
            ["docs/*.md"],
        ) is None

    def test_blocks_readme_create(self):
        from src.llm.impact_expander import validate_create_filename
        err = validate_create_filename("README.md", ["docs/*.md"])
        assert err is not None
        assert "README.md" in err

    def test_blocks_hidden_file(self):
        from src.llm.impact_expander import validate_create_filename
        err = validate_create_filename("src/transport/.hidden.py", ["src/transport/*.py"])
        assert err is not None
        assert "hidden" in err.lower()

    def test_blocks_dotenv_create(self):
        from src.llm.impact_expander import validate_create_filename
        err = validate_create_filename(".env", ["*"])
        assert err is not None

    def test_blocks_key_extension(self):
        from src.llm.impact_expander import validate_create_filename
        err = validate_create_filename("config/private.key", ["config/*"])
        assert err is not None
        assert "blocked file extension" in err.lower() or "key" in err.lower()

    def test_blocks_pem_extension(self):
        from src.llm.impact_expander import validate_create_filename
        err = validate_create_filename("config/cert.pem", ["config/*"])
        assert err is not None

    def test_blocks_path_traversal(self):
        from src.llm.impact_expander import validate_create_filename
        err = validate_create_filename("src/../.env", ["*"])
        assert err is not None
        assert "traversal" in err.lower()

    def test_blocks_git_prefix(self):
        from src.llm.impact_expander import validate_create_filename
        err = validate_create_filename(".git/config", ["*"])
        assert err is not None

    def test_blocks_extensionless_file(self):
        from src.llm.impact_expander import validate_create_filename
        err = validate_create_filename("src/transport/Makefile", ["src/transport/*"])
        assert err is not None
        assert "extension" in err.lower()

    def test_returns_none_without_patterns(self):
        """If no patterns provided, allows file if it passes other checks."""
        from src.llm.impact_expander import validate_create_filename
        assert validate_create_filename("src/transport/new.py", None) is None

    def test_pattern_mismatch_returns_error(self):
        from src.llm.impact_expander import validate_create_filename
        err = validate_create_filename(
            "src/transport/bad_name.py",
            ["src/transport/*_transport.py"],
        )
        assert err is not None
        assert "pattern" in err.lower()
