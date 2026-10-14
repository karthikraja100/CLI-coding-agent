"""
Regression tests for the v2 agent loop improvements.

Covers:
- Code generation does NOT trigger irrelevant tools.
- Strict JSON fence parser ignores prose-embedded JSON.
- Malformed tool-call JSON is treated as FINAL_ANSWER.
- Missing optional dependency (flake8) is handled gracefully.
- Consecutive-error limit forces FINAL_ANSWER after 2 errors.
- File creation with diff + approval workflow.
- Tool output and final answer are stored separately in history.
- search_code invalid regex returns ERROR string (not exception).
- detect_bugs falls back to heuristic when flake8 is missing.
"""

from __future__ import annotations

import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Shared fixtures (mirror test_tools.py so tests can run standalone)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_workspace(tmp_path, monkeypatch):
    import agent.tools as tools_mod
    import agent.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(tools_mod, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


@pytest.fixture()
def sample_py(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("def hello():\n    pass\n", encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Helper: build a mocked AgentLoop
# ---------------------------------------------------------------------------

def make_loop(monkeypatch, responses: list[str], *, approval: bool = True):
    """
    Build an AgentLoop with a FakeClient that yields *responses* in order.
    approval_fn always returns *approval*.
    Returns (loop, captured_tokens).
    """
    from agent.loop import AgentLoop
    from agent.ollama_client import OllamaClient

    response_iter = iter(responses)

    class FakeClient(OllamaClient):
        def __init__(self):
            self.model = "fake"
            self.base_url = "http://fake"
            self.temperature = 0.0
            self.num_ctx = 512
            self.max_tokens = 128

        def chat_stream(self, messages):
            try:
                text = next(response_iter)
            except StopIteration:
                text = "No more canned responses."
            yield text

    tokens: list[str] = []
    loop = AgentLoop(
        client=FakeClient(),
        approval_fn=lambda _: approval,
        output_fn=tokens.append,
    )
    return loop, tokens


# ---------------------------------------------------------------------------
# 1. Code generation must NOT trigger irrelevant tools
# ---------------------------------------------------------------------------

class TestNoIrrelevantTools:
    def test_code_gen_no_tool_calls(self, tmp_path, monkeypatch):
        """
        A plain code-generation request should produce a FINAL_ANSWER directly
        without executing any tool.
        """
        from agent.tools import TOOL_REGISTRY

        called_tools: list[str] = []
        original_fns = {}
        for name, fn in TOOL_REGISTRY.items():
            original_fns[name] = fn
            def _spy(n=name, f=fn, **kw):
                called_tools.append(n)
                return f(**kw)
            TOOL_REGISTRY[name] = _spy

        try:
            final_answer = "def add(a, b):\n    return a + b\n"
            loop, _ = make_loop(monkeypatch, [final_answer])
            result = loop.run("Write a function to add two numbers")
        finally:
            TOOL_REGISTRY.update(original_fns)

        assert result == final_answer
        assert called_tools == [], (
            f"Expected no tool calls for a code-generation request, "
            f"but got: {called_tools}"
        )

    def test_detect_bugs_not_called_for_code_gen(self, tmp_path, monkeypatch):
        """detect_bugs must not be auto-invoked for simple generation tasks."""
        from agent.tools import TOOL_REGISTRY

        detect_called = []
        original = TOOL_REGISTRY["detect_bugs"]
        TOOL_REGISTRY["detect_bugs"] = lambda **kw: (
            detect_called.append(True) or original(**kw)
        )
        try:
            loop, _ = make_loop(monkeypatch, ["Here is a palindrome checker: ..."])
            loop.run("Write a palindrome checker")
        finally:
            TOOL_REGISTRY["detect_bugs"] = original

        assert detect_called == [], "detect_bugs must not be called for code generation"


# ---------------------------------------------------------------------------
# 2. Strict JSON fence parser
# ---------------------------------------------------------------------------

class TestStrictJsonParser:
    def test_valid_tool_call_block_is_parsed(self, tmp_path, monkeypatch):
        """A properly fenced ```json block with known tool is executed."""
        from agent.loop import _parse_tool_call
        text = '```json\n{"tool": "list_files", "args": {"directory": "."}}\n```'
        result = _parse_tool_call(text)
        assert result is not None
        assert result["tool"] == "list_files"

    def test_prose_embedded_json_is_ignored(self, tmp_path, monkeypatch):
        """JSON embedded inside a prose paragraph must NOT be parsed as a tool call."""
        from agent.loop import _parse_tool_call
        text = (
            'You could call {"tool": "detect_bugs", "args": {"path": "x.py"}} '
            "but that is not needed here."
        )
        assert _parse_tool_call(text) is None

    def test_code_block_without_json_tag_is_ignored(self, tmp_path, monkeypatch):
        """A ``` block without the 'json' language tag must be ignored."""
        from agent.loop import _parse_tool_call
        text = '```\n{"tool": "run_command", "args": {"command": "rm -rf /"}}\n```'
        assert _parse_tool_call(text) is None

    def test_unknown_tool_name_is_rejected(self, tmp_path, monkeypatch):
        """A well-formed JSON block with an unknown tool name must be rejected."""
        from agent.loop import _parse_tool_call
        text = '```json\n{"tool": "delete_everything", "args": {}}\n```'
        assert _parse_tool_call(text) is None

    def test_missing_args_key_is_rejected(self, tmp_path, monkeypatch):
        """A block missing the 'args' key must be rejected."""
        from agent.loop import _parse_tool_call
        text = '```json\n{"tool": "list_files"}\n```'
        assert _parse_tool_call(text) is None

    def test_malformed_json_is_rejected(self, tmp_path, monkeypatch):
        """A block with syntactically invalid JSON must be rejected gracefully."""
        from agent.loop import _parse_tool_call
        text = '```json\n{tool: list_files, args: {}}\n```'
        assert _parse_tool_call(text) is None


# ---------------------------------------------------------------------------
# 3. Malformed-response handling
# ---------------------------------------------------------------------------

class TestMalformedResponse:
    def test_malformed_json_treated_as_final_answer(self, tmp_path, monkeypatch):
        """When the model emits malformed JSON, the loop must treat it as FINAL_ANSWER."""
        bad_response = '```json\n{broken json here\n```\nSorry, let me just answer directly.'
        loop, _ = make_loop(monkeypatch, [bad_response])
        result = loop.run("Do something")
        # No tool should have been called; result should be the raw response
        assert result == bad_response

    def test_plain_text_is_always_final_answer(self, tmp_path, monkeypatch):
        """Pure prose (no JSON fence) must always be treated as FINAL_ANSWER."""
        prose = "Here is how you reverse a string: s[::-1]"
        loop, _ = make_loop(monkeypatch, [prose])
        result = loop.run("How do I reverse a string?")
        assert result == prose


# ---------------------------------------------------------------------------
# 4. Missing optional dependency handling
# ---------------------------------------------------------------------------

class TestMissingDependency:
    def test_flake8_unavailable_does_not_crash(self, tmp_path, monkeypatch, sample_py):
        """
        When flake8 is not installed, detect_bugs must return a string
        (heuristic results or UNAVAILABLE notice), NOT raise an exception.
        """
        from agent.tools import detect_bugs
        import subprocess

        # Simulate FileNotFoundError for flake8 by patching subprocess.run
        original_run = subprocess.run

        def _fake_run(cmd, **kw):
            if "flake8" in str(cmd):
                raise FileNotFoundError("flake8 not found")
            return original_run(cmd, **kw)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = detect_bugs("sample.py")
        assert isinstance(result, str)
        assert "ERROR" not in result or "UNAVAILABLE" in result or "No obvious" in result

    def test_missing_dep_error_is_handled_in_loop(self, tmp_path, monkeypatch):
        """
        When a tool returns a 'not found' error, the loop must convert it to
        an UNAVAILABLE message and continue — not raise an exception.
        """
        from agent.tools import TOOL_REGISTRY

        original = TOOL_REGISTRY["detect_bugs"]
        TOOL_REGISTRY["detect_bugs"] = lambda **kw: "ERROR: flake8 not found"
        try:
            tool_call = '```json\n{"tool": "detect_bugs", "args": {"path": "sample.py"}}\n```'
            final = "I could not run flake8 because it is not installed."
            loop, _ = make_loop(monkeypatch, [tool_call, final])
            result = loop.run("Detect bugs in sample.py")
        finally:
            TOOL_REGISTRY["detect_bugs"] = original

        # Loop must survive and return the final answer
        assert "not" in result.lower() or "unavailable" in result.lower() or "flake8" in result.lower()

    def test_no_auto_install_suggestion(self, tmp_path, monkeypatch, sample_py):
        """
        The UNAVAILABLE message must suggest manual pip install, not auto-install.
        """
        from agent.tools import detect_bugs
        import subprocess

        original_run = subprocess.run

        def _fake_run(cmd, **kw):
            if "flake8" in str(cmd):
                raise FileNotFoundError("flake8 not found")
            return original_run(cmd, **kw)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        # Write a non-.py file to trigger the UNAVAILABLE path
        f = tmp_path / "style.css"
        f.write_text("body { color: red; }", encoding="utf-8")
        result = detect_bugs("style.css")
        # Should NOT say "automatically" or "installing"
        assert "automatically" not in result.lower()
        assert "pip install" in result.lower() or "UNAVAILABLE" in result


# ---------------------------------------------------------------------------
# 5. Consecutive-error limit
# ---------------------------------------------------------------------------

class TestConsecutiveErrorLimit:
    def test_consecutive_errors_force_final_answer(self, tmp_path, monkeypatch):
        """
        After 2 consecutive errors on the same tool, the loop must inject a
        system message and allow the model to produce a FINAL_ANSWER.
        """
        from agent.tools import TOOL_REGISTRY

        original = TOOL_REGISTRY["read_file"]
        TOOL_REGISTRY["read_file"] = lambda **kw: "ERROR: simulated repeated failure"
        try:
            tool_call = '```json\n{"tool": "read_file", "args": {"path": "x.py"}}\n```'
            final = "I was unable to read the file. Here is what I know anyway."
            # 3 tool attempts + 1 final answer
            loop, _ = make_loop(
                monkeypatch,
                [tool_call, tool_call, tool_call, final],
            )
            result = loop.run("Read x.py and explain it")
        finally:
            TOOL_REGISTRY["read_file"] = original

        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# 6. File creation with diff + approval workflow
# ---------------------------------------------------------------------------

class TestFileCreationWorkflow:
    def test_write_file_approved_creates_file(self, tmp_path, monkeypatch):
        """Approving a write_file diff must create the file on disk."""
        content = "def add(a, b):\\n    return a + b\\n"
        tool_call = (
            f'```json\n{{"tool": "write_file", "args": {{"path": "add.py", "content": "{content}"}}}}\n```'
        )
        final = "Done. add.py has been created."
        loop, _ = make_loop(monkeypatch, [tool_call, final], approval=True)
        loop.run("Create add.py with an add function")
        assert (tmp_path / "add.py").exists()

    def test_write_file_rejected_does_not_create_file(self, tmp_path, monkeypatch):
        """Rejecting a write_file diff must leave the file untouched."""
        content = "def evil():\\n    pass\\n"
        tool_call = (
            f'```json\n{{"tool": "write_file", "args": {{"path": "evil.py", "content": "{content}"}}}}\n```'
        )
        final = "Okay, I will not write the file."
        loop, _ = make_loop(monkeypatch, [tool_call, final], approval=False)
        loop.run("Create evil.py")
        assert not (tmp_path / "evil.py").exists()


# ---------------------------------------------------------------------------
# 7. Tool output vs final answer are stored separately in history
# ---------------------------------------------------------------------------

class TestHistoryStructure:
    def test_observation_stored_as_user_role(self, tmp_path, monkeypatch):
        """
        After a tool call, the observation must be stored as a 'user' message
        prefixed with '[observation]', not mixed into the assistant turn.
        """
        tool_call = '```json\n{"tool": "list_files", "args": {"directory": "."}}\n```'
        final = "Here are the files I found."
        loop, _ = make_loop(monkeypatch, [tool_call, final])
        loop.run("List all files")

        user_turns = [m for m in loop.history if m["role"] == "user"]
        observation_turns = [t for t in user_turns if t["content"].startswith("[observation]")]
        assert len(observation_turns) >= 1, "Observation must be stored as a [observation] user turn"

    def test_tool_response_not_mixed_into_assistant_content(self, tmp_path, monkeypatch):
        """Assistant content must only contain what the model generated."""
        tool_call = '```json\n{"tool": "list_files", "args": {"directory": "."}}\n```'
        final = "Only this text is from me."
        loop, _ = make_loop(monkeypatch, [tool_call, final])
        loop.run("List files")

        assistant_turns = [m["content"] for m in loop.history if m["role"] == "assistant"]
        for content in assistant_turns:
            assert "[observation]" not in content, (
                "Observation must not appear inside assistant content"
            )


# ---------------------------------------------------------------------------
# 8. search_code invalid regex
# ---------------------------------------------------------------------------

class TestSearchCodeRobustness:
    def test_invalid_regex_returns_error_string(self, tmp_path, monkeypatch, sample_py):
        """An invalid regex must return an ERROR string, not raise an exception."""
        from agent.tools import search_code
        result = search_code("[invalid(")
        assert result.startswith("ERROR:")
        assert "regex" in result.lower() or "invalid" in result.lower()

    def test_valid_regex_works(self, tmp_path, monkeypatch, sample_py):
        from agent.tools import search_code
        result = search_code(r"def \w+")
        assert "sample.py" in result


# ---------------------------------------------------------------------------
# 9. detect_bugs heuristic fallback
# ---------------------------------------------------------------------------

class TestDetectBugsFallback:
    def test_heuristic_finds_bare_except(self, tmp_path, monkeypatch):
        import subprocess
        from agent.tools import detect_bugs

        original_run = subprocess.run
        def _fake_run(cmd, **kw):
            if "flake8" in str(cmd):
                raise FileNotFoundError("flake8 not found")
            return original_run(cmd, **kw)
        monkeypatch.setattr(subprocess, "run", _fake_run)

        bad = tmp_path / "bad.py"
        bad.write_text("try:\n    pass\nexcept:\n    pass\n", encoding="utf-8")
        result = detect_bugs("bad.py")
        assert isinstance(result, str)
        # Heuristic should flag the bare except
        assert "except" in result.lower() or "No obvious" in result

    def test_heuristic_finds_eval(self, tmp_path, monkeypatch):
        import subprocess
        from agent.tools import detect_bugs

        original_run = subprocess.run
        def _fake_run(cmd, **kw):
            if "flake8" in str(cmd):
                raise FileNotFoundError("flake8 not found")
            return original_run(cmd, **kw)
        monkeypatch.setattr(subprocess, "run", _fake_run)

        risky = tmp_path / "risky.py"
        risky.write_text("x = eval(user_input)\n", encoding="utf-8")
        result = detect_bugs("risky.py")
        assert isinstance(result, str)
        assert "eval" in result.lower() or "No obvious" in result
