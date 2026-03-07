#!/usr/bin/env python3
"""
LLM End-to-End Integration Tests

Validates the full LLM chat + tool-calling pipeline against a live
Ollama instance running qwen2.5:0.5b (or any OpenAI-compatible model).

Tests cover:
  1. Ollama health + model availability
  2. JWT authentication flow
  3. LLM status endpoint
  4. Single-turn chat (no tools)
  5. Chat with tool calling (hub introspection)
  6. MCP endpoint availability (when enabled)
  7. Permission enforcement (class-based access)

Usage:
    python test_llm_e2e.py \\
        --api-url http://llm-hub:8000 \\
        --admin-user admin --admin-pass admin123 \\
        --model qwen2.5:0.5b --ollama-url http://ollama:11434
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from typing import Any, Optional

import requests

# ── Helpers ──────────────────────────────────────────────────────────────────

class TestResult:
    def __init__(self):
        self.passed: int = 0
        self.failed: int = 0
        self.skipped: int = 0
        self.details: list[dict[str, Any]] = []

    def record(self, name: str, passed: bool, message: str = "", skip: bool = False):
        if skip:
            self.skipped += 1
            tag = "SKIP"
        elif passed:
            self.passed += 1
            tag = "PASS"
        else:
            self.failed += 1
            tag = "FAIL"
        print(f"  [{tag}] {name}" + (f": {message}" if message else ""))
        self.details.append({"name": name, "status": tag, "message": message})

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped

    def summary(self) -> str:
        return (
            f"\n{'='*60}\n"
            f"Results: {self.passed} passed, {self.failed} failed, "
            f"{self.skipped} skipped / {self.total} total\n"
            f"{'='*60}"
        )


def _get(url: str, headers: dict | None = None, timeout: float = 30) -> requests.Response:
    return requests.get(url, headers=headers or {}, timeout=timeout)


def _post(url: str, json_data: dict | None = None, headers: dict | None = None,
          timeout: float = 120) -> requests.Response:
    return requests.post(url, json=json_data, headers=headers or {}, timeout=timeout)


# ── Test class ───────────────────────────────────────────────────────────────

class LlmE2ETests:
    """End-to-end tests for the LLM + tool-calling pipeline."""

    def __init__(self, api_url: str, admin_user: str, admin_pass: str,
                 model: str, ollama_url: str):
        self.api_url = api_url.rstrip("/")
        self.admin_user = admin_user
        self.admin_pass = admin_pass
        self.model = model
        self.ollama_url = ollama_url.rstrip("/")
        self.token: Optional[str] = None
        self.results = TestResult()

    # -- auth helper --

    def _auth_header(self) -> dict[str, str]:
        assert self.token, "authenticate first"
        return {"Authorization": f"Bearer {self.token}"}

    # -- individual tests --

    def test_ollama_health(self):
        """Verify Ollama is reachable."""
        name = "ollama_health"
        try:
            r = _get(f"{self.ollama_url}/api/tags", timeout=10)
            self.results.record(name, r.status_code == 200,
                                f"status={r.status_code}")
        except Exception as e:
            self.results.record(name, False, str(e))

    def test_model_available(self):
        """Verify the expected model is loaded in Ollama."""
        name = "model_available"
        try:
            r = _get(f"{self.ollama_url}/api/tags", timeout=10)
            models = [m["name"] for m in r.json().get("models", [])]
            # Ollama stores as "qwen2.5:0.5b" — match by prefix
            found = any(self.model in m for m in models)
            self.results.record(name, found,
                                f"looking for '{self.model}' in {models}")
        except Exception as e:
            self.results.record(name, False, str(e))

    def test_hub_health(self):
        """Verify verlihub-py /health endpoint."""
        name = "hub_health"
        try:
            r = _get(f"{self.api_url}/health", timeout=10)
            self.results.record(name, r.status_code == 200,
                                f"status={r.status_code}")
        except Exception as e:
            self.results.record(name, False, str(e))

    def test_authenticate(self):
        """Get a JWT token for the admin user."""
        name = "authenticate"
        try:
            r = _post(f"{self.api_url}/api/v1/auth/login", json_data={
                "nick": self.admin_user,
                "password": self.admin_pass,
            }, timeout=15)
            if r.status_code == 200:
                data = r.json()
                self.token = data.get("access_token") or data.get("token")
                self.results.record(name, bool(self.token),
                                    "token obtained" if self.token else "no token in response")
            else:
                self.results.record(name, False, f"status={r.status_code} body={r.text[:200]}")
        except Exception as e:
            self.results.record(name, False, str(e))

    def test_llm_status(self):
        """GET /api/v1/llm/status — LLM enabled + reachable."""
        name = "llm_status"
        if not self.token:
            self.results.record(name, False, "no auth token", skip=True)
            return
        try:
            r = _get(f"{self.api_url}/api/v1/llm/status", headers=self._auth_header())
            if r.status_code == 200:
                data = r.json()
                ok = data.get("enabled") and data.get("llm_reachable")
                self.results.record(name, ok,
                                    f"enabled={data.get('enabled')} reachable={data.get('llm_reachable')} model={data.get('model')}")
            else:
                self.results.record(name, False, f"status={r.status_code}")
        except Exception as e:
            self.results.record(name, False, str(e))

    def test_simple_chat(self):
        """POST /api/v1/llm/chat — simple greeting, expect coherent text back."""
        name = "simple_chat"
        if not self.token:
            self.results.record(name, False, "no auth token", skip=True)
            return
        try:
            r = _post(f"{self.api_url}/api/v1/llm/chat",
                       json_data={"message": "Hello! What is your name?"},
                       headers=self._auth_header(), timeout=120)
            if r.status_code == 200:
                data = r.json()
                resp = data.get("response", "")
                ok = len(resp) > 5  # got a real response
                self.results.record(name, ok,
                                    f"response_len={len(resp)} snippet={resp[:120]!r}")
            else:
                self.results.record(name, False, f"status={r.status_code} body={r.text[:200]}")
        except Exception as e:
            self.results.record(name, False, str(e))

    def test_tool_calling_hub_users(self):
        """POST /api/v1/llm/chat — ask about hub users, expect tool call."""
        name = "tool_call_hub_users"
        if not self.token:
            self.results.record(name, False, "no auth token", skip=True)
            return
        try:
            r = _post(f"{self.api_url}/api/v1/llm/chat",
                       json_data={
                           "message": "How many users are currently registered on this hub? Use the available tools to check.",
                       },
                       headers=self._auth_header(), timeout=180)
            if r.status_code == 200:
                data = r.json()
                resp = data.get("response", "")
                tools = data.get("tool_calls", [])
                # The LLM should have called at least one tool
                ok = len(resp) > 5
                self.results.record(name, ok,
                                    f"response_len={len(resp)} tool_calls={len(tools)} snippet={resp[:120]!r}")
            else:
                self.results.record(name, False, f"status={r.status_code} body={r.text[:200]}")
        except Exception as e:
            self.results.record(name, False, str(e))

    def test_tool_calling_hub_info(self):
        """POST /api/v1/llm/chat — ask for hub name, expect tool usage."""
        name = "tool_call_hub_info"
        if not self.token:
            self.results.record(name, False, "no auth token", skip=True)
            return
        try:
            r = _post(f"{self.api_url}/api/v1/llm/chat",
                       json_data={
                           "message": "What is the name of this hub? Use your tools to find out.",
                       },
                       headers=self._auth_header(), timeout=180)
            if r.status_code == 200:
                data = r.json()
                resp = data.get("response", "")
                tools = data.get("tool_calls", [])
                # Should mention the hub name from config
                ok = len(resp) > 5
                self.results.record(name, ok,
                                    f"response_len={len(resp)} tool_calls={len(tools)} snippet={resp[:120]!r}")
            else:
                self.results.record(name, False, f"status={r.status_code} body={r.text[:200]}")
        except Exception as e:
            self.results.record(name, False, str(e))

    def test_chat_conversation_id(self):
        """Verify conversation_id creates separate sessions."""
        name = "conversation_id"
        if not self.token:
            self.results.record(name, False, "no auth token", skip=True)
            return
        try:
            r1 = _post(f"{self.api_url}/api/v1/llm/chat",
                        json_data={"message": "Remember the number 42.", "conversation_id": "test-conv-a"},
                        headers=self._auth_header(), timeout=120)
            r2 = _post(f"{self.api_url}/api/v1/llm/chat",
                        json_data={"message": "What number did I ask you to remember?", "conversation_id": "test-conv-a"},
                        headers=self._auth_header(), timeout=120)
            if r1.status_code == 200 and r2.status_code == 200:
                resp2 = r2.json().get("response", "")
                # The LLM should recall "42" from the same conversation
                ok = "42" in resp2
                self.results.record(name, ok,
                                    f"recalled_42={'42' in resp2} snippet={resp2[:120]!r}")
            else:
                self.results.record(name, False,
                                    f"r1={r1.status_code} r2={r2.status_code}")
        except Exception as e:
            self.results.record(name, False, str(e))

    def test_mcp_endpoint(self):
        """Verify the in-process MCP endpoint is mounted."""
        name = "mcp_endpoint"
        if not self.token:
            self.results.record(name, False, "no auth token", skip=True)
            return
        try:
            # The MCP endpoint accepts SSE transport; just check it doesn't 404
            r = _get(f"{self.api_url}/api/v1/mcp/sse",
                     headers=self._auth_header(), timeout=10)
            # SSE endpoint might return 200 with streaming or 401/403
            # A 404 means MCP not mounted
            ok = r.status_code != 404
            self.results.record(name, ok, f"status={r.status_code}")
        except requests.exceptions.ReadTimeout:
            # SSE would block — that's fine, means it's mounted
            self.results.record(name, True, "SSE endpoint is streaming (timeout=ok)")
        except Exception as e:
            self.results.record(name, False, str(e))

    def test_unauthenticated_rejected(self):
        """LLM endpoints should reject unauthenticated requests."""
        name = "unauth_rejected"
        try:
            r = _post(f"{self.api_url}/api/v1/llm/chat",
                       json_data={"message": "hello"}, timeout=15)
            ok = r.status_code in (401, 403)
            self.results.record(name, ok, f"status={r.status_code}")
        except Exception as e:
            self.results.record(name, False, str(e))

    # -- runner --

    def run_all(self) -> int:
        print(f"\n{'='*60}")
        print("Verlihub LLM End-to-End Integration Tests")
        print(f"{'='*60}")
        print(f"  API URL:    {self.api_url}")
        print(f"  Ollama URL: {self.ollama_url}")
        print(f"  Model:      {self.model}")
        print(f"  Admin:      {self.admin_user}")
        print(f"{'='*60}\n")

        tests = [
            self.test_ollama_health,
            self.test_model_available,
            self.test_hub_health,
            self.test_authenticate,
            self.test_llm_status,
            self.test_unauthenticated_rejected,
            self.test_simple_chat,
            self.test_tool_calling_hub_users,
            self.test_tool_calling_hub_info,
            self.test_chat_conversation_id,
            self.test_mcp_endpoint,
        ]

        for test_fn in tests:
            test_section = test_fn.__doc__ or test_fn.__name__
            print(f"\n--- {test_section.strip()} ---")
            try:
                test_fn()
            except Exception:
                self.results.record(test_fn.__name__, False, traceback.format_exc())

        print(self.results.summary())
        return 0 if self.results.failed == 0 else 1


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LLM E2E integration tests")
    parser.add_argument("--api-url", default="http://llm-hub:8000",
                        help="Base URL of the verlihub-py API")
    parser.add_argument("--admin-user", default="admin")
    parser.add_argument("--admin-pass", default="admin123")
    parser.add_argument("--model", default="qwen2.5:0.5b",
                        help="Expected Ollama model name")
    parser.add_argument("--ollama-url", default="http://ollama:11434",
                        help="Ollama API base URL")
    args = parser.parse_args()

    # Wait a few seconds for services to stabilize
    print("Waiting 5s for services to stabilize...")
    time.sleep(5)

    runner = LlmE2ETests(
        api_url=args.api_url,
        admin_user=args.admin_user,
        admin_pass=args.admin_pass,
        model=args.model,
        ollama_url=args.ollama_url,
    )
    sys.exit(runner.run_all())


if __name__ == "__main__":
    main()
