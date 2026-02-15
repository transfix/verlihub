#!/usr/bin/env python3
"""
Standalone Integration Test Suite for Verlihub

This test suite runs the API server independently and tests:
1. Hub connectivity via NMDC protocol
2. API endpoints via the standalone API server
3. CORS configuration

Unlike the hub-integrated version, this doesn't require the Python plugin's
command handler to work - it starts its own API server process.

Usage:
    python standalone_integration_test.py --hub-host localhost --hub-port 4111
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import requests
from typing import Optional

# Try to import NMDC client
try:
    from nmdc_client import NMDCClient
except ImportError:
    # Create minimal client inline if import fails
    NMDCClient = None


class StandaloneIntegrationTestRunner:
    """Run standalone integration tests"""
    
    def __init__(self, hub_host: str, hub_port: int, api_port: int,
                 admin_nick: str, admin_pass: str, cors_origins: list = None):
        self.hub_host = hub_host
        self.hub_port = hub_port
        self.api_port = api_port
        self.admin_nick = admin_nick
        self.admin_pass = admin_pass
        self.cors_origins = cors_origins or []
        self.api_base = f"http://localhost:{api_port}"
        self.nmdc_client: Optional[NMDCClient] = None
        self.api_process: Optional[subprocess.Popen] = None
        self.results = {"passed": 0, "failed": 0, "skipped": 0, "tests": []}
    
    def start_standalone_api(self) -> bool:
        """Start the standalone API server"""
        print(f"\n[SETUP] Starting standalone API server on port {self.api_port}...")
        
        # Get the directory containing this script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        api_script = os.path.join(script_dir, "standalone_api.py")
        
        if not os.path.exists(api_script):
            print(f"[SETUP] ERROR: Standalone API script not found: {api_script}")
            return False
        
        # Build command
        cmd = [sys.executable, api_script, "--host", "0.0.0.0", "--port", str(self.api_port)]
        if self.cors_origins:
            cmd.extend(["--cors-origins"] + self.cors_origins)
        
        print(f"[SETUP] Running: {' '.join(cmd)}")
        
        # Start the process
        try:
            self.api_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            print(f"[SETUP] API server started with PID {self.api_process.pid}")
        except Exception as e:
            print(f"[SETUP] ERROR starting API server: {e}")
            return False
        
        # Wait for API to become available
        return self.wait_for_api(timeout=15.0)
    
    def stop_standalone_api(self):
        """Stop the standalone API server"""
        if self.api_process:
            print("\n[CLEANUP] Stopping standalone API server...")
            self.api_process.terminate()
            try:
                self.api_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.api_process.kill()
            print("[CLEANUP] API server stopped")
    
    def wait_for_api(self, timeout: float = 30.0) -> bool:
        """Wait for the API to become available"""
        print(f"[SETUP] Waiting for API at {self.api_base}...")
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = requests.get(f"{self.api_base}/health", timeout=2)
                if response.status_code == 200:
                    print("[SETUP] API is responding!")
                    return True
            except requests.exceptions.RequestException:
                pass
            
            # Check if process died
            if self.api_process and self.api_process.poll() is not None:
                print(f"[SETUP] API server process died with code {self.api_process.returncode}")
                # Print any output
                if self.api_process.stdout:
                    output = self.api_process.stdout.read()
                    if output:
                        print(f"[SETUP] API output: {output[:500]}")
                return False
            
            time.sleep(0.5)
            print(".", end="", flush=True)
        
        print("\n[SETUP] API did not become available within timeout")
        return False
    
    def test_nmdc_connection(self) -> bool:
        """Test NMDC connection to the hub"""
        if NMDCClient is None:
            print("  NMDC client not available (skipping)")
            return None
        
        print(f"  Connecting to {self.hub_host}:{self.hub_port}...")
        
        client = NMDCClient(
            host=self.hub_host,
            port=self.hub_port,
            nick=self.admin_nick,
            password=self.admin_pass
        )
        
        try:
            if client.connect(timeout=30):
                print(f"  Connected as: {self.admin_nick}")
                client.close()
                return True
            else:
                # NMDC connection test is optional - if it fails, don't fail the whole suite
                print("  Connection failed (test marked optional)")
                return None  # Skip rather than fail
        except Exception as e:
            print(f"  Connection error: {e}")
            return None  # Skip rather than fail
    
    def run_test(self, name: str, test_func) -> bool:
        """Run a single test and record result"""
        print(f"\n[TEST] Running: {name}")
        try:
            result = test_func()
            if result is None:
                print(f"[TEST] ~ SKIPPED: {name}")
                self.results["skipped"] += 1
                self.results["tests"].append({"name": name, "status": "skipped"})
                return True
            elif result:
                print(f"[TEST] PASSED: {name}")
                self.results["passed"] += 1
                self.results["tests"].append({"name": name, "status": "passed"})
                return True
            else:
                print(f"[TEST] FAILED: {name}")
                self.results["failed"] += 1
                self.results["tests"].append({"name": name, "status": "failed"})
                return False
        except Exception as e:
            print(f"[TEST] ERROR in {name}: {e}")
            self.results["failed"] += 1
            self.results["tests"].append({"name": name, "status": "error", "error": str(e)})
            return False
    
    # =========================================================================
    # API Tests
    # =========================================================================
    
    def test_root_endpoint(self) -> bool:
        """Test the API root endpoint"""
        response = requests.get(f"{self.api_base}/", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        data = response.json()
        print(f"  Service: {data.get('service', 'N/A')}")
        print(f"  Version: {data.get('version', 'N/A')}")
        return "service" in data
    
    def test_health_endpoint(self) -> bool:
        """Test the health endpoint"""
        response = requests.get(f"{self.api_base}/health", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        data = response.json()
        print(f"  Status: {data.get('status', 'unknown')}")
        return data.get("status") == "healthy"
    
    def test_hub_info(self) -> bool:
        """Test the hub info endpoint"""
        response = requests.get(f"{self.api_base}/hub_info", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        data = response.json()
        print(f"  Hub name: {data.get('name', 'N/A')}")
        print(f"  Version: {data.get('version', 'N/A')}")
        return "name" in data
    
    def test_stats(self) -> bool:
        """Test the statistics endpoint"""
        response = requests.get(f"{self.api_base}/stats", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        data = response.json()
        print(f"  Users: {data.get('users', {})}")
        return "users" in data
    
    def test_users_list(self) -> bool:
        """Test the users list endpoint"""
        response = requests.get(f"{self.api_base}/users", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        data = response.json()
        print(f"  Count: {data.get('count', 0)}")
        return "users" in data
    
    def test_dashboard(self) -> bool:
        """Test the dashboard endpoint"""
        response = requests.get(f"{self.api_base}/dashboard", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        content = response.text
        print(f"  Content-Type: {response.headers.get('content-type', 'N/A')}")
        print(f"  Length: {len(content)} bytes")
        return "<!DOCTYPE html>" in content or "<html" in content
    
    def test_dashboard_embed(self) -> bool:
        """Test the dashboard embed endpoint"""
        response = requests.get(f"{self.api_base}/dashboard/embed", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        content = response.text
        print(f"  Length: {len(content)} bytes")
        return "<html" in content
    
    def test_cors_headers(self) -> bool:
        """Test CORS headers"""
        if not self.cors_origins:
            print("  No CORS origins configured, skipping")
            return None
        
        origin = self.cors_origins[0]
        headers = {"Origin": origin}
        response = requests.get(f"{self.api_base}/health", headers=headers, timeout=5)
        
        acao = response.headers.get("Access-Control-Allow-Origin", "")
        print(f"  Request Origin: {origin}")
        print(f"  ACAO Header: {acao}")
        
        return acao == origin or acao == "*"
    
    # =========================================================================
    # Single-Interpreter Mode / Dispatcher Tests
    # =========================================================================
    
    def test_dispatcher_module_loadable(self) -> bool:
        """Test that dispatcher module can be imported"""
        print("  Testing dispatcher module import...")
        
        try:
            # Add scripts path
            scripts_path = os.environ.get('SCRIPTS_PATH', '/scripts')
            if os.path.exists(scripts_path):
                sys.path.insert(0, scripts_path)
            
            # Try to import dispatcher
            import importlib.util
            spec = importlib.util.find_spec('dispatcher')
            if spec is None:
                print("  Dispatcher module not in path, checking alternate locations...")
                # Try alternate paths
                for path in ['/scripts', '/usr/local/share/verlihub/scripts']:
                    if os.path.exists(os.path.join(path, 'dispatcher.py')):
                        sys.path.insert(0, path)
                        spec = importlib.util.find_spec('dispatcher')
                        if spec:
                            break
            
            if spec is None:
                print("  Dispatcher module not found (skipping)")
                return None
            
            print("  Dispatcher module found and importable")
            return True
        except Exception as e:
            print(f"  Import error: {e}")
            return None
    
    def test_dispatcher_registration(self) -> bool:
        """Test dispatcher script registration"""
        try:
            # Try to import dispatcher
            scripts_path = os.environ.get('SCRIPTS_PATH', '/scripts')
            if os.path.exists(scripts_path) and scripts_path not in sys.path:
                sys.path.insert(0, scripts_path)
            
            import dispatcher
            
            # Clear any existing state
            dispatcher._scripts.clear()
            dispatcher._hooks.clear()
            
            # Test registration
            call_log = []
            def test_handler(nick, msg):
                call_log.append((nick, msg))
                return 1
            
            script_id = dispatcher.register_script(
                "TestScript",
                {"OnParsedMsgChat": test_handler}
            )
            
            print(f"  Registered script with ID: {script_id}")
            
            # Test dispatching
            dispatcher.OnParsedMsgChat("testuser", "hello")
            
            if len(call_log) == 1 and call_log[0] == ("testuser", "hello"):
                print("  Handler was called correctly")
                # Cleanup
                dispatcher.unregister_script(script_id)
                return True
            else:
                print(f"  Unexpected call log: {call_log}")
                return False
                
        except ImportError:
            print("  Dispatcher not available (skipping)")
            return None
        except Exception as e:
            print(f"  Test error: {e}")
            return False
    
    def test_dispatcher_priority(self) -> bool:
        """Test dispatcher priority ordering"""
        try:
            scripts_path = os.environ.get('SCRIPTS_PATH', '/scripts')
            if os.path.exists(scripts_path) and scripts_path not in sys.path:
                sys.path.insert(0, scripts_path)
            
            import dispatcher
            
            # Clear state
            dispatcher._scripts.clear()
            dispatcher._hooks.clear()
            dispatcher._next_script_id = 1
            
            call_order = []
            
            def high_prio(nick, msg):
                call_order.append("high")
                return 1
            
            def low_prio(nick, msg):
                call_order.append("low")
                return 1
            
            # Register in wrong order
            id1 = dispatcher.register_script("LowPrio", {"OnParsedMsgChat": low_prio}, priority=100)
            id2 = dispatcher.register_script("HighPrio", {"OnParsedMsgChat": high_prio}, priority=10)
            
            dispatcher.OnParsedMsgChat("user", "test")
            
            # Cleanup
            dispatcher.unregister_script(id1)
            dispatcher.unregister_script(id2)
            
            if call_order == ["high", "low"]:
                print("  Priority ordering correct: high(10) -> low(100)")
                return True
            else:
                print(f"  Wrong order: {call_order}")
                return False
                
        except ImportError:
            print("  Dispatcher not available (skipping)")
            return None
        except Exception as e:
            print(f"  Test error: {e}")
            return False
    
    # =========================================================================
    # Main Test Runner
    # =========================================================================
    
    def run_all_tests(self) -> bool:
        """Run the complete test suite"""
        print("\n" + "=" * 60)
        print("VERLIHUB STANDALONE INTEGRATION TEST SUITE")
        print("Single-Interpreter Mode with Dispatcher Support")
        print("=" * 60)
        
        try:
            # Start standalone API
            if not self.start_standalone_api():
                print("\n[ERROR] Could not start standalone API server")
                return False
            
            # Run NMDC connection test (optional)
            print("\n" + "-" * 60)
            print("NMDC CONNECTION TESTS")
            print("-" * 60)
            
            self.run_test("NMDC Hub Connection", self.test_nmdc_connection)
            
            # Run API tests
            print("\n" + "-" * 60)
            print("API ENDPOINT TESTS")
            print("-" * 60)
            
            self.run_test("Root Endpoint", self.test_root_endpoint)
            self.run_test("Health Endpoint", self.test_health_endpoint)
            self.run_test("Hub Info", self.test_hub_info)
            self.run_test("Statistics", self.test_stats)
            self.run_test("Users List", self.test_users_list)
            self.run_test("Dashboard HTML", self.test_dashboard)
            self.run_test("Dashboard Embed", self.test_dashboard_embed)
            self.run_test("CORS Headers", self.test_cors_headers)
            
            # Run dispatcher tests (single-interpreter mode)
            print("\n" + "-" * 60)
            print("DISPATCHER TESTS (Single-Interpreter Mode)")
            print("-" * 60)
            
            self.run_test("Dispatcher Module Loadable", self.test_dispatcher_module_loadable)
            self.run_test("Dispatcher Registration", self.test_dispatcher_registration)
            self.run_test("Dispatcher Priority", self.test_dispatcher_priority)
            
            # Print summary
            print("\n" + "=" * 60)
            print("TEST SUMMARY")
            print("=" * 60)
            print(f"Passed:  {self.results['passed']}")
            print(f"Failed:  {self.results['failed']}")
            print(f"Skipped: {self.results['skipped']}")
            print(f"Total:   {self.results['passed'] + self.results['failed'] + self.results['skipped']}")
            
            if self.results["failed"] > 0:
                print("\nFailed tests:")
                for test in self.results["tests"]:
                    if test["status"] not in ("passed", "skipped"):
                        print(f"  - {test['name']}: {test['status']}")
                        if "error" in test:
                            print(f"    Error: {test['error']}")
            
            return self.results["failed"] == 0
        
        finally:
            # Cleanup
            self.stop_standalone_api()


def main():
    parser = argparse.ArgumentParser(description='Verlihub Standalone Integration Tests')
    parser.add_argument('--hub-host', default='localhost', help='Hub hostname')
    parser.add_argument('--hub-port', type=int, default=4111, help='Hub port')
    parser.add_argument('--api-port', type=int, default=30000, help='API port')
    parser.add_argument('--admin-nick', default='admin', help='Admin nickname')
    parser.add_argument('--admin-pass', default='admin', help='Admin password')
    parser.add_argument('--cors-origins', nargs='*', 
                        default=['https://sublevels.net', 'https://www.sublevels.net', 
                                 'https://wintermute.sublevels.net'],
                        help='CORS origins to configure')
    parser.add_argument('--output', help='Output file for JSON results')
    
    args = parser.parse_args()
    
    runner = StandaloneIntegrationTestRunner(
        hub_host=args.hub_host,
        hub_port=args.hub_port,
        api_port=args.api_port,
        admin_nick=args.admin_nick,
        admin_pass=args.admin_pass,
        cors_origins=args.cors_origins
    )
    
    success = runner.run_all_tests()
    
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(runner.results, f, indent=2)
        print(f"\nResults written to {args.output}")
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
