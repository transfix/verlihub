#!/usr/bin/env python3
"""
Verlihub Integration Test Suite

Tests the full stack including:
- NMDC connection
- Python plugin loading
- FastAPI REST API
- Dashboard serving

Usage:
    python integration_test.py --hub-host localhost --hub-port 4111 --api-port 30000
"""

import argparse
import json
import sys
import time
import requests
from typing import Optional
from nmdc_client import NMDCClient


class IntegrationTestRunner:
    """Run integration tests against a Verlihub instance"""
    
    def __init__(self, hub_host: str, hub_port: int, api_port: int,
                 admin_nick: str, admin_pass: str, cors_origins: list = None):
        self.hub_host = hub_host
        self.hub_port = hub_port
        self.api_port = api_port
        self.admin_nick = admin_nick
        self.admin_pass = admin_pass
        self.cors_origins = cors_origins or []
        self.api_base = f"http://{hub_host}:{api_port}"
        self.client: Optional[NMDCClient] = None
        self.results = {"passed": 0, "failed": 0, "tests": []}
    
    def connect_to_hub(self) -> bool:
        """Connect to the hub as admin"""
        print(f"\n[TEST] Connecting to hub at {self.hub_host}:{self.hub_port}...")
        
        self.client = NMDCClient(
            host=self.hub_host,
            port=self.hub_port,
            nick=self.admin_nick,
            password=self.admin_pass
        )
        self.client.debug = True
        
        if self.client.connect(timeout=30):
            print("[TEST] ✓ Connected to hub")
            return True
        else:
            print("[TEST] ✗ Failed to connect to hub")
            return False
    
    def enable_python_plugin(self) -> bool:
        """Enable the Python plugin"""
        print("\n[TEST] Enabling Python plugin...")
        
        responses = self.client.execute_command("!onplugin python", wait_time=5.0)
        
        # Check for success indicators in responses
        for msg in responses:
            if "enabled" in msg.lower() or "already" in msg.lower() or "python" in msg.lower():
                print(f"[TEST] ✓ Python plugin response: {msg[:100]}")
                return True
        
        # Even if we don't get a clear success message, continue (might already be enabled)
        print("[TEST] ⚠ No clear success message, continuing anyway...")
        return True
    
    def start_api_server(self) -> bool:
        """Start the FastAPI server"""
        print(f"\n[TEST] Starting API server on port {self.api_port}...")
        
        # Build command with CORS origins
        cmd_parts = ["!api", "start", str(self.api_port)]
        cmd_parts.extend(self.cors_origins)
        command = " ".join(cmd_parts)
        
        print(f"[TEST] Command: {command}")
        responses = self.client.execute_command(command, wait_time=10.0)
        
        print(f"[TEST] Got {len(responses)} responses:")
        for msg in responses:
            print(f"[TEST] Response: {msg[:200]}")
        
        # Wait for server to start
        print("[TEST] Waiting for API server to start...")
        time.sleep(5)
        
        return True
    
    def wait_for_api(self, timeout: float = 30.0) -> bool:
        """Wait for the API to become available"""
        print(f"\n[TEST] Waiting for API at {self.api_base}...")
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = requests.get(f"{self.api_base}/health", timeout=2)
                if response.status_code == 200:
                    print("[TEST] ✓ API is responding")
                    return True
            except requests.exceptions.RequestException:
                pass
            time.sleep(1)
            print(".", end="", flush=True)
        
        print("\n[TEST] ✗ API did not become available")
        return False
    
    def run_test(self, name: str, test_func) -> bool:
        """Run a single test and record result"""
        print(f"\n[TEST] Running: {name}")
        try:
            result = test_func()
            if result:
                print(f"[TEST] ✓ PASSED: {name}")
                self.results["passed"] += 1
                self.results["tests"].append({"name": name, "status": "passed"})
            else:
                print(f"[TEST] ✗ FAILED: {name}")
                self.results["failed"] += 1
                self.results["tests"].append({"name": name, "status": "failed"})
            return result
        except Exception as e:
            print(f"[TEST] ✗ ERROR in {name}: {e}")
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
        if "service" not in data or "Verlihub" not in data.get("service", ""):
            print(f"  Unexpected response: {data}")
            return False
        
        print(f"  Service: {data.get('service')}")
        print(f"  Endpoints: {list(data.get('endpoints', {}).keys())}")
        return True
    
    def test_hub_info(self) -> bool:
        """Test the hub info endpoint"""
        response = requests.get(f"{self.api_base}/hub", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        data = response.json()
        print(f"  Hub info keys: {list(data.keys())[:10]}")
        return True
    
    def test_stats(self) -> bool:
        """Test the statistics endpoint"""
        response = requests.get(f"{self.api_base}/stats", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        data = response.json()
        print(f"  Users online: {data.get('users_online', 'N/A')}")
        print(f"  Hub name: {data.get('hub_name', 'N/A')}")
        return True
    
    def test_users_list(self) -> bool:
        """Test the users list endpoint"""
        response = requests.get(f"{self.api_base}/users", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        data = response.json()
        users = data.get("users", [])
        print(f"  Total users: {data.get('total', len(users))}")
        return True
    
    def test_health_endpoint(self) -> bool:
        """Test the health endpoint"""
        response = requests.get(f"{self.api_base}/health", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        data = response.json()
        print(f"  Status: {data.get('status', 'unknown')}")
        return data.get("status") == "healthy"
    
    def test_dashboard(self) -> bool:
        """Test the dashboard HTML endpoint"""
        response = requests.get(f"{self.api_base}/dashboard", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            print(f"  Unexpected content type: {content_type}")
            return False
        
        # Check that HTML contains expected content
        html = response.text
        if "Verlihub Dashboard" not in html:
            print("  Dashboard title not found")
            return False
        
        # Check that API_BASE was modified for same-origin serving
        if "const API_BASE = '';" not in html:
            print("  API_BASE not modified for same-origin serving")
            return False
        
        print(f"  Dashboard loaded successfully ({len(html)} bytes)")
        return True
    
    def test_dashboard_embed(self) -> bool:
        """Test the embedded dashboard endpoint"""
        response = requests.get(f"{self.api_base}/dashboard/embed", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            print(f"  Unexpected content type: {content_type}")
            return False
        
        print(f"  Embedded dashboard loaded ({len(response.text)} bytes)")
        return True
    
    def test_cors_headers(self) -> bool:
        """Test CORS headers are properly set"""
        if not self.cors_origins:
            print("  No CORS origins configured, skipping")
            return True
        
        origin = self.cors_origins[0]
        headers = {"Origin": origin}
        response = requests.get(f"{self.api_base}/health", headers=headers, timeout=5)
        
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        cors_header = response.headers.get("access-control-allow-origin", "")
        if origin in cors_header or cors_header == "*":
            print(f"  CORS header present: {cors_header}")
            return True
        
        print(f"  CORS header missing or incorrect: {cors_header}")
        return False
    
    def test_geo_endpoint(self) -> bool:
        """Test the geographic distribution endpoint"""
        response = requests.get(f"{self.api_base}/geo", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        data = response.json()
        print(f"  Countries in data: {len(data.get('countries', []))}")
        return True
    
    def test_share_endpoint(self) -> bool:
        """Test the share statistics endpoint"""
        response = requests.get(f"{self.api_base}/share", timeout=5)
        if response.status_code != 200:
            print(f"  Unexpected status: {response.status_code}")
            return False
        
        data = response.json()
        print(f"  Total share: {data.get('total_formatted', 'N/A')}")
        return True
    
    # =========================================================================
    # Main Test Runner
    # =========================================================================
    
    def run_all_tests(self) -> bool:
        """Run the complete test suite"""
        print("\n" + "=" * 60)
        print("VERLIHUB INTEGRATION TEST SUITE")
        print("=" * 60)
        
        # Connect and setup
        if not self.connect_to_hub():
            return False
        
        if not self.enable_python_plugin():
            return False
        
        if not self.start_api_server():
            return False
        
        if not self.wait_for_api():
            return False
        
        # Run API tests
        print("\n" + "-" * 60)
        print("RUNNING API TESTS")
        print("-" * 60)
        
        self.run_test("Root Endpoint", self.test_root_endpoint)
        self.run_test("Health Endpoint", self.test_health_endpoint)
        self.run_test("Hub Info", self.test_hub_info)
        self.run_test("Statistics", self.test_stats)
        self.run_test("Users List", self.test_users_list)
        self.run_test("Geographic Distribution", self.test_geo_endpoint)
        self.run_test("Share Statistics", self.test_share_endpoint)
        self.run_test("Dashboard HTML", self.test_dashboard)
        self.run_test("Dashboard Embed", self.test_dashboard_embed)
        self.run_test("CORS Headers", self.test_cors_headers)
        
        # Print summary
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)
        print(f"Passed: {self.results['passed']}")
        print(f"Failed: {self.results['failed']}")
        print(f"Total:  {self.results['passed'] + self.results['failed']}")
        
        if self.results["failed"] > 0:
            print("\nFailed tests:")
            for test in self.results["tests"]:
                if test["status"] != "passed":
                    print(f"  - {test['name']}: {test['status']}")
                    if "error" in test:
                        print(f"    Error: {test['error']}")
        
        # Cleanup
        if self.client:
            self.client.close()
        
        return self.results["failed"] == 0


def main():
    parser = argparse.ArgumentParser(description='Verlihub Integration Tests')
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
    
    runner = IntegrationTestRunner(
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
