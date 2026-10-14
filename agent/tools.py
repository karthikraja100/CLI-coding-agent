"""
Tool implementations for the AI Coding Agent.

Each public function is a *tool* the agent can call.  The TOOL_REGISTRY maps
the canonical tool name (used in model responses) to the Python callable.

Tool functions return a plain string result that is fed back to the model as
the "observation" in the ReAct loop.
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Callable

from agent.config import (
    ALLOWED_EXTENSIONS,
    BLOCKED_PATTERNS,
    WORKSPACE_ROOT,
)

# ---------------------------------------------------------------------------
# Workspace safety helpers
# ---------------------------------------------------------------------------

def _resolve_safe(path_str: str) -> Path:
    """
    Resolve *path_str* relative to WORKSPACE_ROOT and verify it stays inside
    the workspace.  Raises ValueError if the path escapes.
    """
    target = (WORKSPACE_ROOT / path_str).resolve()
    try:
        target.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise ValueError(
            f"Path '{path_str}' is outside the workspace '{WORKSPACE_ROOT}'."
        )
    return target


def _is_blocked(path: Path) -> bool:
    name = path.name
    for pattern in BLOCKED_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def _check_extension(path: Path) -> None:
    if path.suffix.lower() not in ALLOWED_EXTENSIONS and path.suffix != "":
        raise ValueError(
            f"File type '{path.suffix}' is not in the allowed list."
        )


# ---------------------------------------------------------------------------
# Tool: read_file
# ---------------------------------------------------------------------------

def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """Return the contents (or a line range) of a file inside the workspace."""
    try:
        target = _resolve_safe(path)
    except ValueError as exc:
        return f"ERROR: {exc}"
    if _is_blocked(target):
        return f"ERROR: '{path}' is blocked for security reasons."
    try:
        _check_extension(target)
    except ValueError as exc:
        return f"ERROR: {exc}"
    if not target.exists():
        return f"ERROR: File '{path}' does not exist."
    if not target.is_file():
        return f"ERROR: '{path}' is not a file."

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    s = max(1, start_line) - 1
    e = total if end_line is None else min(end_line, total)
    selected = lines[s:e]
    numbered = "\n".join(f"{s+i+1:4d} | {l}" for i, l in enumerate(selected))
    return f"File: {path}  (lines {s+1}-{s+len(selected)} of {total})\n{numbered}"


# ---------------------------------------------------------------------------
# Tool: write_file
# ---------------------------------------------------------------------------

def write_file(path: str, content: str) -> str:
    """
    Write *content* to a file inside the workspace.
    This tool should only be invoked **after** the user has approved a diff.
    """
    try:
        target = _resolve_safe(path)
    except ValueError as exc:
        return f"ERROR: {exc}"
    if _is_blocked(target):
        return f"ERROR: '{path}' is blocked for security reasons."
    try:
        _check_extension(target)
    except ValueError as exc:
        return f"ERROR: {exc}"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"OK: Written {len(content)} bytes to '{path}'."


# ---------------------------------------------------------------------------
# Tool: list_files
# ---------------------------------------------------------------------------

def list_files(directory: str = ".", pattern: str = "*") -> str:
    """
    List files matching *pattern* inside *directory* (recursively).
    """
    base = _resolve_safe(directory)
    if not base.exists():
        return f"ERROR: Directory '{directory}' does not exist."
    if not base.is_dir():
        return f"ERROR: '{directory}' is not a directory."

    results: list[str] = []
    for p in sorted(base.rglob(pattern)):
        if p.is_file():
            rel = p.relative_to(WORKSPACE_ROOT)
            results.append(str(rel))

    if not results:
        return f"No files matching '{pattern}' found in '{directory}'."
    return "\n".join(results)


# ---------------------------------------------------------------------------
# Tool: search_code
# ---------------------------------------------------------------------------

def search_code(query: str, directory: str = ".", case_sensitive: bool = False) -> str:
    """
    Grep for *query* in all text files under *directory*.
    Returns matching lines with file paths and line numbers.
    """
    base = _resolve_safe(directory)
    if not base.exists():
        return f"ERROR: Directory '{directory}' does not exist."

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query, flags)
    except re.error as exc:
        return f"ERROR: Invalid regex pattern '{query}': {exc}"
    results: list[str] = []
    limit = 200  # cap output lines

    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        try:
            for lineno, line in enumerate(
                p.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if pattern.search(line):
                    rel = p.relative_to(WORKSPACE_ROOT)
                    results.append(f"{rel}:{lineno}: {line.rstrip()}")
                    if len(results) >= limit:
                        results.append("... (output truncated)")
                        return "\n".join(results)
        except OSError:
            continue

    if not results:
        return f"No matches for '{query}' in '{directory}'."
    return "\n".join(results)


# ---------------------------------------------------------------------------
# Tool: show_diff
# ---------------------------------------------------------------------------

def show_diff(path: str, new_content: str) -> str:
    """
    Generate a unified diff between the current file contents and *new_content*.
    Does NOT modify the file.
    """
    target = _resolve_safe(path)
    if target.exists() and target.is_file():
        old_lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    else:
        old_lines = []

    new_lines = new_content.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    if not diff:
        return "No changes detected."
    return "".join(diff)


# ---------------------------------------------------------------------------
# Tool: run_tests
# ---------------------------------------------------------------------------

def run_tests(path: str = ".", extra_args: str = "") -> str:
    """
    Run pytest on *path* (file or directory) inside the workspace and return
    the combined stdout/stderr output (truncated to 4 000 chars).
    """
    target = _resolve_safe(path)
    cmd = [
        sys.executable, "-m", "pytest",
        str(target),
        "--tb=short",
        "-q",
    ]
    if extra_args:
        cmd += extra_args.split()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(WORKSPACE_ROOT),
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "ERROR: Tests timed out after 120 seconds."
    except FileNotFoundError:
        return "ERROR: pytest not found. Install it with: pip install pytest"

    # Truncate very long output
    if len(output) > 4000:
        output = output[:4000] + "\n... (output truncated)"
    return output if output.strip() else "Tests completed with no output."


# ---------------------------------------------------------------------------
# Tool: run_command
# ---------------------------------------------------------------------------

_ALLOWED_COMMANDS: set[str] = {
    "python", "python3", "pip", "pip3",
    "pytest", "mypy", "flake8", "black", "isort",
    "node", "npm", "npx",
    "git",
    "echo", "type", "cat",
}

def run_command(command: str) -> str:
    """
    Execute a *safe* shell command inside the workspace directory.
    Only white-listed executables are permitted.
    """
    parts = command.strip().split()
    if not parts:
        return "ERROR: Empty command."

    executable = parts[0].lower().rstrip(".exe")
    if executable not in _ALLOWED_COMMANDS:
        return (
            f"ERROR: '{parts[0]}' is not in the allowed command list: "
            + ", ".join(sorted(_ALLOWED_COMMANDS))
        )

    try:
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(WORKSPACE_ROOT),
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "ERROR: Command timed out after 60 seconds."
    except FileNotFoundError:
        return f"ERROR: Executable '{parts[0]}' not found."

    if len(output) > 4000:
        output = output[:4000] + "\n... (output truncated)"
    return output if output.strip() else "(no output)"


# ---------------------------------------------------------------------------
# Tool: detect_bugs
# ---------------------------------------------------------------------------

def detect_bugs(path: str) -> str:
    """
    Run a lightweight static analysis (pyflakes via flake8) on *path* and
    return any issues found.  Falls back to a regex-based heuristic when
    flake8 is not installed.  Reports flake8 as UNAVAILABLE (not an error)
    so the agent loop can surface it gracefully without interrupting the task.
    """
    target = _resolve_safe(path)
    if not target.exists():
        return f"ERROR: '{path}' does not exist."

    _flake8_available = True

    # Try flake8 first
    try:
        result = subprocess.run(
            [sys.executable, "-m", "flake8", str(target), "--max-line-length=120"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(WORKSPACE_ROOT),
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0 and ("No module named flake8" in output or "not found" in output.lower()):
            _flake8_available = False  # fall through to built-in heuristic
        elif output:
            return output
        else:
            return f"No issues found by flake8 in '{path}'."
    except FileNotFoundError:
        _flake8_available = False  # fall through to built-in heuristic

    # Heuristic regex checks for Python files
    if target.suffix != ".py":
        return (
            "UNAVAILABLE: flake8 is not installed and built-in heuristics "
            "only support .py files. Install with: pip install flake8"
        )

    issues: list[str] = []
    source = target.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()

    checks = [
        (r"\bexcept\s*:", "Bare 'except:' catches all exceptions including SystemExit."),
        (r"\bprint\s*\(", "print() found – consider using logging in production code."),
        (r"TODO|FIXME|HACK|XXX", "Unresolved TODO/FIXME/HACK/XXX marker."),
        (r"\beval\s*\(", "eval() is a security risk."),
        (r"\bexec\s*\(", "exec() is a security risk."),
        (r"password\s*=\s*['\"][^'\"]+['\"]", "Hardcoded password detected."),
    ]

    for lineno, line in enumerate(lines, 1):
        for pattern, msg in checks:
            if re.search(pattern, line, re.IGNORECASE):
                issues.append(f"{path}:{lineno}: {msg}  |  {line.strip()}")

    return "\n".join(issues) if issues else f"No obvious issues found in '{path}'."


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, Callable] = {
    "read_file":    read_file,
    "write_file":   write_file,
    "list_files":   list_files,
    "search_code":  search_code,
    "show_diff":    show_diff,
    "run_tests":    run_tests,
    "run_command":  run_command,
    "detect_bugs":  detect_bugs,
}

# Human-readable descriptions used in the system prompt
TOOL_DESCRIPTIONS: str = textwrap.dedent("""
Available tools (call using the exact JSON format shown):

1. read_file(path, start_line?, end_line?)
   – Read a file (optionally a line range) from the workspace.
   Example: {"tool": "read_file", "args": {"path": "agent/config.py"}}

2. write_file(path, content)
   – Write content to a file. The system will automatically show a diff and ask for user approval before writing.
   Example: {"tool": "write_file", "args": {"path": "foo.py", "content": "..."}}

3. list_files(directory?, pattern?)
   – List files in a directory (supports glob patterns).
   Example: {"tool": "list_files", "args": {"directory": ".", "pattern": "*.py"}}

4. search_code(query, directory?, case_sensitive?)
   – Search for a regex pattern across all workspace files.
   Example: {"tool": "search_code", "args": {"query": "def main"}}

5. show_diff(path, new_content)
   – Show a unified diff between the current file and new_content.
   Example: {"tool": "show_diff", "args": {"path": "foo.py", "new_content": "..."}}

6. run_tests(path?, extra_args?)
   – Run pytest on a file or directory.
   Example: {"tool": "run_tests", "args": {"path": "tests/"}}

7. run_command(command)
   – Execute a safe, white-listed shell command inside the workspace.
   Example: {"tool": "run_command", "args": {"command": "python --version"}}

8. detect_bugs(path)
   – Run static analysis on a file and report issues.
   Example: {"tool": "detect_bugs", "args": {"path": "agent/tools.py"}}
""").strip()
