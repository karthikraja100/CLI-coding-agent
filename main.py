"""
CLI entry point for the AI Coding Agent.

Usage:
    python main.py [--workspace PATH] [--model MODEL]

Slash commands:
    /help    – show this help
    /status  – show model / workspace info
    /test    – run all tests in the workspace
    /reset   – clear conversation history
    /exit    – quit
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: make sure the project root is on sys.path when run directly
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from agent.config import Color, MODEL_NAME, OLLAMA_BASE_URL, WORKSPACE_ROOT
from agent.loop import AgentLoop
from agent.ollama_client import OllamaClient, OllamaError
from agent.tools import run_tests


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

BANNER = f"""\
{Color.CYAN}{Color.BOLD}
 ╔══════════════════════════════════════════════╗
 ║        AI  Coding  Agent  CLI  v1.0          ║
 ║   Powered by Ollama · {MODEL_NAME:<22}║
 ╚══════════════════════════════════════════════╝
{Color.RESET}"""

HELP_TEXT = f"""\
{Color.BOLD}Slash commands:{Color.RESET}
  {Color.GREEN}/help{Color.RESET}            Show this help message
  {Color.GREEN}/status{Color.RESET}          Show model, workspace, and connection info
  {Color.GREEN}/test [path]{Color.RESET}     Run pytest on a path (default: whole workspace)
  {Color.GREEN}/reset{Color.RESET}           Clear conversation history
  {Color.GREEN}/exit{Color.RESET}            Quit the agent

{Color.BOLD}Examples:{Color.RESET}
  > Generate a Python function to reverse a linked list
  > Read the file agent/tools.py and explain what it does
  > Search for all usages of 'write_file' in the workspace
  > Detect bugs in agent/loop.py
  > Fix the bug you found and show me a diff before applying it
"""


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _print(text: str, color: str = "", bold: bool = False) -> None:
    prefix = (Color.BOLD if bold else "") + color
    print(f"{prefix}{text}{Color.RESET}")


def _print_separator() -> None:
    print(f"{Color.DIM}{'─' * 56}{Color.RESET}")


def _handle_slash_command(cmd: str, agent: AgentLoop, client: OllamaClient) -> bool:
    """
    Handle a /command.  Returns True if handled, False if unknown.
    """
    parts = cmd.strip().split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if command == "/help":
        print(HELP_TEXT)
        return True

    if command == "/status":
        available = client.is_available()
        models = client.list_models()
        status_color = Color.GREEN if available else Color.RED
        _print(f"\n  Model     : {client.model}", Color.CYAN)
        _print(f"  Endpoint  : {client.base_url}", Color.CYAN)
        _print(f"  Available : {'✓ Yes' if available else '✗ No'}", status_color)
        _print(f"  Workspace : {WORKSPACE_ROOT}", Color.CYAN)
        if models:
            _print(f"  Models    : {', '.join(models)}", Color.DIM)
        _print(f"  History   : {len(agent.history)} messages\n", Color.DIM)
        return True

    if command == "/test":
        path = arg.strip() or "."
        _print(f"\n  Running tests on: {path}\n", Color.YELLOW, bold=True)
        result = run_tests(path)
        print(result)
        print()
        return True

    if command == "/reset":
        agent.reset()
        _print("\n  Conversation history cleared.\n", Color.GREEN)
        return True

    if command in ("/exit", "/quit", "/q"):
        _print("\n  Goodbye! 👋\n", Color.CYAN, bold=True)
        sys.exit(0)

    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Coding Agent CLI – powered by Ollama",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workspace", "-w",
        default=str(WORKSPACE_ROOT),
        help=f"Root workspace directory (default: {WORKSPACE_ROOT})",
    )
    parser.add_argument(
        "--model", "-m",
        default=MODEL_NAME,
        help=f"Ollama model to use (default: {MODEL_NAME})",
    )
    parser.add_argument(
        "--base-url",
        default=OLLAMA_BASE_URL,
        help=f"Ollama base URL (default: {OLLAMA_BASE_URL})",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colour output",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Apply workspace override via environment variable so tools.py picks it up
    os.environ["AGENT_WORKSPACE"] = str(Path(args.workspace).resolve())

    # Disable colour if requested or if stdout is not a TTY
    if args.no_color or not sys.stdout.isatty():
        for attr in vars(Color):
            if not attr.startswith("_"):
                setattr(Color, attr, "")

    # Create client
    client = OllamaClient(
        base_url=args.base_url,
        model=args.model,
    )

    # Print banner
    print(BANNER)

    # Check Ollama availability
    if not client.is_available():
        _print(
            f"  ⚠  Ollama is not available at {args.base_url}.\n"
            f"     Make sure Ollama is running and '{args.model}' is pulled:\n\n"
            f"       ollama serve\n"
            f"       ollama pull {args.model}\n",
            Color.YELLOW,
        )
        # We still start the CLI; tools work, but LLM calls will fail gracefully.
    else:
        _print(f"  ✓  Connected to Ollama ({args.model})\n", Color.GREEN)

    _print(f"  Workspace : {WORKSPACE_ROOT}", Color.DIM)
    _print(f"  Type /help for available commands.\n", Color.DIM)
    _print_separator()

    # Token streaming callback – writes directly to stdout
    def stream_token(token: str) -> None:
        print(token, end="", flush=True)

    # Create agent loop
    agent = AgentLoop(client=client, output_fn=stream_token)

    # REPL
    while True:
        try:
            _print_separator()
            user_input = input(f"\n{Color.BOLD}{Color.BLUE}You › {Color.RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            _print("\n\n  Interrupted. Use /exit to quit.\n", Color.YELLOW)
            continue

        if not user_input:
            continue

        # Handle slash commands
        if user_input.startswith("/"):
            handled = _handle_slash_command(user_input, agent, client)
            if not handled:
                _print(
                    f"  Unknown command: '{user_input}'.  Type /help for help.\n",
                    Color.RED,
                )
            continue

        # Regular agent turn
        _print(f"\n{Color.MAGENTA}Agent › {Color.RESET}", bold=False)
        try:
            agent.run(user_input)
        except OllamaError as exc:
            _print(f"\n  LLM error: {exc}\n", Color.RED)
        except KeyboardInterrupt:
            _print("\n  Generation interrupted.\n", Color.YELLOW)
        print()


if __name__ == "__main__":
    main()
