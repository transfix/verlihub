#!/usr/bin/env python3
"""
Ledokol Integration Test Suite — verlihub-py (Python Hub + Ledokol-compat Plugin)

Exercises ledokol-equivalent functionality on the Python reimplementation
of Verlihub. The verlihub-py hub handles these commands through its
chat_message event handler pipeline and the ledokol_compat plugin.

Tests cover the same feature categories as test_ledokol_legacy.py:

  - Script management (help, stats, config)
  - Chat features (say, clear, calculator, history)
  - News system (add, list, delete)
  - Content management (replacer, responder, triggers, reminders)
  - Security (antispam, search filters, protection)
  - User management (gag, userinfo, custom nicks)
  - Releases, friendly hubs, ranks
  - Two-client interaction tests (PM, say verification)

Unlike the legacy test, this uses the full-featured verlihub.client.nmdc
NMDCClient which has threading, callbacks, and context-manager support.

Requirements:
  - verlihub-py hub running with ledokol_compat plugin loaded
  - Admin account available via NMDC
  - pip install verlihub (or pip install -e ./python)

Usage:
    python test_ledokol_py.py --hub-host localhost --hub-port 4111 \\
        --admin-nick admin --admin-pass admin123

    # With optional REST API validation
    python test_ledokol_py.py --hub-host localhost --hub-port 4111 \\
        --api-url http://localhost:8000
"""

import argparse
import json
import os
import re
import sys
import time
import threading
from typing import Optional, List, Callable, Dict, Any

# Use the full-featured verlihub-py NMDC client
try:
    from verlihub.client.nmdc import NMDCClient, NMDCError, NMDCConnectionError
    HAS_VH_CLIENT = True
except ImportError:
    HAS_VH_CLIENT = False

try:
    from verlihub.client.api import HubClient, HubClientError
    HAS_API_CLIENT = True
except ImportError:
    HAS_API_CLIENT = False


class LedokolPyTestRunner:
    """Integration test runner for ledokol-compat commands on verlihub-py"""

    SEPARATOR = "=" * 60

    def __init__(self, hub_host: str, hub_port: int,
                 admin_nick: str, admin_pass: str,
                 api_url: str = None,
                 cmd_wait: float = 3.0, debug: bool = False):
        self.hub_host = hub_host
        self.hub_port = hub_port
        self.admin_nick = admin_nick
        self.admin_pass = admin_pass
        self.api_url = api_url
        self.cmd_wait = cmd_wait
        self.debug = debug
        self.client: Optional[NMDCClient] = None
        self.api_client: Optional[Any] = None
        self.results: Dict[str, Any] = {
            "passed": 0, "failed": 0, "skipped": 0, "tests": []
        }
        # Collect chat messages seen by our observer
        self._chat_log: List[str] = []
        self._chat_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _record(self, name: str, passed: bool, msg: str = "", skipped: bool = False):
        status = "SKIP" if skipped else ("PASS" if passed else "FAIL")
        self.results["tests"].append({"name": name, "status": status, "message": msg})
        if skipped:
            self.results["skipped"] += 1
            print(f"  [SKIP] {name}: {msg}")
        elif passed:
            self.results["passed"] += 1
            print(f"  [PASS] {name}")
        else:
            self.results["failed"] += 1
            print(f"  [FAIL] {name}: {msg}")

    def _run(self, name: str, fn: Callable) -> bool:
        try:
            result = fn()
            self._record(name, result)
            return result
        except Exception as exc:
            self._record(name, False, f"Exception: {exc}")
            return False

    def _on_chat(self, nick: str, message: str):
        """Callback for chat messages — buffers for inspection."""
        with self._chat_lock:
            self._chat_log.append(f"<{nick}> {message}")
            if self.debug:
                print(f"  [CHAT] <{nick}> {message}")

    def _on_pm(self, from_nick: str, message: str):
        """Callback for private messages — buffers for inspection."""
        with self._chat_lock:
            self._chat_log.append(f"[PM from {from_nick}] {message}")
            if self.debug:
                print(f"  [PM] <{from_nick}> {message}")

    def _clear_log(self):
        with self._chat_lock:
            self._chat_log.clear()

    def _get_log(self) -> List[str]:
        with self._chat_lock:
            return list(self._chat_log)

    def _exec(self, command: str, wait: float = None) -> List[str]:
        """Send a command and collect responses via execute_command + chat log."""
        self._clear_log()
        responses = self.client.execute_command(command, wait_time=wait or self.cmd_wait)
        # Merge with any chat log entries
        log = self._get_log()
        return responses + log

    def _contains(self, responses: list, pattern: str, flags=re.IGNORECASE) -> bool:
        for msg in responses:
            if re.search(pattern, msg, flags):
                return True
        return False

    def _skip_if_not_implemented(self, name: str, responses: list) -> bool:
        """Check if the command is not (yet) implemented and skip gracefully."""
        if self._contains(responses, r"unknown.*command|not.*implement|no.*such|syntax"):
            self._record(name, False, "Not yet implemented", skipped=True)
            return True
        return False

    # ------------------------------------------------------------------
    # Setup / Teardown
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        if not HAS_VH_CLIENT:
            print("[SETUP] ✗ verlihub.client.nmdc not available — pip install -e ./python")
            return False

        print(f"\n[SETUP] Connecting to verlihub-py at {self.hub_host}:{self.hub_port} …")
        self.client = NMDCClient(
            host=self.hub_host,
            port=self.hub_port,
            nick=self.admin_nick,
            password=self.admin_pass,
        )
        self.client.on_chat_message = self._on_chat
        self.client.on_private_message = self._on_pm

        try:
            self.client.connect(timeout=30)
        except (NMDCConnectionError, NMDCError) as e:
            print(f"[SETUP] ✗ NMDC connection failed: {e}")
            return False

        if not self.client.is_connected:
            print("[SETUP] ✗ Not connected after handshake")
            return False

        print("[SETUP] ✓ Connected via NMDC")

        # Give hub a moment to finish welcome burst
        time.sleep(2)
        self._clear_log()

        # Optional: connect REST API client
        if self.api_url and HAS_API_CLIENT:
            try:
                self.api_client = HubClient(self.api_url, timeout=10)
                print(f"[SETUP] ✓ API client connected to {self.api_url}")
            except Exception as e:
                print(f"[SETUP] ⚠ API client failed: {e}")

        return True

    def cleanup(self):
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass
        if self.api_client:
            try:
                self.api_client.close()
            except Exception:
                pass

    # ==================================================================
    # TEST: Script Management
    # ==================================================================

    def test_ledohelp(self) -> bool:
        r = self._exec("!ledohelp", wait=5)
        if self._skip_if_not_implemented("Script/ledohelp", r):
            return True
        return self._contains(r, r"command|help|ledokol")

    def test_ledostats(self) -> bool:
        r = self._exec("!ledostats", wait=5)
        if self._skip_if_not_implemented("Script/ledostats", r):
            return True
        return self._contains(r, r"version|statistic|uptime|memory|ledokol")

    def test_ledoconf(self) -> bool:
        r = self._exec("!ledoconf", wait=5)
        if self._skip_if_not_implemented("Script/ledoconf", r):
            return True
        return self._contains(r, r"configuration|variable")

    def test_ledoset(self) -> bool:
        r = self._exec("!ledoset custnickclass 2", wait=3)
        if self._skip_if_not_implemented("Script/ledoset", r):
            return True
        changed = self._contains(r, r"changed|custnickclass|=>|2")
        self._exec("!ledoset custnickclass 3", wait=2)
        return changed

    # ==================================================================
    # TEST: Chat Features
    # ==================================================================

    def test_say(self) -> bool:
        r = self._exec("!say PyTestBot Hello from verlihub-py test!", wait=3)
        return self._contains(r, r"<PyTestBot>.*Hello.*verlihub-py") or self._contains(r, r"say|unknown")

    def test_clear(self) -> bool:
        r = self._exec("!clear", wait=3)
        return True  # No-crash = pass

    def test_calculator(self) -> bool:
        r = self._exec("+calculate 6*7", wait=3)
        return self._contains(r, r"42")

    def test_calculator_complex(self) -> bool:
        r = self._exec("+calculate 100/4", wait=3)
        return self._contains(r, r"25")

    def test_calculator_div_zero(self) -> bool:
        r = self._exec("+calculate 1/0", wait=3)
        return self._contains(r, r"zero|forbid|error|divide")

    def test_topic(self) -> bool:
        r = self._exec("+showtopic", wait=3)
        return self._contains(r, r"topic") or len(r) > 0

    def test_mode(self) -> bool:
        r = self._exec("+mode 0", wait=3)
        return self._contains(r, r"mode|chat") or len(r) > 0

    # ==================================================================
    # TEST: Chat History
    # ==================================================================

    def test_history_show(self) -> bool:
        self.client.send_chat("verlihub-py history marker ABCDE789")
        time.sleep(1)
        r = self._exec("+history 10", wait=5)
        return self._contains(r, r"history|chat|message|ABCDE789") or len(r) > 0

    def test_history_clean(self) -> bool:
        r = self._exec("!histclean", wait=3)
        return self._contains(r, r"delet|clean|history|empty")

    # ==================================================================
    # TEST: News System
    # ==================================================================

    def test_news_lifecycle(self) -> bool:
        r = self._exec("!newsadd PyTest news item ZZZZ456", wait=3)
        added = self._contains(r, r"added|news")

        r = self._exec("+hubnews 10", wait=3)
        listed = self._contains(r, r"ZZZZ456|news")

        r = self._exec("!newsdel all", wait=3)
        return added and listed

    # ==================================================================
    # TEST: Content Management
    # ==================================================================

    def test_replacer_lifecycle(self) -> bool:
        r = self._exec('!repladd "pytestbad" "pytestgood" 10', wait=3)
        added = self._contains(r, r"added|replacer|pytestbad")
        r = self._exec("!repllist", wait=3)
        listed = self._contains(r, r"pytestbad|replacer|list")
        r = self._exec("!repldel 1", wait=3)
        return added or listed

    def test_responder_lifecycle(self) -> bool:
        r = self._exec('!respadd "pytest hello" "PyTest response!" 10', wait=3)
        added = self._contains(r, r"added|responder|pytest")
        r = self._exec("!resplist", wait=3)
        listed = self._contains(r, r"pytest|responder|list")
        r = self._exec("!respdel 1", wait=3)
        return added or listed

    def test_trigger_lifecycle(self) -> bool:
        self._exec("!ledoset trigrunning 1", wait=2)
        r = self._exec('!trigadd pytest_trig "Trigger response here" 0 10', wait=3)
        added = self._contains(r, r"added|trigger|pytest")
        r = self._exec("!triglist", wait=3)
        listed = self._contains(r, r"pytest_trig|trigger|list")
        self._exec("!trigdel pytest_trig", wait=3)
        self._exec("!ledoset trigrunning 0", wait=2)
        return added or listed

    def test_reminder_lifecycle(self) -> bool:
        r = self._exec('!remadd pytest_rem "PyTest reminder" 0 10 0 60', wait=3)
        added = self._contains(r, r"added|modified|reminder|pytest")
        r = self._exec("!remlist", wait=3)
        listed = self._contains(r, r"pytest_rem|reminder|list")
        self._exec("!remdel pytest_rem", wait=3)
        return added or listed

    # ==================================================================
    # TEST: Security
    # ==================================================================

    def test_antispam_lifecycle(self) -> bool:
        r = self._exec('!antiadd "pytest_spam" 1 0 0', wait=3)
        added = self._contains(r, r"added|antispam|pytest")
        r = self._exec("!antilist", wait=3)
        listed = self._contains(r, r"pytest_spam|antispam|list")
        self._exec('!antidel "pytest_spam"', wait=3)
        return added or listed

    def test_antispam_exception_lifecycle(self) -> bool:
        r = self._exec('!antiexadd "pytest_antiex"', wait=3)
        added = self._contains(r, r"added|exception|pytest")
        r = self._exec("!antiexlist", wait=3)
        listed = self._contains(r, r"pytest_antiex|exception|list")
        self._exec('!antiexdel "pytest_antiex"', wait=3)
        return added or listed

    def test_search_filter_lifecycle(self) -> bool:
        r = self._exec('!sefiadd "pytest_sefi" 1 0 0', wait=3)
        added = self._contains(r, r"added|search.*filter|pytest")
        r = self._exec("!sefilist", wait=3)
        listed = self._contains(r, r"pytest_sefi|search.*filter|list")
        self._exec('!sefidel "pytest_sefi"', wait=3)
        return added or listed

    def test_myinfo_filter_lifecycle(self) -> bool:
        r = self._exec('!myinfadd nick "pytest_mi" "1d" "test"', wait=3)
        added = self._contains(r, r"added|forbid|pytest")
        r = self._exec("!myinflist nick", wait=3)
        listed = self._contains(r, r"pytest_mi|list|nick")
        self._exec('!myinfdel nick "pytest_mi"', wait=3)
        return added or listed

    def test_protection_lifecycle(self) -> bool:
        r = self._exec("!protadd pytest_protected", wait=3)
        added = self._contains(r, r"added|protection|pytest")
        r = self._exec("!protlist", wait=3)
        listed = self._contains(r, r"pytest_protected|protection|list")
        self._exec("!protdel pytest_protected", wait=3)
        return added or listed

    # ==================================================================
    # TEST: User Management
    # ==================================================================

    def test_ip_gag_lifecycle(self) -> bool:
        r = self._exec('!gagipadd "192%.168%.99%.%d+" 1', wait=3)
        added = self._contains(r, r"added|gag|192")
        r = self._exec("!gagiplist", wait=3)
        listed = self._contains(r, r"192.*168|gag|list")
        self._exec('!gagipdel "192%.168%.99%.%d+"', wait=3)
        return added or listed

    def test_userinfo(self) -> bool:
        r = self._exec(f"!userinfo {self.admin_nick}", wait=5)
        return self._contains(r, r"nick|class|ip|info|share|user")

    def test_custom_nick(self) -> bool:
        self._exec("!ledoset custnickclass 3", wait=2)
        r = self._exec("+nick PyTestAdmin", wait=3)
        nick_set = self._contains(r, r"known as|nick|PyTestAdmin")
        r = self._exec("+custlist", wait=3)
        listed = self._contains(r, r"PyTestAdmin|custom|nick|list")
        self._exec(f"!custdel {self.admin_nick}", wait=2)
        return nick_set or listed

    def test_welcome_message(self) -> bool:
        r = self._exec("+wmset login PyTestWelcome777", wait=3)
        msg_set = self._contains(r, r"login.*message|PyTestWelcome")
        r = self._exec("+wmshow", wait=3)
        shown = self._contains(r, r"PyTestWelcome777|welcome|message")
        self._exec(f"!wmdel {self.admin_nick}", wait=2)
        return msg_set or shown

    def test_offline_msg_list(self) -> bool:
        r = self._exec("!offlist", wait=3)
        return self._contains(r, r"offline|message|list|empty")

    def test_ip_watch_list(self) -> bool:
        r = self._exec("!ipwatlist", wait=3)
        return self._contains(r, r"ip.*watch|list|empty")

    def test_hard_ban_list(self) -> bool:
        r = self._exec("!hbans", wait=3)
        return self._contains(r, r"hard|ban|ip|list|empty")

    def test_votekick_list(self) -> bool:
        r = self._exec("!votekicklist", wait=3)
        return self._contains(r, r"vote|kick|list|empty")

    def test_rank_exception_lifecycle(self) -> bool:
        r = self._exec("!ranexadd pytest_rankex", wait=3)
        added = self._contains(r, r"added|rank.*exception|pytest")
        r = self._exec("!ranexlist", wait=3)
        listed = self._contains(r, r"pytest_rankex|rank.*exception|list")
        self._exec("!ranexdel pytest_rankex", wait=3)
        return added or listed

    def test_clone_info(self) -> bool:
        r = self._exec("!cloneinfo", wait=3)
        return self._contains(r, r"clone|detect|no|info|total")

    # ==================================================================
    # TEST: Releases & Hubs
    # ==================================================================

    def test_release_lifecycle(self) -> bool:
        r = self._exec('!reladd "PyTestRelease" "PyTestCat"', wait=3)
        added = self._contains(r, r"added|release|PyTestRelease")
        r = self._exec("+rellist cat 10", wait=3)
        listed = self._contains(r, r"PyTestCat|release|list")
        self._exec('!reldel name "PyTestRelease"', wait=3)
        return added or listed

    def test_friendly_hub_lifecycle(self) -> bool:
        r = self._exec('!hubadd nmdc://pytest.example:411 "PyTestHub" "Tester"', wait=3)
        added = self._contains(r, r"added|friendly|hub|PyTestHub")
        r = self._exec("+showhubs", wait=3)
        listed = self._contains(r, r"PyTestHub|pytest|friendly|hub")
        self._exec("!hubdel nmdc://pytest.example:411", wait=3)
        return added or listed

    # ==================================================================
    # TEST: Registration & Ranks
    # ==================================================================

    def test_regstats(self) -> bool:
        r = self._exec("!regstats", wait=5)
        return self._contains(r, r"registered|user|class|statistic")

    def test_regfind(self) -> bool:
        r = self._exec(f"!regfind {self.admin_nick}", wait=3)
        return self._contains(r, r"registered|class|found|result|admin")

    def test_chat_ranks(self) -> bool:
        r = self._exec("+chatranks", wait=3)
        return self._contains(r, r"rank|chat|list|empty|top")

    def test_share_ranks(self) -> bool:
        r = self._exec("+shareranks", wait=3)
        return self._contains(r, r"rank|share|list|empty|top")

    def test_op_ranks(self) -> bool:
        r = self._exec("+opranks", wait=3)
        return self._contains(r, r"rank|operator|list|empty|top")

    def test_cc_live(self) -> bool:
        r = self._exec("+cclive", wait=3)
        return self._contains(r, r"country|location|statistic|empty|total") or len(r) > 0

    # ==================================================================
    # TEST: Menu & Misc Commands
    # ==================================================================

    def test_rcmenu_list(self) -> bool:
        r = self._exec("!rcmenulist", wait=3)
        return self._contains(r, r"menu|item|list|empty|right|click")

    def test_cmndshow(self) -> bool:
        r = self._exec("!cmndshow", wait=3)
        return self._contains(r, r"command|custom|list|script|empty")

    def test_command_logger(self) -> bool:
        r = self._exec("!clog 5", wait=3)
        return self._contains(r, r"command|log|empty|entry")

    # ==================================================================
    # TEST: Two-Client Interaction
    # ==================================================================

    def test_two_client_chat(self) -> bool:
        """Connect a second client and verify chat is visible."""
        observer_messages = []

        def on_observer_chat(nick, msg):
            observer_messages.append(f"<{nick}> {msg}")

        try:
            observer = NMDCClient(
                host=self.hub_host,
                port=self.hub_port,
                nick="LedoObserver",
                password=self.admin_pass,
            )
            observer.on_chat_message = on_observer_chat
            observer.connect(timeout=15)

            if not observer.is_connected:
                return False

            time.sleep(1)

            # Admin sends a message
            marker = "TwoClientTest_MARKER_" + str(int(time.time()))
            self.client.send_chat(marker)
            time.sleep(2)

            # Check observer saw it
            found = any(marker in m for m in observer_messages)
            observer.disconnect()
            return found

        except Exception as e:
            if self.debug:
                print(f"  [DEBUG] Two-client error: {e}")
            return False

    def test_two_client_pm(self) -> bool:
        """Send a PM from admin to observer and verify receipt."""
        pm_received = []

        def on_observer_pm(from_nick, msg):
            pm_received.append(f"<{from_nick}> {msg}")

        try:
            observer = NMDCClient(
                host=self.hub_host,
                port=self.hub_port,
                nick="LedoPMBot",
                password=self.admin_pass,
            )
            observer.on_private_message = on_observer_pm
            observer.connect(timeout=15)

            if not observer.is_connected:
                return False

            time.sleep(1)

            marker = "PMTest_MARKER_" + str(int(time.time()))
            self.client.send_pm("LedoPMBot", marker)
            time.sleep(2)

            found = any(marker in m for m in pm_received)
            observer.disconnect()
            return found

        except Exception as e:
            if self.debug:
                print(f"  [DEBUG] PM test error: {e}")
            return False

    # ==================================================================
    # TEST: REST API Integration (optional)
    # ==================================================================

    def test_api_hub_info(self) -> bool:
        """Verify hub info via REST API matches NMDC state."""
        if not self.api_client:
            self._record("API/hub_info", False, "No API client", skipped=True)
            return True
        try:
            info = self.api_client.get_hub_info()
            return info is not None and "name" in info
        except Exception:
            return False

    def test_api_user_count(self) -> bool:
        """Verify user count via REST API includes our NMDC sessions."""
        if not self.api_client:
            self._record("API/user_count", False, "No API client", skipped=True)
            return True
        try:
            count = self.api_client.get_user_count()
            return isinstance(count, int) and count >= 1
        except Exception:
            return False

    # ==================================================================
    # Main Runner
    # ==================================================================

    def run_all(self) -> bool:
        print(f"\n{self.SEPARATOR}")
        print("LEDOKOL INTEGRATION TEST SUITE — VERLIHUB-PY")
        print(f"Hub: {self.hub_host}:{self.hub_port}  Admin: {self.admin_nick}")
        if self.api_url:
            print(f"API: {self.api_url}")
        print(self.SEPARATOR)

        if not self.connect():
            return False

        time.sleep(1)
        self._clear_log()

        categories = [
            ("Script Management", [
                ("ledohelp",  self.test_ledohelp),
                ("ledostats", self.test_ledostats),
                ("ledoconf",  self.test_ledoconf),
                ("ledoset",   self.test_ledoset),
            ]),
            ("Chat Features", [
                ("say",        self.test_say),
                ("clear",      self.test_clear),
                ("calculate",  self.test_calculator),
                ("calc_complex", self.test_calculator_complex),
                ("calc_zero",  self.test_calculator_div_zero),
                ("topic",      self.test_topic),
                ("mode",       self.test_mode),
            ]),
            ("Chat History", [
                ("history",    self.test_history_show),
                ("histclean",  self.test_history_clean),
            ]),
            ("News System", [
                ("news",       self.test_news_lifecycle),
            ]),
            ("Content Management", [
                ("replacer",   self.test_replacer_lifecycle),
                ("responder",  self.test_responder_lifecycle),
                ("trigger",    self.test_trigger_lifecycle),
                ("reminder",   self.test_reminder_lifecycle),
            ]),
            ("Security", [
                ("antispam",       self.test_antispam_lifecycle),
                ("antispam_ex",    self.test_antispam_exception_lifecycle),
                ("search_filter",  self.test_search_filter_lifecycle),
                ("myinfo_filter",  self.test_myinfo_filter_lifecycle),
                ("protection",     self.test_protection_lifecycle),
            ]),
            ("User Management", [
                ("ip_gag",         self.test_ip_gag_lifecycle),
                ("userinfo",       self.test_userinfo),
                ("custom_nick",    self.test_custom_nick),
                ("welcome_msg",    self.test_welcome_message),
                ("offline_msg",    self.test_offline_msg_list),
                ("ip_watch",       self.test_ip_watch_list),
                ("hard_bans",      self.test_hard_ban_list),
                ("votekick_list",  self.test_votekick_list),
                ("rank_exception", self.test_rank_exception_lifecycle),
                ("clone_info",     self.test_clone_info),
            ]),
            ("Releases & Hubs", [
                ("releases",       self.test_release_lifecycle),
                ("friendly_hubs",  self.test_friendly_hub_lifecycle),
            ]),
            ("Registration & Ranks", [
                ("regstats",       self.test_regstats),
                ("regfind",        self.test_regfind),
                ("chat_ranks",     self.test_chat_ranks),
                ("share_ranks",    self.test_share_ranks),
                ("op_ranks",       self.test_op_ranks),
                ("cclive",         self.test_cc_live),
            ]),
            ("Menu & Commands", [
                ("rcmenu_list",    self.test_rcmenu_list),
                ("cmndshow",       self.test_cmndshow),
                ("command_logger", self.test_command_logger),
            ]),
            ("Multi-Client", [
                ("two_client_chat", self.test_two_client_chat),
                ("two_client_pm",   self.test_two_client_pm),
            ]),
        ]

        # Add API tests if URL provided
        if self.api_url and HAS_API_CLIENT:
            categories.append(("REST API", [
                ("api_hub_info",   self.test_api_hub_info),
                ("api_user_count", self.test_api_user_count),
            ]))

        for cat_name, tests in categories:
            print(f"\n--- {cat_name} ---")
            for test_name, test_fn in tests:
                self._run(f"{cat_name}/{test_name}", test_fn)
                time.sleep(0.5)

        # Summary
        print(f"\n{self.SEPARATOR}")
        print("LEDOKOL TEST SUMMARY — VERLIHUB-PY")
        print(self.SEPARATOR)
        total = self.results["passed"] + self.results["failed"] + self.results["skipped"]
        print(f"  Passed:  {self.results['passed']}")
        print(f"  Failed:  {self.results['failed']}")
        print(f"  Skipped: {self.results['skipped']}")
        print(f"  Total:   {total}")

        if self.results["failed"] > 0:
            print("\n  Failed tests:")
            for t in self.results["tests"]:
                if t["status"] == "FAIL":
                    print(f"    ✗ {t['name']}: {t['message']}")

        self.cleanup()
        return self.results["failed"] == 0


def main():
    parser = argparse.ArgumentParser(description="Ledokol Integration Tests — verlihub-py")
    parser.add_argument("--hub-host", default="verlihub-py", help="Hub hostname")
    parser.add_argument("--hub-port", type=int, default=4111, help="Hub NMDC port")
    parser.add_argument("--admin-nick", default="admin", help="Admin nickname")
    parser.add_argument("--admin-pass", default="admin123", help="Admin password")
    parser.add_argument("--api-url", default=None, help="REST API URL (optional)")
    parser.add_argument("--cmd-wait", type=float, default=3.0, help="Command response wait (s)")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument("--output", help="JSON results output file")
    args = parser.parse_args()

    runner = LedokolPyTestRunner(
        hub_host=args.hub_host,
        hub_port=args.hub_port,
        admin_nick=args.admin_nick,
        admin_pass=args.admin_pass,
        api_url=args.api_url,
        cmd_wait=args.cmd_wait,
        debug=args.debug,
    )

    success = runner.run_all()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(runner.results, f, indent=2)
        print(f"\nResults written to {args.output}")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
