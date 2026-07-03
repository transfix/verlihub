"""
NMDC Protocol Stress Tests.

This module provides stress testing for the NMDC protocol implementation,
testing connection handling, message throughput, and protocol robustness.
"""
from __future__ import annotations

import asyncio
import random
import string
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from verlihub.benchmarks.core import (
    BenchmarkResult,
    Timer,
    calculate_percentiles,
)


@dataclass
class NMDCStressResult:
    """Results from NMDC stress testing."""
    
    test_name: str
    total_operations: int
    successful_operations: int
    failed_operations: int
    total_time_seconds: float
    latencies_ms: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    bytes_sent: int = 0
    bytes_received: int = 0
    connections_made: int = 0
    messages_sent: int = 0
    messages_received: int = 0
    
    @property
    def success_rate(self) -> float:
        if self.total_operations == 0:
            return 0.0
        return (self.successful_operations / self.total_operations) * 100
    
    @property
    def operations_per_second(self) -> float:
        if self.total_time_seconds == 0:
            return 0.0
        return self.successful_operations / self.total_time_seconds
    
    @property
    def p50_ms(self) -> float:
        return calculate_percentiles(self.latencies_ms, 50)
    
    @property
    def p95_ms(self) -> float:
        return calculate_percentiles(self.latencies_ms, 95)
    
    @property
    def p99_ms(self) -> float:
        return calculate_percentiles(self.latencies_ms, 99)
    
    def __str__(self) -> str:
        lines = [
            f"\n{'=' * 60}",
            f"NMDC Stress Test: {self.test_name}",
            f"{'=' * 60}",
            f"",
            f"Summary:",
            f"  Total Operations:    {self.total_operations:,}",
            f"  Successful:          {self.successful_operations:,} ({self.success_rate:.1f}%)",
            f"  Failed:              {self.failed_operations:,}",
            f"  Total Time:          {self.total_time_seconds:.2f}s",
            f"  Throughput:          {self.operations_per_second:.2f} ops/s",
            f"",
            f"Protocol Stats:",
            f"  Connections Made:    {self.connections_made:,}",
            f"  Messages Sent:       {self.messages_sent:,}",
            f"  Messages Received:   {self.messages_received:,}",
            f"  Bytes Sent:          {self.bytes_sent:,}",
            f"  Bytes Received:      {self.bytes_received:,}",
        ]
        
        if self.latencies_ms:
            lines.extend([
                f"",
                f"Latency (ms):",
                f"  p50:                 {self.p50_ms:.3f}",
                f"  p95:                 {self.p95_ms:.3f}",
                f"  p99:                 {self.p99_ms:.3f}",
            ])
        
        if self.errors:
            lines.extend([
                f"",
                f"Errors (first 5):",
            ])
            for err in self.errors[:5]:
                lines.append(f"  - {err[:80]}")
        
        lines.append(f"{'=' * 60}\n")
        return "\n".join(lines)


class NMDCStressTest:
    """
    NMDC Protocol stress testing suite.
    
    Tests various aspects of NMDC protocol handling:
    - Connection establishment/teardown cycles
    - Message throughput
    - Protocol robustness against malformed data
    - Concurrent connection handling
    - Memory stability under load
    """
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 4111,
        username: str = "stress_test",
        password: str = "",
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
    
    def _create_client(self, nick_suffix: str = "") -> "NMDCClient":
        """Create a new NMDC client instance."""
        from verlihub.client import NMDCClient
        
        nick = f"{self.username}_{nick_suffix}" if nick_suffix else self.username
        # Truncate nick to NMDC max (typically ~64 chars)
        nick = nick[:32]
        
        return NMDCClient(
            host=self.host,
            port=self.port,
            nick=nick,
            password=self.password,
        )
    
    def test_connection_cycles(
        self,
        num_cycles: int = 50,
        delay_between_ms: float = 100,
    ) -> NMDCStressResult:
        """
        Test repeated connect/disconnect cycles.
        
        This stress tests the connection handling code path and ensures
        proper cleanup between connections.
        """
        from verlihub.client import NMDCClient
        
        latencies = []
        errors = []
        successful = 0
        failed = 0
        connections_made = 0
        
        start_time = time.perf_counter()
        
        for i in range(num_cycles):
            timer = Timer()
            client = None
            try:
                with timer:
                    client = self._create_client(f"conn{i}")
                    connected = client.connect(timeout=10.0)
                    if connected:
                        connections_made += 1
                        client.close()
                        successful += 1
                    else:
                        failed += 1
                        errors.append(f"Connection {i} failed to connect")
                
                latencies.append(timer.elapsed_ms)
                
            except Exception as e:
                failed += 1
                errors.append(f"Connection {i}: {str(e)[:50]}")
                if client:
                    try:
                        client.close()
                    except:
                        pass
            
            if delay_between_ms > 0:
                time.sleep(delay_between_ms / 1000)
        
        total_time = time.perf_counter() - start_time
        
        return NMDCStressResult(
            test_name="Connection Cycles",
            total_operations=num_cycles,
            successful_operations=successful,
            failed_operations=failed,
            total_time_seconds=total_time,
            latencies_ms=latencies,
            errors=errors,
            connections_made=connections_made,
        )
    
    def test_message_throughput(
        self,
        num_messages: int = 1000,
        message_size: int = 100,
    ) -> NMDCStressResult:
        """
        Test message sending throughput.
        
        Connects once and sends many messages to measure protocol throughput.
        """
        from verlihub.client import NMDCClient
        
        latencies = []
        errors = []
        successful = 0
        failed = 0
        messages_sent = 0
        bytes_sent = 0
        connections_made = 0
        
        start_time = time.perf_counter()
        
        try:
            client = self._create_client("throughput")
            if not client.connect(timeout=10.0):
                return NMDCStressResult(
                    test_name="Message Throughput",
                    total_operations=num_messages,
                    successful_operations=0,
                    failed_operations=num_messages,
                    total_time_seconds=0,
                    errors=["Failed to connect"],
                )
            
            connections_made = 1
            
            # Generate random message content
            for i in range(num_messages):
                message = ''.join(random.choices(string.ascii_letters + string.digits, k=message_size))
                
                timer = Timer()
                try:
                    with timer:
                        client.send_chat(f"Stress test message {i}: {message}")
                    
                    successful += 1
                    messages_sent += 1
                    bytes_sent += len(message) + 30  # Approximate protocol overhead
                    latencies.append(timer.elapsed_ms)
                    
                except Exception as e:
                    failed += 1
                    errors.append(f"Message {i}: {str(e)[:50]}")
            
            client.close()
            
        except Exception as e:
            errors.append(f"Connection error: {str(e)[:50]}")
            failed = num_messages - successful
        
        total_time = time.perf_counter() - start_time
        
        return NMDCStressResult(
            test_name="Message Throughput",
            total_operations=num_messages,
            successful_operations=successful,
            failed_operations=failed,
            total_time_seconds=total_time,
            latencies_ms=latencies,
            errors=errors,
            connections_made=connections_made,
            messages_sent=messages_sent,
            bytes_sent=bytes_sent,
        )
    
    def test_concurrent_connections(
        self,
        num_connections: int = 10,
        messages_per_connection: int = 10,
    ) -> NMDCStressResult:
        """
        Test concurrent connection handling.
        
        Creates multiple simultaneous connections to test thread safety
        and concurrent protocol handling.
        """
        from verlihub.client import NMDCClient
        
        latencies = []
        errors = []
        successful = 0
        failed = 0
        connections_made = 0
        messages_sent = 0
        
        # Thread-safe counters
        lock = threading.Lock()
        
        def worker(worker_id: int):
            nonlocal successful, failed, connections_made, messages_sent
            
            local_latencies = []
            local_errors = []
            local_successful = 0
            local_failed = 0
            local_connections = 0
            local_messages = 0
            
            try:
                client = self._create_client(f"concurrent{worker_id}")
                
                timer = Timer()
                with timer:
                    if client.connect(timeout=15.0):
                        local_connections += 1
                        
                        # Send messages
                        for j in range(messages_per_connection):
                            try:
                                client.send_chat(f"Worker {worker_id} message {j}")
                                local_messages += 1
                                local_successful += 1
                            except Exception as e:
                                local_failed += 1
                                local_errors.append(f"W{worker_id}M{j}: {str(e)[:30]}")
                        
                        client.close()
                    else:
                        local_failed += messages_per_connection
                        local_errors.append(f"Worker {worker_id} connection failed")
                
                local_latencies.append(timer.elapsed_ms)
                
            except Exception as e:
                local_failed += messages_per_connection
                local_errors.append(f"Worker {worker_id}: {str(e)[:50]}")
            
            # Update global counters
            with lock:
                latencies.extend(local_latencies)
                errors.extend(local_errors)
                successful += local_successful
                failed += local_failed
                connections_made += local_connections
                messages_sent += local_messages
        
        start_time = time.perf_counter()
        
        # Run workers concurrently
        with ThreadPoolExecutor(max_workers=num_connections) as executor:
            futures = [executor.submit(worker, i) for i in range(num_connections)]
            for future in futures:
                future.result()  # Wait for completion
        
        total_time = time.perf_counter() - start_time
        total_ops = num_connections * messages_per_connection
        
        return NMDCStressResult(
            test_name="Concurrent Connections",
            total_operations=total_ops,
            successful_operations=successful,
            failed_operations=failed,
            total_time_seconds=total_time,
            latencies_ms=latencies,
            errors=errors,
            connections_made=connections_made,
            messages_sent=messages_sent,
        )
    
    def test_protocol_robustness(
        self,
        num_tests: int = 100,
    ) -> NMDCStressResult:
        """
        Test protocol robustness with edge cases.
        
        Tests handling of:
        - Empty messages
        - Very long messages
        - Special characters
        - Unicode content
        - Rapid message bursts
        """
        from verlihub.client import NMDCClient
        
        errors = []
        successful = 0
        failed = 0
        messages_sent = 0
        
        # Test cases with various edge case messages
        test_messages = [
            "",  # Empty
            " ",  # Space only
            "\t\n\r",  # Whitespace
            "A" * 1000,  # Long message
            "A" * 10000,  # Very long message
            "<>|{}[]",  # Special chars
            "$Lock EXTENDEDPROTOCOL",  # Protocol-like content
            "Hello 世界 🌍 مرحبا",  # Unicode
            "\x00\x01\x02\x03",  # Binary chars
            "|$|$|$|",  # NMDC separators
            "<script>alert('xss')</script>",  # XSS attempt
            "'; DROP TABLE users; --",  # SQL injection attempt
            "../../../etc/passwd",  # Path traversal
            "a" * 100000,  # Extremely long
        ]
        
        start_time = time.perf_counter()
        
        try:
            client = self._create_client("robustness")
            if not client.connect(timeout=10.0):
                return NMDCStressResult(
                    test_name="Protocol Robustness",
                    total_operations=num_tests,
                    successful_operations=0,
                    failed_operations=num_tests,
                    total_time_seconds=0,
                    errors=["Failed to connect"],
                )
            
            for i in range(num_tests):
                # Pick a random test message
                msg = random.choice(test_messages)
                
                try:
                    # Try to send the message - success = didn't crash
                    client.send_chat(msg[:64000])  # Limit to 64KB for sanity
                    successful += 1
                    messages_sent += 1
                except Exception as e:
                    # Some messages might reasonably fail, that's OK
                    # We're testing that the client doesn't crash
                    if "broken pipe" in str(e).lower() or "connection" in str(e).lower():
                        failed += 1
                        errors.append(f"Test {i}: Connection lost - {str(e)[:30]}")
                        # Reconnect for remaining tests
                        try:
                            client.close()
                        except:
                            pass
                        client = self._create_client("robustness_reconnect")
                        if not client.connect(timeout=10.0):
                            break
                    else:
                        # Other errors are acceptable (message rejected, etc)
                        successful += 1
                
                # Small delay to avoid flooding
                time.sleep(0.01)
            
            client.close()
            
        except Exception as e:
            errors.append(f"Fatal error: {str(e)[:100]}")
        
        total_time = time.perf_counter() - start_time
        
        return NMDCStressResult(
            test_name="Protocol Robustness",
            total_operations=num_tests,
            successful_operations=successful,
            failed_operations=failed,
            total_time_seconds=total_time,
            errors=errors,
            messages_sent=messages_sent,
            connections_made=1,
        )
    
    def test_rapid_disconnect(
        self,
        num_cycles: int = 20,
    ) -> NMDCStressResult:
        """
        Test rapid connect/disconnect without proper handshake completion.
        
        This tests the server's ability to handle incomplete connections
        and rapid client disconnections.
        """
        import socket
        
        errors = []
        successful = 0
        failed = 0
        connections_made = 0
        latencies = []
        
        start_time = time.perf_counter()
        
        for i in range(num_cycles):
            timer = Timer()
            try:
                with timer:
                    # Raw socket connection - connect and immediately disconnect
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(5.0)
                    sock.connect((self.host, self.port))
                    connections_made += 1
                    
                    # Read a bit of data (partial handshake)
                    try:
                        data = sock.recv(100)
                    except:
                        pass
                    
                    # Close immediately
                    sock.close()
                
                successful += 1
                latencies.append(timer.elapsed_ms)
                
            except Exception as e:
                failed += 1
                errors.append(f"Cycle {i}: {str(e)[:50]}")
            
            # Minimal delay
            time.sleep(0.05)
        
        total_time = time.perf_counter() - start_time
        
        return NMDCStressResult(
            test_name="Rapid Disconnect",
            total_operations=num_cycles,
            successful_operations=successful,
            failed_operations=failed,
            total_time_seconds=total_time,
            latencies_ms=latencies,
            errors=errors,
            connections_made=connections_made,
        )
    
    def test_command_flood(
        self,
        num_commands: int = 100,
    ) -> NMDCStressResult:
        """
        Test rapid command execution.
        
        Tests the hub's ability to handle many commands in quick succession.
        """
        from verlihub.client import NMDCClient
        
        latencies = []
        errors = []
        successful = 0
        failed = 0
        messages_sent = 0
        messages_received = 0
        
        commands = [
            "!help",
            "!hubinfo",
            "!ul",
            "!time",
            "!seen admin",
            "!motd",
        ]
        
        start_time = time.perf_counter()
        
        try:
            client = self._create_client("cmdfood")
            if not client.connect(timeout=10.0):
                return NMDCStressResult(
                    test_name="Command Flood",
                    total_operations=num_commands,
                    successful_operations=0,
                    failed_operations=num_commands,
                    total_time_seconds=0,
                    errors=["Failed to connect"],
                )
            
            for i in range(num_commands):
                cmd = random.choice(commands)
                
                timer = Timer()
                try:
                    with timer:
                        responses = client.execute_command(cmd, timeout=2.0)
                    
                    successful += 1
                    messages_sent += 1
                    messages_received += len(responses)
                    latencies.append(timer.elapsed_ms)
                    
                except Exception as e:
                    failed += 1
                    errors.append(f"Cmd {i} ({cmd}): {str(e)[:30]}")
            
            client.close()
            
        except Exception as e:
            errors.append(f"Connection error: {str(e)[:50]}")
        
        total_time = time.perf_counter() - start_time
        
        return NMDCStressResult(
            test_name="Command Flood",
            total_operations=num_commands,
            successful_operations=successful,
            failed_operations=failed,
            total_time_seconds=total_time,
            latencies_ms=latencies,
            errors=errors,
            connections_made=1,
            messages_sent=messages_sent,
            messages_received=messages_received,
        )
    
    def run_all_tests(
        self,
        quick: bool = False,
    ) -> dict[str, NMDCStressResult]:
        """
        Run all NMDC stress tests.
        
        Args:
            quick: If True, run with reduced iterations for faster testing
        
        Returns:
            Dictionary mapping test name to result
        """
        results = {}
        
        if quick:
            iterations = 10
            messages = 50
        else:
            iterations = 50
            messages = 500
        
        print("\n>>> Running NMDC Connection Cycle Test...")
        results["connection_cycles"] = self.test_connection_cycles(
            num_cycles=iterations,
            delay_between_ms=50,
        )
        print(results["connection_cycles"])
        
        print("\n>>> Running NMDC Message Throughput Test...")
        results["message_throughput"] = self.test_message_throughput(
            num_messages=messages,
            message_size=100,
        )
        print(results["message_throughput"])
        
        print("\n>>> Running NMDC Concurrent Connections Test...")
        results["concurrent"] = self.test_concurrent_connections(
            num_connections=5 if quick else 10,
            messages_per_connection=5 if quick else 20,
        )
        print(results["concurrent"])
        
        print("\n>>> Running NMDC Protocol Robustness Test...")
        results["robustness"] = self.test_protocol_robustness(
            num_tests=iterations,
        )
        print(results["robustness"])
        
        print("\n>>> Running NMDC Rapid Disconnect Test...")
        results["rapid_disconnect"] = self.test_rapid_disconnect(
            num_cycles=iterations,
        )
        print(results["rapid_disconnect"])
        
        print("\n>>> Running NMDC Command Flood Test...")
        results["command_flood"] = self.test_command_flood(
            num_commands=iterations,
        )
        print(results["command_flood"])
        
        # Print summary
        print("\n" + "=" * 70)
        print("NMDC STRESS TEST SUMMARY")
        print("=" * 70)
        print(f"{'Test':<25} {'Success Rate':>15} {'Ops/sec':>12} {'p95 (ms)':>12}")
        print("-" * 70)
        for name, result in results.items():
            print(f"{name:<25} {result.success_rate:>14.1f}% {result.operations_per_second:>12.1f} {result.p95_ms:>12.2f}")
        print("=" * 70)
        
        return results


# CLI support
def main():
    """Run NMDC stress tests from command line."""
    import argparse
    
    parser = argparse.ArgumentParser(description="NMDC Protocol Stress Test")
    parser.add_argument("--host", default="localhost", help="Hub hostname")
    parser.add_argument("--port", type=int, default=4111, help="Hub port")
    parser.add_argument("--user", default="stress_test", help="Username")
    parser.add_argument("--password", default="", help="Password")
    parser.add_argument("--quick", action="store_true", help="Quick test mode")
    
    args = parser.parse_args()
    
    stress = NMDCStressTest(
        host=args.host,
        port=args.port,
        username=args.user,
        password=args.password,
    )
    
    results = stress.run_all_tests(quick=args.quick)
    
    # Calculate overall success rate
    total_success = sum(r.successful_operations for r in results.values())
    total_ops = sum(r.total_operations for r in results.values())
    overall_rate = (total_success / total_ops * 100) if total_ops > 0 else 0
    
    print(f"\nOverall Success Rate: {overall_rate:.1f}%")
    
    return 0 if overall_rate > 90 else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
