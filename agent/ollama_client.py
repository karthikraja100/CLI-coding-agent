"""
Ollama LLM client – thin wrapper around the Ollama REST API.
No third-party SDK required; uses only the stdlib `urllib` and `json`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterator

from agent.config import (
    MAX_TOKENS,
    MODEL_NAME,
    NUM_CTX,
    OLLAMA_BASE_URL,
    TEMPERATURE,
)


class OllamaError(RuntimeError):
    """Raised when the Ollama API returns an error."""


class OllamaClient:
    """Minimal Ollama REST client (no extra dependencies)."""

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = MODEL_NAME,
        temperature: float = TEMPERATURE,
        num_ctx: int = NUM_CTX,
        max_tokens: int = MAX_TOKENS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.max_tokens = max_tokens

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if Ollama is reachable and the model is pulled."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            # Accept both "qwen2.5-coder:3b" and "qwen2.5-coder:3b-..."
            return any(m.startswith(self.model.split(":")[0]) for m in models)
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Return names of all locally available models."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Chat (non-streaming)
    # ------------------------------------------------------------------

    def chat(self, messages: list[dict], stream: bool = False) -> str:
        """
        Send *messages* to the /api/chat endpoint and return the assistant
        response as a plain string.

        Parameters
        ----------
        messages : list of {"role": str, "content": str}
        stream   : if True, uses streaming and yields tokens; otherwise
                   blocks until the full response arrives.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": self.max_tokens,
            },
        }
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                if stream:
                    return self._collect_stream(resp)
                raw = resp.read()
                data = json.loads(raw)
                return data["message"]["content"]
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace")
            raise OllamaError(f"HTTP {exc.code}: {body_text}") from exc
        except urllib.error.URLError as exc:
            raise OllamaError(
                f"Cannot connect to Ollama at {self.base_url}: {exc.reason}"
            ) from exc

    def _collect_stream(self, resp) -> str:
        """Accumulate streamed JSON-lines into a single string."""
        parts: list[str] = []
        for raw_line in resp:
            line = raw_line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = chunk.get("message", {}).get("content", "")
            if content:
                parts.append(content)
            if chunk.get("done"):
                break
        return "".join(parts)

    # ------------------------------------------------------------------
    # Streaming generator (used by the CLI for live output)
    # ------------------------------------------------------------------

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        """Yield tokens one by one as the model generates them."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": self.max_tokens,
            },
        }
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                for raw_line in resp:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if chunk.get("done"):
                        break
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace")
            raise OllamaError(f"HTTP {exc.code}: {body_text}") from exc
        except urllib.error.URLError as exc:
            raise OllamaError(
                f"Cannot connect to Ollama at {self.base_url}: {exc.reason}"
            ) from exc
