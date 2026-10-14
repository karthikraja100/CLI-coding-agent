"""
Core agent loop – strict ReAct protocol (Reason → Act → Observe).

Improvements in v2:
- Strict parser: only executes tool calls from a ```json fence that starts
  on its own line and contains ONLY a tool-call object. Code blocks embedded
  inside prose (``` without json, or inline JSON) are ignored.
- Missing-dependency guard: "not found" / "ModuleNotFound" errors are
  surfaced as informational messages, not hard failures.
- Consecutive-error limit: after 2 errors on the same tool, the loop
  forces a FINAL_ANSWER instead of looping indefinitely.
- Clear log separator between tool output and model explanation.
- Malformed-JSON responses are reported and the loop continues as
  FINAL_ANSWER rather than crashing.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Callable

from agent.config import MAX_AGENT_ITERATIONS, Color
from agent.ollama_client import OllamaClient, OllamaError
from agent.prompt import build_messages
from agent.tools import TOOL_REGISTRY, show_diff

# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

# Match a ```json block that contains ONLY a tool-call object.
# The block must not have prose before the opening ```.
_JSON_FENCE = re.compile(
    r"^\s*```json\s*\n(\{.*?\})\s*\n```",
    re.DOTALL | re.MULTILINE,
)

# Detect optional-tool "not found" errors so we can handle them gracefully.
_MISSING_DEP_PATTERNS = re.compile(
    r"(No module named|not found|ModuleNotFoundError|command not found|"
    r"is not recognized|cannot find|ERROR:.*not found)",
    re.IGNORECASE,
)


def _parse_tool_call(text: str) -> dict | None:
    """
    Return the first valid TOOL_CALL dict from *text*, or None.

    A valid tool-call block:
    - Opens with ```json at the start of a line (possibly preceded by spaces).
    - Contains exactly one JSON object with "tool" and "args" keys.
    - Is NOT embedded inside a larger prose paragraph.
    """
    match = _JSON_FENCE.search(text)
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "tool" not in obj or "args" not in obj:
        return None
    # Reject if the "tool" value is not a known tool name
    if obj["tool"] not in TOOL_REGISTRY:
        return None
    return obj


def _is_missing_dep_error(observation: str) -> bool:
    return bool(_MISSING_DEP_PATTERNS.search(observation))


def _print_colored(text: str, color: str, bold: bool = False) -> None:
    prefix = Color.BOLD if bold else ""
    msg = f"{prefix}{color}{text}{Color.RESET}"
    try:
        print(msg)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", "ascii") or "ascii"
        safe_msg = msg.encode(encoding, errors="replace").decode(encoding)
        print(safe_msg)


def _print_tool_log(tool_name: str, observation: str) -> None:
    """Print a clearly separated tool log block."""
    sep = "─" * 56
    _print_colored(f"\n{sep}", Color.DIM)
    _print_colored(f"[TOOL OUTPUT: {tool_name}]", Color.CYAN, bold=True)
    print(observation)
    _print_colored(sep + "\n", Color.DIM)


# ---------------------------------------------------------------------------
# AgentLoop
# ---------------------------------------------------------------------------

class AgentLoop:
    """
    Strict ReAct agent loop.

    Parameters
    ----------
    client       : configured OllamaClient
    approval_fn  : callable(prompt) -> bool  – asks the user for yes/no
    output_fn    : callable(text)            – streams model tokens to UI
    """

    def __init__(
        self,
        client: OllamaClient,
        approval_fn: Callable[[str], bool] | None = None,
        output_fn: Callable[[str], None] | None = None,
    ) -> None:
        self.client = client
        self.history: list[dict] = []
        self._approval_fn = approval_fn or self._default_approval
        self._output_fn = output_fn or (lambda t: print(t, end="", flush=True))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, user_input: str) -> str:
        """
        Process one user turn through the agent loop.
        Returns the final textual answer.
        """
        self.history.append({"role": "user", "content": user_input})

        consecutive_errors: int = 0
        last_error_tool: str = ""

        for iteration in range(MAX_AGENT_ITERATIONS):
            messages = build_messages(
                self.history[:-1], self.history[-1]["content"]
            )

            # ── Stream model response ──────────────────────────────────
            response_parts: list[str] = []
            try:
                for token in self.client.chat_stream(messages):
                    self._output_fn(token)
                    response_parts.append(token)
            except OllamaError as exc:
                err = f"\n{Color.RED}Ollama error: {exc}{Color.RESET}\n"
                print(err)
                return str(exc)

            response_text = "".join(response_parts)
            print()  # newline after stream

            # ── Parse tool call ────────────────────────────────────────
            tool_call = _parse_tool_call(response_text)

            if tool_call is None:
                # STATE 2: FINAL_ANSWER — no valid tool-call block found
                self.history.append(
                    {"role": "assistant", "content": response_text}
                )
                return response_text

            tool_name: str = tool_call["tool"]
            tool_args: dict = tool_call.get("args", {})

            # ── Consecutive-error guard ────────────────────────────────
            if consecutive_errors >= 2 and tool_name == last_error_tool:
                notice = (
                    f"Skipping '{tool_name}' after 2 consecutive errors. "
                    "Please answer with the information already available."
                )
                _print_colored(f"\n[Agent] {notice}\n", Color.YELLOW)
                self.history.append(
                    {"role": "assistant", "content": response_text}
                )
                self.history.append(
                    {"role": "user", "content": f"[system] {notice}"}
                )
                consecutive_errors = 0
                continue

            # ── write_file: show diff + require approval ───────────────
            if tool_name == "write_file":
                approved = self._handle_write_approval(tool_args)
                if approved is None:
                    # Content already matches on disk – no change needed, conclude
                    path_str = tool_args.get("path", "")
                    final_ans = f"Done. '{path_str}' has been saved and is up to date."
                    self.history.append({"role": "assistant", "content": final_ans})
                    _print_colored(f"\n{final_ans}\n", Color.GREEN)
                    return final_ans

                if not approved:
                    observation = "User declined the file edit."
                    _print_colored(
                        f"\n[{tool_name}] → {observation}", Color.YELLOW
                    )
                    self.history.append(
                        {"role": "assistant", "content": response_text}
                    )
                    self.history.append(
                        {
                            "role": "user",
                            "content": f"[observation]\n{observation}",
                        }
                    )
                    continue

            # ── Execute tool ───────────────────────────────────────────
            fn = TOOL_REGISTRY.get(tool_name)
            if fn is None:
                observation = f"ERROR: Unknown tool '{tool_name}'."
            else:
                try:
                    observation = fn(**tool_args)
                except TypeError as exc:
                    observation = f"ERROR: Bad tool arguments – {exc}"
                except ValueError as exc:
                    observation = f"ERROR: {exc}"
                except Exception as exc:  # noqa: BLE001
                    observation = f"ERROR: Unexpected – {exc}"

            # ── Handle missing-dependency errors gracefully ────────────
            if observation.startswith("ERROR:") and _is_missing_dep_error(observation):
                # Extract the tool/package name for a helpful message
                observation = (
                    f"UNAVAILABLE: The tool '{tool_name}' requires a dependency "
                    f"that is not installed.\n"
                    f"Details: {observation}\n"
                    f"To install it, run the appropriate pip command "
                    f"(ask the user for approval first). "
                    f"Continue the task without this tool."
                )

            # ── Track consecutive errors on same tool ──────────────────
            if observation.startswith("ERROR:") or observation.startswith("UNAVAILABLE:"):
                if tool_name == last_error_tool:
                    consecutive_errors += 1
                else:
                    consecutive_errors = 1
                    last_error_tool = tool_name
            else:
                consecutive_errors = 0
                last_error_tool = ""

            # If the user requested to edit/fix/write a file, guide the model to call write_file
            obs_content = observation
            prompt_lower = user_input.lower()
            action_words = ("fix", "write", "update", "modify", "save", "change", "replace", "edit")
            if tool_name in ("read_file", "run_tests") and any(w in prompt_lower for w in action_words):
                target_path = tool_args.get("path", "")
                if target_path and "." in target_path and not target_path.startswith("tests/"):
                    obs_content += f"\n\n[system] Now call write_file with the full updated content for '{target_path}' to apply your fix."

            _print_tool_log(tool_name, observation)

            # Append assistant message and observation to history
            self.history.append(
                {"role": "assistant", "content": response_text}
            )
            self.history.append(
                {"role": "user", "content": f"[observation]\n{obs_content}"}
            )

        # Exceeded max iterations
        final = (
            "Agent reached the maximum number of iterations. "
            "Please rephrase your request or use /reset."
        )
        self.history.append({"role": "assistant", "content": final})
        return final

    def reset(self) -> None:
        """Clear conversation history."""
        self.history.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_write_approval(self, args: dict) -> bool:
        """Show a unified diff and prompt the user to approve or reject."""
        path = args.get("path", "")
        content = args.get("content", "")
        diff_output = show_diff(path, content)

        if diff_output == "No changes detected.":
            _print_colored(
                f"\n[write_file] '{path}': No changes detected (content is already up to date).",
                Color.DIM,
            )
            return None

        _print_colored(
            "\n─── Proposed file change ───────────────────────────────",
            Color.CYAN,
            bold=True,
        )
        _print_colored(diff_output, "")
        _print_colored(
            "────────────────────────────────────────────────────────",
            Color.CYAN,
        )
        return self._approval_fn("Apply this change?")

    @staticmethod
    def _default_approval(prompt: str) -> bool:
        while True:
            ans = (
                input(f"{Color.YELLOW}{prompt} [y/N]: {Color.RESET}")
                .strip()
                .lower()
            )
            if ans in ("y", "yes"):
                return True
            if ans in ("", "n", "no"):
                return False
