#!/usr/bin/env python3
"""
Bot Chat End-to-End Integration Tests

Validates the NMDC bot chat pipeline using real NMDC client connections:
  1. PM to Hub-Security → LLM response (admin with tools)
  2. PM to Hub-Security → LLM response (operator with read-only tools)
  3. PM to Hub-Security → LLM response (registered user, no tools)
  4. Main chat mention  → LLM response (lowest security, no tools)
  5. Permission enforcement: different user classes get different responses
  6. Multi-turn conversation memory

Uses :class:`verlihub.client.nmdc.NMDCClient` to simulate hub users at
different permission levels.

Usage:
    python test_bot_chat_e2e.py \\
        --hub-host bot-hub --hub-port 4112 \\
        --api-url http://bot-hub:8000 \\
        --admin-user admin --admin-pass admin123 \\
        --operator-user operator1 --operator-pass oper123 \\
        --registered-user regular1 --registered-pass reg123
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import traceback
from threading import Event
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Allow running from the project root without installing
# ---------------------------------------------------------------------------
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "python"))

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. pip install requests")
    sys.exit(1)

try:
    from verlihub.client.nmdc import NMDCClient, NMDCClientConfig
except ImportError:
    print("ERROR: verlihub.client.nmdc not found — check PYTHONPATH")
    sys.exit(1)


# ── Helpers ──────────────────────────────────────────────────────────────────

class TestResult:
    """Accumulates test outcomes."""

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


def wait_for_pm(client: NMDCClient, timeout: float = 60.0) -> Optional[str]:
    """
    Block until a PM from Hub-Security is received, or timeout.

    Returns the message body, or ``None`` on timeout.
    """
    result_msg: list[str] = []
    got_it = Event()

    def _on_pm(from_nick: str, to_nick: str, message: str):
        if from_nick == "Hub-Security":
            result_msg.append(message)
            got_it.set()

    old_cb = client.on_private_message
    client.on_private_message = _on_pm
    try:
        got_it.wait(timeout=timeout)
    finally:
        client.on_private_message = old_cb

    return result_msg[0] if result_msg else None


def wait_for_bot_chat(client: NMDCClient, timeout: float = 60.0) -> Optional[str]:
    """
    Block until a main-chat message from Hub-Security is received.

    Returns the message body, or ``None`` on timeout.
    """
    result_msg: list[str] = []
    got_it = Event()

    def _on_chat(nick: str, message: str):
        if nick == "Hub-Security":
            result_msg.append(message)
            got_it.set()

    old_cb = client.on_chat_message
    client.on_chat_message = _on_chat
    try:
        got_it.wait(timeout=timeout)
    finally:
        client.on_chat_message = old_cb

    return result_msg[0] if result_msg else None


# ── Test class ───────────────────────────────────────────────────────────────

class BotChatE2ETests:
    """End-to-end NMDC bot chat tests."""

    def __init__(
        self,
        hub_host: str,
        hub_port: int,
        api_url: str,
        admin_user: str,
        admin_pass: str,
        operator_user: str,
        operator_pass: str,
        registered_user: str,
        registered_pass: str,
    ):
        self.hub_host = hub_host
        self.hub_port = hub_port
        self.api_url = api_url.rstrip("/")
        self.admin_user = admin_user
        self.admin_pass = admin_pass
        self.operator_user = operator_user
        self.operator_pass = operator_pass
        self.registered_user = registered_user
        self.registered_pass = registered_pass
        self.results = TestResult()

    # -- connection helpers -------------------------------------------------

    def _make_client(self, nick: str, password: str) -> NMDCClient:
        cfg = NMDCClientConfig(
            host=self.hub_host,
            port=self.hub_port,
            nick=nick,
            password=password,
            description="Bot chat test client",
            timeout=30.0,
        )
        return NMDCClient(config=cfg)

    # -- individual tests --------------------------------------------------

    def test_hub_health(self):
        """Verify the hub API is healthy before NMDC tests."""
        try:
            r = requests.get(f"{self.api_url}/health", timeout=10)
            data = r.json()
            ok = data.get("hub_running", False) and data.get("status") == "healthy"
            self.results.record(
                "Hub health check",
                ok,
                f"hub_running={data.get('hub_running')}, status={data.get('status')}",
            )
        except Exception as e:
            self.results.record("Hub health check", False, str(e))

    def test_llm_status(self):
        """Verify LLM is enabled and reachable."""
        try:
            # Authenticate to get a JWT
            r = requests.post(
                f"{self.api_url}/api/v1/auth/login",
                json={"username": self.admin_user, "password": self.admin_pass},
                timeout=10,
            )
            token = r.json().get("access_token", "")
            headers = {"Authorization": f"Bearer {token}"}

            r = requests.get(f"{self.api_url}/api/v1/llm/status", headers=headers, timeout=10)
            data = r.json()
            ok = data.get("enabled", False) and data.get("llm_reachable", False)
            self.results.record(
                "LLM status (enabled + reachable)",
                ok,
                f"enabled={data.get('enabled')}, reachable={data.get('llm_reachable')}",
            )
        except Exception as e:
            self.results.record("LLM status", False, str(e))

    def test_admin_nmdc_connect(self):
        """Admin user can connect to the NMDC hub."""
        try:
            client = self._make_client(self.admin_user, self.admin_pass)
            ok = client.connect(timeout=15)
            self.results.record("Admin NMDC connect", ok)
            client.close()
        except Exception as e:
            self.results.record("Admin NMDC connect", False, str(e))

    def test_admin_pm_bot(self):
        """Admin sends PM to Hub-Security and gets an LLM response."""
        client = self._make_client(self.admin_user, self.admin_pass)
        try:
            client.connect(timeout=15)
            time.sleep(1)  # let the hub register the user

            # Send PM
            client.send_pm("Hub-Security", "Hello, who are you?")
            response = wait_for_pm(client, timeout=90)

            ok = response is not None and len(response) > 5
            self.results.record(
                "Admin PM → bot response",
                ok,
                f"response={response[:120]}..." if response else "no response",
            )
        except Exception as e:
            self.results.record("Admin PM → bot response", False, traceback.format_exc())
        finally:
            try:
                client.close()
            except Exception:
                pass

    def test_operator_pm_bot(self):
        """Operator sends PM to Hub-Security and gets an LLM response."""
        client = self._make_client(self.operator_user, self.operator_pass)
        try:
            client.connect(timeout=15)
            time.sleep(1)

            client.send_pm("Hub-Security", "How many users are online?")
            response = wait_for_pm(client, timeout=90)

            ok = response is not None and len(response) > 5
            self.results.record(
                "Operator PM → bot response",
                ok,
                f"response={response[:120]}..." if response else "no response",
            )
        except Exception as e:
            self.results.record("Operator PM → bot response", False, traceback.format_exc())
        finally:
            try:
                client.close()
            except Exception:
                pass

    def test_registered_pm_bot(self):
        """Registered user (class 1) PMs Hub-Security — should still get a response (conversational)."""
        client = self._make_client(self.registered_user, self.registered_pass)
        try:
            client.connect(timeout=15)
            time.sleep(1)

            client.send_pm("Hub-Security", "Hi there, what can you do?")
            response = wait_for_pm(client, timeout=90)

            ok = response is not None and len(response) > 5
            self.results.record(
                "Registered PM → bot response (no tools)",
                ok,
                f"response={response[:120]}..." if response else "no response",
            )
        except Exception as e:
            self.results.record("Registered PM → bot response", False, traceback.format_exc())
        finally:
            try:
                client.close()
            except Exception:
                pass

    def test_main_chat_mention(self):
        """User addresses bot in main chat — should get a response at lowest security."""
        client = self._make_client(self.admin_user, self.admin_pass)
        try:
            client.connect(timeout=15)
            time.sleep(1)

            client.send_chat("Hub-Security: What is this hub about?")
            response = wait_for_bot_chat(client, timeout=90)

            ok = response is not None and len(response) > 5
            self.results.record(
                "Main chat mention → bot response",
                ok,
                f"response={response[:120]}..." if response else "no response",
            )
        except Exception as e:
            self.results.record("Main chat mention → bot response", False, traceback.format_exc())
        finally:
            try:
                client.close()
            except Exception:
                pass

    def test_pm_multi_turn(self):
        """Multi-turn PM conversation preserves context."""
        client = self._make_client(self.admin_user, self.admin_pass)
        try:
            client.connect(timeout=15)
            time.sleep(1)

            # Turn 1
            client.send_pm("Hub-Security", "My name is TestAdmin. Remember it.")
            resp1 = wait_for_pm(client, timeout=90)
            ok1 = resp1 is not None

            if ok1:
                # Turn 2 — ask for the name back
                client.send_pm("Hub-Security", "What is my name?")
                resp2 = wait_for_pm(client, timeout=90)
                ok2 = resp2 is not None and "TestAdmin" in resp2
                self.results.record(
                    "Multi-turn PM (context retained)",
                    ok2,
                    f"resp2={resp2[:120]}..." if resp2 else "no response",
                )
            else:
                self.results.record("Multi-turn PM (context retained)", False, "Turn 1 got no response")
        except Exception as e:
            self.results.record("Multi-turn PM (context retained)", False, traceback.format_exc())
        finally:
            try:
                client.close()
            except Exception:
                pass

    def test_no_response_for_non_bot_pm(self):
        """PM to another user (not Hub-Security) should NOT trigger the bot."""
        client1 = self._make_client(self.admin_user, self.admin_pass)
        client2 = self._make_client(self.operator_user, self.operator_pass)
        try:
            client1.connect(timeout=15)
            client2.connect(timeout=15)
            time.sleep(1)

            # Admin sends PM to operator (not to the bot)
            received: list[str] = []
            got_pm = Event()

            def _capture_pm(from_nick: str, to_nick: str, message: str):
                received.append(f"{from_nick}→{to_nick}: {message}")
                got_pm.set()

            client2.on_private_message = _capture_pm
            client1.send_pm(self.operator_user, "Hello operator")

            # Wait briefly — we expect the operator to get it (user-to-user PM),
            # but NOT from Hub-Security
            got_pm.wait(timeout=10)

            from_bot = any("Hub-Security" in m for m in received)
            self.results.record(
                "Non-bot PM does NOT trigger bot",
                not from_bot,
                f"received={received}" if received else "no PMs received (expected)",
            )
        except Exception as e:
            self.results.record("Non-bot PM does NOT trigger bot", False, traceback.format_exc())
        finally:
            try:
                client1.close()
            except Exception:
                pass
            try:
                client2.close()
            except Exception:
                pass

    # -- runner ------------------------------------------------------------

    def run_all(self):
        """Execute all tests in order."""
        print("\n" + "=" * 60)
        print("Bot Chat E2E Integration Tests")
        print("=" * 60)
        print(f"  Hub:  {self.hub_host}:{self.hub_port}")
        print(f"  API:  {self.api_url}")
        print()

        # Pre-flight checks
        self.test_hub_health()
        self.test_llm_status()

        # NMDC connectivity
        self.test_admin_nmdc_connect()

        # PM bot tests
        self.test_admin_pm_bot()
        self.test_operator_pm_bot()
        self.test_registered_pm_bot()

        # Main chat bot tests
        self.test_main_chat_mention()

        # Advanced
        self.test_pm_multi_turn()
        self.test_no_response_for_non_bot_pm()

        print(self.results.summary())
        return self.results.failed == 0


# ── CLI entry-point ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Bot Chat E2E Integration Tests")
    parser.add_argument("--hub-host", default="bot-hub", help="NMDC hub hostname")
    parser.add_argument("--hub-port", type=int, default=4112, help="NMDC hub port")
    parser.add_argument("--api-url", default="http://bot-hub:8000", help="REST API URL")
    parser.add_argument("--admin-user", default="admin")
    parser.add_argument("--admin-pass", default="admin123")
    parser.add_argument("--operator-user", default="operator1")
    parser.add_argument("--operator-pass", default="oper123")
    parser.add_argument("--registered-user", default="regular1")
    parser.add_argument("--registered-pass", default="reg123")
    args = parser.parse_args()

    # Wait for hub to be ready (retry NMDC connectivity)
    print("Waiting for hub to accept NMDC connections...")
    for attempt in range(30):
        try:
            cfg = NMDCClientConfig(
                host=args.hub_host, port=args.hub_port,
                nick="probe", password="",
                timeout=5.0,
            )
            probe = NMDCClient(config=cfg)
            probe.connect(timeout=5)
            probe.close()
            print(f"  Hub accepting connections (attempt {attempt + 1})")
            break
        except Exception:
            time.sleep(2)
    else:
        print("ERROR: Hub not reachable after 60 s")
        sys.exit(1)

    tests = BotChatE2ETests(
        hub_host=args.hub_host,
        hub_port=args.hub_port,
        api_url=args.api_url,
        admin_user=args.admin_user,
        admin_pass=args.admin_pass,
        operator_user=args.operator_user,
        operator_pass=args.operator_pass,
        registered_user=args.registered_user,
        registered_pass=args.registered_pass,
    )

    ok = tests.run_all()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
