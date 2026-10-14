"""
Configuration and constants for the AI Coding Agent CLI.
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Ollama / Model settings (optimised for 8 GB RAM)
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL_NAME: str = os.getenv("AGENT_MODEL", "qwen2.5-coder:3b")

# Keep context small to stay well within 8 GB VRAM/RAM
MAX_TOKENS: int = 2048
TEMPERATURE: float = 0.2
NUM_CTX: int = 4096          # context window passed to Ollama

# ---------------------------------------------------------------------------
# Agent behaviour
# ---------------------------------------------------------------------------
MAX_AGENT_ITERATIONS: int = 20   # guard against infinite loops

# ---------------------------------------------------------------------------
# Safe workspace
# ---------------------------------------------------------------------------
# If the environment variable AGENT_WORKSPACE is set, use it; otherwise fall
# back to the current working directory when the CLI is launched.
WORKSPACE_ROOT: Path = Path(
    os.getenv("AGENT_WORKSPACE", os.getcwd())
).resolve()

# Allowed file extensions for reading/editing
ALLOWED_EXTENSIONS: set[str] = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".html", ".css", ".md", ".txt", ".json",
    ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".sh", ".bat", ".ps1", ".sql", ".env",
    ".c", ".cpp", ".h", ".hpp", ".rs", ".go",
    ".java", ".rb", ".php",
}

# Files / patterns that must never be read or written
BLOCKED_PATTERNS: list[str] = [
    ".env",
    "*.secret",
    "*.key",
    "*.pem",
    "id_rsa",
    "id_ed25519",
]

# ---------------------------------------------------------------------------
# CLI colours (ANSI)
# ---------------------------------------------------------------------------
class Color:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    DIM     = "\033[2m"
