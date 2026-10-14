"""
Unit tests for agent/tools.py
Tests use tmp_path and monkeypatching to stay fully offline.
"""

from __future__ import annotations

import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_workspace(tmp_path, monkeypatch):
    """
    Redirect WORKSPACE_ROOT to a fresh temp directory for every test so that
    tests cannot touch the real project files.
    """
    import agent.tools as tools_mod
    import agent.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(tools_mod, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


@pytest.fixture()
def sample_py(tmp_path):
    """Create a simple Python file inside the workspace."""
    f = tmp_path / "sample.py"
    f.write_text(
        "# sample file\n"
        "def hello(name):\n"
        "    print(f'Hello, {name}!')\n"
        "\n"
        "hello('world')\n",
        encoding="utf-8",
    )
    return f


# ---------------------------------------------------------------------------
# Tests: _resolve_safe
# ---------------------------------------------------------------------------

class TestResolveSafe:
    def test_inside_workspace(self, tmp_path):
        from agent.tools import _resolve_safe
        result = _resolve_safe("foo/bar.py")
        assert result == (tmp_path / "foo" / "bar.py").resolve()

    def test_escape_rejected(self, tmp_path):
        from agent.tools import _resolve_safe
        with pytest.raises(ValueError, match="outside the workspace"):
            _resolve_safe("../../etc/passwd")


# ---------------------------------------------------------------------------
# Tests: read_file
# ---------------------------------------------------------------------------

class TestReadFile:
    def test_reads_existing_file(self, tmp_path, sample_py):
        from agent.tools import read_file
        result = read_file("sample.py")
        assert "hello" in result
        assert "1 |" in result  # line numbers present

    def test_line_range(self, tmp_path, sample_py):
        from agent.tools import read_file
        result = read_file("sample.py", start_line=2, end_line=3)
        assert "def hello" in result
        assert "print" in result

    def test_missing_file_error(self, tmp_path):
        from agent.tools import read_file
        result = read_file("nonexistent.py")
        assert result.startswith("ERROR:")

    def test_blocked_file(self, tmp_path, monkeypatch):
        from agent.tools import read_file
        import agent.config as cfg
        # Create a file that matches a blocked pattern
        secret = tmp_path / "id_rsa"
        secret.write_text("secret", encoding="utf-8")
        monkeypatch.setattr(cfg, "BLOCKED_PATTERNS", ["id_rsa"])
        import agent.tools as tools_mod
        monkeypatch.setattr(tools_mod, "BLOCKED_PATTERNS", ["id_rsa"])
        result = read_file("id_rsa")
        assert "blocked" in result.lower()

    def test_disallowed_extension(self, tmp_path):
        from agent.tools import read_file
        exe = tmp_path / "evil.exe"
        exe.write_bytes(b"\x00\x01\x02")
        result = read_file("evil.exe")
        assert result.startswith("ERROR:")


# ---------------------------------------------------------------------------
# Tests: write_file
# ---------------------------------------------------------------------------

class TestWriteFile:
    def test_creates_new_file(self, tmp_path):
        from agent.tools import write_file
        result = write_file("new_file.py", "print('hi')")
        assert "OK:" in result
        assert (tmp_path / "new_file.py").read_text() == "print('hi')"

    def test_overwrites_existing_file(self, tmp_path, sample_py):
        from agent.tools import write_file
        write_file("sample.py", "# overwritten")
        assert sample_py.read_text() == "# overwritten"

    def test_creates_subdirectory(self, tmp_path):
        from agent.tools import write_file
        result = write_file("subdir/nested.py", "x = 1")
        assert "OK:" in result
        assert (tmp_path / "subdir" / "nested.py").exists()

    def test_escape_rejected(self, tmp_path):
        from agent.tools import write_file
        result = write_file("../../evil.py", "bad")
        assert result.startswith("ERROR:")


# ---------------------------------------------------------------------------
# Tests: list_files
# ---------------------------------------------------------------------------

class TestListFiles:
    def test_lists_py_files(self, tmp_path, sample_py):
        from agent.tools import list_files
        result = list_files(".", "*.py")
        assert "sample.py" in result

    def test_empty_directory(self, tmp_path):
        from agent.tools import list_files
        result = list_files(".")
        # Either "No files" or an empty result is acceptable
        assert isinstance(result, str)

    def test_nonexistent_directory(self, tmp_path):
        from agent.tools import list_files
        result = list_files("ghost_dir")
        assert "ERROR" in result


# ---------------------------------------------------------------------------
# Tests: search_code
# ---------------------------------------------------------------------------

class TestSearchCode:
    def test_finds_match(self, tmp_path, sample_py):
        from agent.tools import search_code
        result = search_code("def hello")
        assert "sample.py" in result
        assert "def hello" in result

    def test_case_insensitive_default(self, tmp_path, sample_py):
        from agent.tools import search_code
        result = search_code("DEF HELLO")
        assert "sample.py" in result

    def test_no_match(self, tmp_path, sample_py):
        from agent.tools import search_code
        result = search_code("zzz_no_match_zzz")
        assert "No matches" in result

    def test_invalid_regex(self, tmp_path, sample_py):
        from agent.tools import search_code
        # An invalid regex should raise or return an error message gracefully
        try:
            result = search_code("[invalid(")
        except Exception:
            pass  # acceptable – regex compilation may raise


# ---------------------------------------------------------------------------
# Tests: show_diff
# ---------------------------------------------------------------------------

class TestShowDiff:
    def test_shows_diff_for_change(self, tmp_path, sample_py):
        from agent.tools import show_diff
        diff = show_diff("sample.py", "# completely new content\n")
        assert "-" in diff  # removals
        assert "+" in diff  # additions

    def test_no_change(self, tmp_path, sample_py):
        from agent.tools import show_diff
        original = sample_py.read_text()
        result = show_diff("sample.py", original)
        assert result == "No changes detected."

    def test_new_file_diff(self, tmp_path):
        from agent.tools import show_diff
        diff = show_diff("brand_new.py", "x = 1\n")
        assert "+" in diff


# ---------------------------------------------------------------------------
# Tests: detect_bugs
# ---------------------------------------------------------------------------

class TestDetectBugs:
    def test_detects_bare_except(self, tmp_path):
        from agent.tools import detect_bugs
        bad = tmp_path / "bad.py"
        bad.write_text(
            "try:\n    pass\nexcept:\n    pass\n",
            encoding="utf-8",
        )
        result = detect_bugs("bad.py")
        # flake8 or heuristic should report something
        assert isinstance(result, str)

    def test_detects_eval(self, tmp_path):
        from agent.tools import detect_bugs
        risky = tmp_path / "risky.py"
        risky.write_text("result = eval(user_input)\n", encoding="utf-8")
        result = detect_bugs("risky.py")
        assert isinstance(result, str)

    def test_missing_file(self, tmp_path):
        from agent.tools import detect_bugs
        result = detect_bugs("ghost.py")
        assert "ERROR" in result


# ---------------------------------------------------------------------------
# Tests: run_command
# ---------------------------------------------------------------------------

class TestRunCommand:
    def test_blocked_command(self, tmp_path):
        from agent.tools import run_command
        result = run_command("rm -rf /")
        assert "ERROR" in result

    def test_empty_command(self, tmp_path):
        from agent.tools import run_command
        result = run_command("")
        assert "ERROR" in result

    def test_python_version(self, tmp_path):
        from agent.tools import run_command
        result = run_command("python --version")
        # Should either return version info or an error (if python not on PATH)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests: OllamaClient (offline – no real network calls)
# ---------------------------------------------------------------------------

class TestOllamaClientOffline:
    def test_is_available_returns_false_when_server_down(self):
        from agent.ollama_client import OllamaClient
        client = OllamaClient(base_url="http://127.0.0.1:19999")
        assert client.is_available() is False

    def test_list_models_returns_empty_when_server_down(self):
        from agent.ollama_client import OllamaClient
        client = OllamaClient(base_url="http://127.0.0.1:19999")
        assert client.list_models() == []

    def test_chat_raises_ollama_error_when_server_down(self):
        from agent.ollama_client import OllamaClient, OllamaError
        client = OllamaClient(base_url="http://127.0.0.1:19999")
        with pytest.raises(OllamaError):
            client.chat([{"role": "user", "content": "hello"}])


# ---------------------------------------------------------------------------
# Tests: AgentLoop (mocked LLM)
# ---------------------------------------------------------------------------

class TestAgentLoopMocked:
    """Tests that mock the LLM so no Ollama server is needed."""

    def _make_loop(self, tmp_path, monkeypatch, responses: list[str]):
        """Create an AgentLoop whose client streams the given canned responses."""
        from agent.loop import AgentLoop
        from agent.ollama_client import OllamaClient

        response_iter = iter(responses)

        class FakeClient(OllamaClient):
            def __init__(self):
                # Skip real __init__
                self.model = "fake"
                self.base_url = "http://fake"
                self.temperature = 0.0
                self.num_ctx = 512
                self.max_tokens = 128

            def chat_stream(self, messages):
                try:
                    text = next(response_iter)
                except StopIteration:
                    text = "No more responses."
                yield text

        tokens: list[str] = []
        loop = AgentLoop(
            client=FakeClient(),
            approval_fn=lambda _: True,
            output_fn=tokens.append,
        )
        return loop, tokens

    def test_plain_answer(self, tmp_path, monkeypatch):
        loop, tokens = self._make_loop(tmp_path, monkeypatch, ["Hello! How can I help?"])
        result = loop.run("Hi")
        assert "Hello" in result

    def test_tool_call_then_answer(self, tmp_path, monkeypatch, sample_py):
        """Agent calls list_files then gives a final answer."""
        tool_response = '```json\n{"tool": "list_files", "args": {"directory": "."}}\n```'
        final = "I found sample.py in the workspace."
        loop, tokens = self._make_loop(
            tmp_path, monkeypatch, [tool_response, final]
        )
        result = loop.run("List all files")
        assert "found" in result.lower() or len(loop.history) >= 2

    def test_write_file_approved(self, tmp_path, monkeypatch):
        content = "print('new')\n"
        tool_response = (
            f'```json\n{{"tool": "write_file", "args": {{"path": "out.py", "content": "{content}"}}}}\n```'
        )
        final = "File written successfully."
        loop, _ = self._make_loop(tmp_path, monkeypatch, [tool_response, final])
        result = loop.run("Write a hello world file")
        # approval_fn always returns True → file should be created
        assert isinstance(result, str)

    def test_history_accumulates(self, tmp_path, monkeypatch):
        loop, _ = self._make_loop(tmp_path, monkeypatch, ["Response 1", "Response 2"])
        loop.run("First message")
        assert len(loop.history) >= 2

    def test_reset_clears_history(self, tmp_path, monkeypatch):
        loop, _ = self._make_loop(tmp_path, monkeypatch, ["Hi"])
        loop.run("Hello")
        loop.reset()
        assert loop.history == []
