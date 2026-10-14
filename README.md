# AI Coding Agent CLI

A lightweight, modular **AI coding assistant** that runs entirely on your local machine using [Ollama](https://ollama.com) and the `qwen2.5-coder:3b` model — optimised for **8 GB RAM**.

---

## Features

| Capability | Details |
|---|---|
| **Code generation & explanation** | Ask in plain English; get working code with explanations |
| **File inspection & search** | Read files, list directories, regex-search across all workspace files |
| **Bug detection** | Static analysis via flake8 (or built-in heuristics) |
| **Diff & approval workflow** | Agent shows a unified diff; file is only written after you type `y` |
| **Test runner** | Runs pytest and feeds error output back to the model for self-correction |
| **Safe command execution** | Only white-listed executables (`python`, `git`, `pytest`, …) |
| **Workspace isolation** | All file access is restricted to a single root directory |
| **Manual ReAct loop** | Validated JSON tool calls; model cannot escape the loop |
| **No external SDK** | Pure stdlib + pytest; zero mandatory pip packages beyond pytest |

---

## Project Layout

```
CLI/
├── agent/
│   ├── __init__.py
│   ├── config.py          ← constants, Color helpers
│   ├── ollama_client.py   ← thin Ollama REST wrapper (stdlib only)
│   ├── prompt.py          ← system prompt & message builder
│   ├── loop.py            ← ReAct agent loop
│   └── tools.py           ← all tool implementations
├── tests/
│   ├── __init__.py
│   └── test_tools.py      ← 35 offline unit tests
├── main.py                ← CLI entry point
├── pytest.ini
├── requirements.txt
└── README.md
```

---

## Windows Setup

### 1 — Prerequisites

| Requirement | Notes |
|---|---|
| **Python ≥ 3.11** | [python.org/downloads](https://www.python.org/downloads/) |
| **Ollama** | [ollama.com/download](https://ollama.com/download) |
| **8 GB RAM** | `qwen2.5-coder:3b` uses ~2.5 GB VRAM/RAM |

### 2 — Install Ollama and pull the model

```powershell
# After installing Ollama (the installer sets it up as a Windows service)
ollama pull qwen2.5-coder:3b
```

Verify Ollama is running:

```powershell
ollama list
# Should show: qwen2.5-coder:3b
```

### 3 — Clone / download this project

```powershell
# Example – adjust path to wherever you saved the project
cd C:\Users\YourName\Downloads\CLI
```

### 4 — (Optional) Create a virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

> **Tip:** If PowerShell blocks script execution, run:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

### 5 — Install dependencies

```powershell
pip install -r requirements.txt

# Optional extras (for richer bug detection / formatting)
pip install flake8 black isort
```

### 6 — Run the agent

```powershell
# Use current directory as workspace (default)
python main.py

# Or specify a workspace explicitly
python main.py --workspace C:\Projects\my-project

# Use a different model
python main.py --model qwen2.5-coder:7b

# Disable ANSI colours (for CI or non-TTY terminals)
python main.py --no-color
```

---

## CLI Commands

| Command | Description |
|---|---|
| `/help` | Show available commands |
| `/status` | Display model, workspace, connection info, and history length |
| `/test [path]` | Run pytest on a path (default: whole workspace) |
| `/reset` | Clear conversation history |
| `/exit` | Quit |

---

## Example Session

```
You › Read the file agent/tools.py and tell me what tools are available

Agent › [reads file, lists tool names with short descriptions]

You › Detect bugs in agent/tools.py

Agent › [runs static analysis, reports issues]

You › Fix the bare-except issue and show me a diff

Agent › [shows unified diff]

─── Proposed file change ───
--- a/agent/tools.py
+++ b/agent/tools.py
@@ ... @@
-    except:
+    except Exception:
────────────────────────────
Apply this change? [y/N]: y

Agent › Done. The bare except has been replaced.

You › /test tests/
      [runs pytest, shows results]
```

---

## Running Tests

```powershell
python -m pytest tests/ -v
```

All 35 tests run **fully offline** (no Ollama server needed):

```
35 passed in 6.82s
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AGENT_WORKSPACE` | current working directory | Workspace root directory |
| `AGENT_MODEL` | `qwen2.5-coder:3b` | Ollama model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |

---

## Memory Optimisation (8 GB RAM)

| Setting | Value | Reason |
|---|---|---|
| `NUM_CTX` | 4096 tokens | Keeps KV-cache small |
| `MAX_TOKENS` | 2048 tokens | Caps single-response size |
| `TEMPERATURE` | 0.2 | Deterministic, less sampling overhead |
| Model | `qwen2.5-coder:3b` | ~2.5 GB RAM; leaves headroom for OS |

---

## Security Model

- **Workspace isolation** – all paths are resolved and checked against `WORKSPACE_ROOT` before any I/O.
- **Blocked patterns** – files matching `.env`, `*.key`, `*.pem`, `id_rsa`, etc. are always refused.
- **Allowed extensions** – only recognised text/code extensions can be read or written.
- **Command whitelist** – `run_command` only allows: `python`, `pip`, `pytest`, `git`, `black`, `mypy`, `flake8`, `isort`, `node`, `npm`, `npx`, `echo`, `cat`.
- **Diff-then-approve** – `write_file` always shows a diff and requires `y` from the user.

---

## Architecture

```
main.py (CLI REPL)
  │
  ├── AgentLoop (loop.py)
  │     ├── OllamaClient  ← streams tokens via /api/chat
  │     ├── parse_tool_call() ← extracts ```json``` blocks
  │     └── TOOL_REGISTRY  ← dispatches to tool functions
  │
  └── agent/
        ├── config.py   – constants & colour codes
        ├── prompt.py   – system prompt + message builder
        ├── tools.py    – 8 tool functions
        └── ollama_client.py – stdlib HTTP wrapper
```

The agent uses a **ReAct (Reason → Act → Observe)** loop:

1. Model emits a response (streamed token-by-token).
2. If a `\`\`\`json { "tool": ..., "args": ... }\`\`\`` block is detected, the tool is called.
3. The observation is appended to history and the model is called again.
4. When no tool call is present the response is the final answer.
5. `write_file` is intercepted: diff is shown, user must approve.

---

## Licence

MIT
