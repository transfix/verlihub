#!/usr/bin/env python3
"""
NMDC Load Test - 16 Concurrent Clients with Staggered Messages

Connects 16 NMDC clients to a Verlihub hub, each sending staggered
test messages to the main chat. Useful for QA testing with concurrent
load before connecting a desktop client.

Usage:
    python nmdc_load_test.py --host hub --port 4111
    python nmdc_load_test.py --host hub --port 4111 --clients 16 --messages 20
    python nmdc_load_test.py --host hub --port 4111 --duration 300
"""

import argparse
import os
import random
import socket
import sys
import threading
import time
from typing import Optional, List


# ── NMDC client (self-contained for Docker use) ──────────────────────────────

class LoadTestClient:
    """Lightweight NMDC client for load testing"""

    def __init__(self, host: str, port: int, nick: str,
                 share: int = 0, slots: int = 1, description: str = "LoadBot"):
        self.host = host
        self.port = port
        self.nick = nick
        self.share = share
        self.slots = slots
        self.description = description
        self.sock: Optional[socket.socket] = None
        self.buffer = ""
        self.connected = False
        self.logged_in = False
        self.debug = False
        self._lock = threading.Lock()
        self.messages_sent = 0
        self.messages_received = 0
        self.errors: List[str] = []

    def connect(self, timeout: float = 30.0) -> bool:
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(timeout)
            self.sock.connect((self.host, self.port))
            self.connected = True
            return self._process_handshake()
        except Exception as e:
            self.errors.append(f"connect: {e}")
            return False

    def _process_handshake(self) -> bool:
        start = time.time()
        while time.time() - start < 30:
            try:
                data = self.sock.recv(4096).decode("utf-8", errors="replace")
                if not data:
                    return False
                self.buffer += data
                while "|" in self.buffer:
                    msg, self.buffer = self.buffer.split("|", 1)
                    if not self._handle(msg):
                        return False
                    if self.logged_in:
                        return True
            except socket.timeout:
                continue
        return False

    def _handle(self, msg: str) -> bool:
        if msg.startswith("$Lock "):
            lock_data = msg[6:].split(" ", 1)[0]
            key = self._calc_key(lock_data)
            self._send("$Supports UserCommand NoGetINFO NoHello UserIP2 BotINFO HubINFO ZPipe0")
            self._send(f"$Key {key}")
            self._send(f"$ValidateNick {self.nick}")
        elif msg.startswith("$Hello "):
            pass  # wait for GetPass or LogedIn
        elif msg.startswith("$GetPass"):
            # Unregistered users won't get this; skip
            pass
        elif msg.startswith("$BadPass"):
            self.errors.append("bad password")
            return False
        elif msg.startswith("$LogedIn"):
            self._send(self._build_myinfo())
            self._send("$GetNickList")
            self.logged_in = True
        elif msg.startswith("$ValidateDenide"):
            self.errors.append(f"nick denied: {self.nick}")
            return False
        elif msg.startswith("<"):
            self.messages_received += 1
        # Accept $HubName without password → unregistered user flow
        elif msg.startswith("$HubName"):
            pass
        elif msg.startswith("$Supports"):
            pass
        return True

    def _calc_key(self, lock: str) -> str:
        key = []
        for i in range(len(lock)):
            if i == 0:
                key.append(ord(lock[0]) ^ ord(lock[-1]) ^ ord(lock[-2]) ^ 5)
            else:
                key.append(ord(lock[i]) ^ ord(lock[i - 1]))
        result = ""
        for b in key:
            b = b & 0xFF
            if b in (0, 5, 36, 96, 124, 126):
                result += f"/%DCN{b:03d}%/"
            else:
                result += chr(b)
        return result

    def _build_myinfo(self) -> str:
        desc = self.description
        speed = f"Bot\x01"
        return (
            f"$MyINFO $ALL {self.nick} {desc}"
            f"<Bot V:1.0,M:A,H:1/0/0,S:{self.slots}>"
            f"$ ${speed}$bot@test${self.share}$"
        )

    def _send(self, msg: str):
        with self._lock:
            try:
                self.sock.sendall((msg + "|").encode("utf-8"))
            except Exception as e:
                self.errors.append(f"send: {e}")

    def send_chat(self, message: str):
        self._send(f"<{self.nick}> {message}")
        self.messages_sent += 1

    def drain(self, seconds: float = 0.5):
        """Read and discard incoming data for a short period."""
        self.sock.settimeout(0.2)
        end = time.time() + seconds
        while time.time() < end:
            try:
                data = self.sock.recv(4096)
                if data:
                    # count received chat messages
                    text = data.decode("utf-8", errors="replace")
                    self.messages_received += text.count("<")
            except (socket.timeout, OSError):
                pass

    def close(self):
        if self.sock:
            try:
                self._send("$Quit")
                self.sock.close()
            except Exception:
                pass
            self.sock = None
            self.connected = False
            self.logged_in = False


# ── Load test orchestrator ───────────────────────────────────────────────────

CHAT_MESSAGES = [
    "Hello from client {nick}!",
    "Testing message #{n} from {nick}",
    "The quick brown fox jumps over the lazy dog #{n}",
    "Lorem ipsum dolor sit amet #{n}",
    "Hub load test in progress – client {nick}, msg {n}",
    "Random payload: {payload}",
    "Checking connectivity – {nick} still online",
    "All systems nominal – message {n}",
    "Stress test iteration {n} from {nick}",
    "How many users are online right now?",
    "!help",
    "Testing unicode: äöü ñ ü ø 你好 🌍",
    "{nick} says: Stay cool, message #{n}",
    "🔧 QA pass – {nick}/{n}",
    "Ping from {nick} at {ts}",
    "End-to-end test message {n}",
]


def random_payload(length: int = 32) -> str:
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(random.choice(chars) for _ in range(length))


def wait_for_hub(host: str, port: int, timeout: float = 120.0) -> bool:
    """Wait until the hub's NMDC port is accepting connections."""
    print(f"[load-test] Waiting for hub at {host}:{port} ...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            s = socket.create_connection((host, port), timeout=3)
            s.close()
            elapsed = time.time() - start
            print(f"[load-test] Hub reachable after {elapsed:.1f}s")
            return True
        except OSError:
            time.sleep(1)
    print(f"[load-test] ERROR: Hub not reachable after {timeout:.0f}s")
    return False


def client_worker(
    client_id: int,
    host: str,
    port: int,
    nick: str,
    num_messages: int,
    interval: float,
    stagger_offset: float,
    duration: float,
    results: dict,
):
    """Thread worker: connect one client, send messages, disconnect."""
    # Stagger connection
    time.sleep(stagger_offset)

    client = LoadTestClient(host, port, nick, description=f"LoadBot#{client_id}")
    if os.getenv("LOAD_TEST_DEBUG"):
        client.debug = True

    print(f"  [{nick}] Connecting...")
    if not client.connect(timeout=30):
        results[nick] = {
            "connected": False,
            "sent": 0,
            "received": 0,
            "errors": client.errors,
        }
        print(f"  [{nick}] FAILED to connect: {client.errors}")
        return

    print(f"  [{nick}] Connected and logged in")

    # Send messages
    start = time.time()
    n = 0
    try:
        while True:
            if duration > 0 and (time.time() - start) >= duration:
                break
            if num_messages > 0 and n >= num_messages:
                break

            n += 1
            template = random.choice(CHAT_MESSAGES)
            msg = template.format(
                nick=nick,
                n=n,
                payload=random_payload(),
                ts=time.strftime("%H:%M:%S"),
            )
            client.send_chat(msg)

            # Drain incoming traffic between sends
            client.drain(0.1)

            # Stagger send interval with jitter
            jitter = random.uniform(0, interval * 0.3)
            time.sleep(interval + jitter)

    except Exception as e:
        client.errors.append(f"send loop: {e}")

    # Final drain
    client.drain(1.0)

    results[nick] = {
        "connected": True,
        "sent": client.messages_sent,
        "received": client.messages_received,
        "errors": client.errors,
    }
    print(f"  [{nick}] Done – sent {client.messages_sent}, received {client.messages_received}")
    client.close()


def run_load_test(
    host: str,
    port: int,
    num_clients: int = 16,
    num_messages: int = 20,
    interval: float = 1.0,
    stagger: float = 0.5,
    duration: float = 0,
    nick_prefix: str = "LoadBot",
) -> dict:
    """Run the full load test with N concurrent NMDC clients."""

    print(f"\n{'='*60}")
    print(f"  NMDC Load Test")
    print(f"  Hub: {host}:{port}")
    print(f"  Clients: {num_clients}")
    print(f"  Messages per client: {num_messages if num_messages > 0 else 'unlimited'}")
    print(f"  Interval: {interval:.1f}s  |  Stagger: {stagger:.1f}s")
    if duration > 0:
        print(f"  Duration cap: {duration:.0f}s")
    print(f"{'='*60}\n")

    results = {}
    threads = []

    for i in range(num_clients):
        nick = f"{nick_prefix}{i+1:02d}"
        t = threading.Thread(
            target=client_worker,
            args=(i, host, port, nick, num_messages, interval, stagger * i, duration, results),
            daemon=True,
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=max(300, duration + 60))

    # ── Summary ──────────────────────────────────────────────────────────
    total_sent = sum(r["sent"] for r in results.values())
    total_recv = sum(r["received"] for r in results.values())
    connected = sum(1 for r in results.values() if r["connected"])
    failed = num_clients - connected
    total_errors = sum(len(r.get("errors", [])) for r in results.values())

    print(f"\n{'='*60}")
    print(f"  Load Test Results")
    print(f"  Connected: {connected}/{num_clients}")
    print(f"  Total messages sent: {total_sent}")
    print(f"  Total messages received: {total_recv}")
    print(f"  Errors: {total_errors}")
    if failed:
        print(f"  FAILED clients: {failed}")
        for nick, r in results.items():
            if not r["connected"]:
                print(f"    {nick}: {r['errors']}")
    print(f"{'='*60}\n")

    return {
        "clients": num_clients,
        "connected": connected,
        "failed": failed,
        "total_sent": total_sent,
        "total_received": total_recv,
        "total_errors": total_errors,
        "details": results,
    }


# ── CLI entrypoint ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="NMDC Load Test – multiple concurrent hub clients"
    )
    parser.add_argument("--host", default=os.getenv("HUB_HOST", "hub"),
                        help="Hub hostname (default: hub)")
    parser.add_argument("--port", type=int, default=int(os.getenv("HUB_PORT", "4111")),
                        help="Hub NMDC port (default: 4111)")
    parser.add_argument("--clients", type=int, default=int(os.getenv("NUM_CLIENTS", "16")),
                        help="Number of concurrent clients (default: 16)")
    parser.add_argument("--messages", type=int, default=int(os.getenv("NUM_MESSAGES", "20")),
                        help="Messages per client, 0=unlimited (default: 20)")
    parser.add_argument("--interval", type=float,
                        default=float(os.getenv("MSG_INTERVAL", "1.0")),
                        help="Seconds between messages per client (default: 1.0)")
    parser.add_argument("--stagger", type=float,
                        default=float(os.getenv("STAGGER_DELAY", "0.5")),
                        help="Seconds between client connections (default: 0.5)")
    parser.add_argument("--duration", type=float,
                        default=float(os.getenv("DURATION", "0")),
                        help="Max test duration in seconds, 0=until messages done (default: 0)")
    parser.add_argument("--prefix", default=os.getenv("NICK_PREFIX", "LoadBot"),
                        help="Nick prefix (default: LoadBot)")
    parser.add_argument("--wait", type=float,
                        default=float(os.getenv("HUB_WAIT_TIMEOUT", "120")),
                        help="Seconds to wait for hub to be reachable (default: 120)")
    args = parser.parse_args()

    # Wait for the hub to be ready
    if not wait_for_hub(args.host, args.port, timeout=args.wait):
        sys.exit(1)

    # Small extra delay for hub to finish init
    time.sleep(2)

    result = run_load_test(
        host=args.host,
        port=args.port,
        num_clients=args.clients,
        num_messages=args.messages,
        interval=args.interval,
        stagger=args.stagger,
        duration=args.duration,
        nick_prefix=args.prefix,
    )

    # Keep container alive for a bit so desktop clients can still connect
    keep_alive = int(os.getenv("KEEP_ALIVE", "0"))
    if keep_alive > 0:
        print(f"[load-test] Keeping container alive for {keep_alive}s so you can connect a desktop client...")
        time.sleep(keep_alive)

    # Exit with error if too many failures
    if result["failed"] > result["clients"] // 2:
        print("[load-test] FAIL – more than half the clients failed to connect")
        sys.exit(1)

    if result["total_errors"] > result["clients"]:
        print("[load-test] WARN – high error count")
        sys.exit(1)

    print("[load-test] PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
