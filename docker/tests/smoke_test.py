#!/usr/bin/env python3
"""
Verlihub-py Smoke Test Suite

Tests verlihub-py startup with different database backends.
Verifies:
1. Server starts successfully with config file
2. API endpoints are accessible via HubClient
3. Database connectivity works
4. NMDC client connectivity and messaging

Usage:
    # Run all tests
    python smoke_test.py
    
    # Test specific config
    python smoke_test.py --config configs/sqlite-memory.yml
    
    # With API URL
    python smoke_test.py --api-url http://localhost:8000
    
    # With NMDC testing (requires hub with NMDC port)
    python smoke_test.py --hub-host localhost --hub-port 4111
"""

import argparse
import os
import subprocess
import sys
import time
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List

# Import verlihub client modules
from verlihub.client.api import HubClient, HubClientError, AuthenticationError
from verlihub.client.nmdc import NMDCClient, NMDCError, NMDCConnectionError


class SmokeTestRunner:
    """Run smoke tests against a verlihub-py instance"""
    
    def __init__(
        self,
        api_url: str = "http://localhost:8000",
        admin_user: str = "admin",
        admin_pass: str = "admin123",
        hub_host: Optional[str] = None,
        hub_port: int = 4111,
    ):
        self.api_url = api_url.rstrip("/")
        self.admin_user = admin_user
        self.admin_pass = admin_pass
        self.hub_host = hub_host
        self.hub_port = hub_port
        self.client: Optional[HubClient] = None
        self.results: Dict[str, Any] = {
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "tests": [],
        }
    
    def record_result(self, name: str, passed: bool, message: str = "", skipped: bool = False):
        """Record a test result"""
        status = "SKIP" if skipped else ("PASS" if passed else "FAIL")
        self.results["tests"].append({
            "name": name,
            "status": status,
            "message": message,
        })
        
        if skipped:
            self.results["skipped"] += 1
            print(f"  [SKIP] {name}: {message}")
        elif passed:
            self.results["passed"] += 1
            print(f"  [PASS] {name}")
        else:
            self.results["failed"] += 1
            print(f"  [FAIL] {name}: {message}")
    
    def wait_for_api(self, timeout: float = 60.0) -> bool:
        """Wait for API to become available using HubClient"""
        print(f"Waiting for API at {self.api_url}...")
        
        start = time.time()
        while time.time() - start < timeout:
            try:
                # Try to create client and check connection
                with HubClient(self.api_url, timeout=5.0) as client:
                    info = client.get_hub_info()
                    if info:
                        print(f"  API ready after {time.time() - start:.1f}s")
                        return True
            except HubClientError:
                pass
            except Exception:
                pass
            time.sleep(1)
        
        print(f"  API not ready after {timeout}s")
        return False
    
    def test_hub_info(self) -> bool:
        """Test hub info endpoint"""
        try:
            info = self.client.get_hub_info()
            if info and "name" in info:
                self.record_result("hub_info", True)
                return True
            self.record_result("hub_info", False, "Missing hub name in response")
            return False
        except Exception as e:
            self.record_result("hub_info", False, str(e))
            return False
    
    def test_login(self) -> bool:
        """Test authentication via HubClient"""
        try:
            result = self.client.login(self.admin_user, self.admin_pass)
            if result and self.client.is_authenticated:
                self.record_result("auth_login", True)
                return True
            self.record_result("auth_login", False, "Login failed")
            return False
        except AuthenticationError as e:
            self.record_result("auth_login", False, str(e))
            return False
        except Exception as e:
            self.record_result("auth_login", False, str(e))
            return False
    
    def test_authenticated_endpoints(self) -> bool:
        """Test endpoints that require authentication"""
        if not self.client.is_authenticated:
            self.record_result("authenticated_endpoints", False, "Not authenticated", skipped=True)
            return False
            
        try:
            # Test user class (should have some value after login)
            user_class = self.client.user_class
            if user_class >= 0:
                self.record_result("authenticated_endpoints", True)
                return True
            self.record_result("authenticated_endpoints", False, "Invalid user class")
            return False
        except Exception as e:
            self.record_result("authenticated_endpoints", False, str(e))
            return False
    
    def test_hub_stats(self) -> bool:
        """Test hub statistics endpoint"""
        try:
            stats = self.client.get_hub_stats()
            if stats and isinstance(stats, dict):
                self.record_result("hub_stats", True)
                return True
            self.record_result("hub_stats", False, "Invalid stats response")
            return False
        except Exception as e:
            self.record_result("hub_stats", False, str(e))
            return False
    
    def test_user_count(self) -> bool:
        """Test user count retrieval"""
        try:
            count = self.client.get_user_count()
            if isinstance(count, int) and count >= 0:
                self.record_result("user_count", True)
                return True
            self.record_result("user_count", False, f"Invalid count: {count}")
            return False
        except Exception as e:
            self.record_result("user_count", False, str(e))
            return False
    
    def test_nmdc_connection(self) -> bool:
        """Test NMDC client connection to hub"""
        if not self.hub_host:
            self.record_result("nmdc_connection", False, "No hub host configured", skipped=True)
            return False
        
        try:
            with NMDCClient(
                host=self.hub_host,
                port=self.hub_port,
                nick="SmokeTestBot1",
                password=self.admin_pass,
            ) as client:
                if client.is_connected:
                    self.record_result("nmdc_connection", True)
                    return True
                self.record_result("nmdc_connection", False, "Failed to connect")
                return False
        except NMDCConnectionError as e:
            self.record_result("nmdc_connection", False, f"Connection error: {e}")
            return False
        except NMDCError as e:
            self.record_result("nmdc_connection", False, f"NMDC error: {e}")
            return False
        except Exception as e:
            self.record_result("nmdc_connection", False, str(e))
            return False
    
    def test_nmdc_messaging(self) -> bool:
        """Test NMDC client-to-client messaging"""
        if not self.hub_host:
            self.record_result("nmdc_messaging", False, "No hub host configured", skipped=True)
            return False
        
        received_messages: List[str] = []
        message_event = threading.Event()
        test_message = f"SmokeTest-{time.time()}"
        
        def on_pm(from_nick: str, to_nick: str, message: str):
            if test_message in message:
                received_messages.append(message)
                message_event.set()
        
        try:
            # Connect two clients
            client1 = NMDCClient(
                host=self.hub_host,
                port=self.hub_port,
                nick="SmokeTestSender",
                password=self.admin_pass,
            )
            
            client2 = NMDCClient(
                host=self.hub_host,
                port=self.hub_port,
                nick="SmokeTestReceiver",
                password=self.admin_pass,
            )
            client2.on_private_message = on_pm
            
            # Connect both clients
            if not client1.connect(timeout=15.0):
                self.record_result("nmdc_messaging", False, "Client1 failed to connect")
                return False
            
            if not client2.connect(timeout=15.0):
                client1.close()
                self.record_result("nmdc_messaging", False, "Client2 failed to connect")
                return False
            
            # Wait a moment for both to settle
            time.sleep(1)
            
            # Send message from client1 to client2
            client1.send_pm("SmokeTestReceiver", test_message)
            
            # Wait for message to be received
            if message_event.wait(timeout=10.0):
                self.record_result("nmdc_messaging", True)
                result = True
            else:
                self.record_result("nmdc_messaging", False, "Message not received within timeout")
                result = False
            
            # Cleanup
            client1.close()
            client2.close()
            
            return result
            
        except NMDCError as e:
            self.record_result("nmdc_messaging", False, f"NMDC error: {e}")
            return False
        except Exception as e:
            self.record_result("nmdc_messaging", False, str(e))
            return False
    
    def run_all_tests(self) -> bool:
        """Run all smoke tests"""
        print("\n" + "="*60)
        print("Verlihub-py Smoke Tests")
        print("="*60)
        
        # Wait for API
        if not self.wait_for_api():
            self.record_result("api_available", False, "API not reachable")
            return False
        self.record_result("api_available", True)
        
        # Create persistent client for API tests
        self.client = HubClient(self.api_url, timeout=30.0)
        
        try:
            # Run API tests
            print("\nRunning API tests...")
            self.test_hub_info()
            self.test_hub_stats()
            self.test_user_count()
            self.test_login()
            self.test_authenticated_endpoints()
            
            # Run NMDC tests
            print("\nRunning NMDC tests...")
            self.test_nmdc_connection()
            self.test_nmdc_messaging()
            
        finally:
            self.client.close()
        
        # Summary
        print("\n" + "="*60)
        print("Results Summary")
        print("="*60)
        print(f"  Passed:  {self.results['passed']}")
        print(f"  Failed:  {self.results['failed']}")
        print(f"  Skipped: {self.results['skipped']}")
        
        return self.results["failed"] == 0


class ServerProcess:
    """Manage a verlihub-py server process"""
    
    def __init__(
        self,
        config_file: Optional[str] = None,
        config_dir: Optional[str] = None,
        port: int = 8000,
    ):
        self.config_file = config_file
        self.config_dir = config_dir
        self.port = port
        self.process: Optional[subprocess.Popen] = None
    
    def start(self) -> bool:
        """Start the server"""
        cmd = [sys.executable, "-m", "verlihub.server"]
        
        if self.config_file:
            cmd.extend(["-c", self.config_file])
        if self.config_dir:
            cmd.extend(["--config-dir", self.config_dir])
        
        cmd.extend(["--port", str(self.port)])
        
        print(f"Starting server: {' '.join(cmd)}")
        
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            time.sleep(2)  # Initial startup time
            
            if self.process.poll() is not None:
                # Process exited
                stdout = self.process.stdout.read() if self.process.stdout else ""
                print(f"Server failed to start:\n{stdout}")
                return False
            
            return True
        except Exception as e:
            print(f"Failed to start server: {e}")
            return False
    
    def stop(self):
        """Stop the server"""
        if self.process:
            print("Stopping server...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None


def run_external_tests(
    api_url: str, 
    admin_user: str, 
    admin_pass: str,
    hub_host: Optional[str] = None,
    hub_port: int = 4111,
) -> bool:
    """Run tests against an external server"""
    runner = SmokeTestRunner(
        api_url=api_url,
        admin_user=admin_user,
        admin_pass=admin_pass,
        hub_host=hub_host,
        hub_port=hub_port,
    )
    return runner.run_all_tests()


def run_with_config(config_file: str, port: int = 8000, hub_port: int = 4111) -> bool:
    """Start server with config file and run tests"""
    print(f"\n{'='*60}")
    print(f"Testing with config: {config_file}")
    print('='*60)
    
    server = ServerProcess(config_file=config_file, port=port)
    
    if not server.start():
        return False
    
    try:
        runner = SmokeTestRunner(
            api_url=f"http://localhost:{port}",
            admin_user="admin",
            admin_pass="admin123",
            hub_host="localhost",
            hub_port=hub_port,
        )
        return runner.run_all_tests()
    finally:
        server.stop()


def run_default_startup(config_dir: str, port: int = 8000) -> bool:
    """Test default startup with no config file"""
    print(f"\n{'='*60}")
    print(f"Testing default startup (config_dir: {config_dir})")
    print('='*60)
    
    # Set environment for API auth
    env = os.environ.copy()
    env["VH_API_USERNAME"] = "admin"
    env["VH_API_PASSWORD"] = "admin123"
    env["VH_API_PORT"] = str(port)
    env["VH_API_HOST"] = "0.0.0.0"
    
    cmd = [sys.executable, "-m", "verlihub.server", "--config-dir", config_dir]
    print(f"Starting server: {' '.join(cmd)}")
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    
    try:
        time.sleep(3)  # Startup time
        
        if process.poll() is not None:
            stdout = process.stdout.read() if process.stdout else ""
            print(f"Server failed to start:\n{stdout}")
            return False
        
        runner = SmokeTestRunner(
            api_url=f"http://localhost:{port}",
            admin_user="admin",
            admin_pass="admin123",
            # No NMDC tests for default startup (API only mode)
            hub_host=None,
        )
        return runner.run_all_tests()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def main():
    parser = argparse.ArgumentParser(description="Verlihub-py smoke tests")
    parser.add_argument(
        "--config", "-c",
        help="Config file to test with",
    )
    parser.add_argument(
        "--config-dir",
        help="Config directory to test with (default startup)",
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="API URL for external server tests",
    )
    parser.add_argument(
        "--hub-host",
        help="Hub hostname for NMDC tests",
    )
    parser.add_argument(
        "--hub-port",
        type=int,
        default=4111,
        help="Hub NMDC port for NMDC tests",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for local server tests",
    )
    parser.add_argument(
        "--admin-user",
        default="admin",
        help="Admin username",
    )
    parser.add_argument(
        "--admin-pass",
        default="admin123",
        help="Admin password",
    )
    parser.add_argument(
        "--external",
        action="store_true",
        help="Test against external server (don't start local)",
    )
    parser.add_argument(
        "--all-configs",
        action="store_true",
        help="Test all config files in configs/ directory",
    )
    args = parser.parse_args()
    
    if args.external:
        # Test against external server
        success = run_external_tests(
            api_url=args.api_url,
            admin_user=args.admin_user,
            admin_pass=args.admin_pass,
            hub_host=args.hub_host,
            hub_port=args.hub_port,
        )
    elif args.config:
        # Test with specific config
        success = run_with_config(args.config, args.port, args.hub_port)
    elif args.config_dir:
        # Test default startup
        success = run_default_startup(args.config_dir, args.port)
    elif args.all_configs:
        # Test all configs
        configs_dir = Path(__file__).parent / "configs"
        configs = list(configs_dir.glob("*.yml"))
        
        if not configs:
            print(f"No config files found in {configs_dir}")
            sys.exit(1)
        
        all_passed = True
        for i, config in enumerate(configs):
            port = 8000 + i  # Different port for each
            hub_port = 4111 + i
            if not run_with_config(str(config), port, hub_port):
                all_passed = False
        
        success = all_passed
    else:
        # Default: test with default startup in temp dir
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            success = run_default_startup(tmpdir, args.port)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
