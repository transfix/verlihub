#!/usr/bin/env python3
"""
Verlihub Benchmark CLI - Performance testing tool.

Usage:
    verlihub-bench [OPTIONS] COMMAND

Commands:
    quick       Quick health check benchmark
    api         Run API endpoint benchmarks
    stress      Run stress tests
    full        Run full benchmark suite
    latency     Measure detailed latency profile
    auth        Benchmark authentication endpoint

Examples:
    verlihub-bench quick
    verlihub-bench api --requests 1000 --concurrency 20
    verlihub-bench full --include-websocket --output results.json
    verlihub-bench latency --endpoint /api/v1/hub/stats --samples 500
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path


def get_token(args: argparse.Namespace) -> str | None:
    """Get authentication token from args or login."""
    if args.token:
        return args.token
    
    if args.username and args.password:
        import httpx
        try:
            response = httpx.post(
                f"{args.url}/api/v1/auth/login",
                json={"username": args.username, "password": args.password},
                timeout=10.0,
            )
            if response.status_code == 200:
                return response.json().get("access_token")
        except Exception as e:
            print(f"Warning: Could not authenticate: {e}")
    
    return None


async def cmd_quick(args: argparse.Namespace) -> int:
    """Quick health check benchmark."""
    from verlihub.benchmarks.api_benchmarks import quick_health_check
    
    print(f"Running quick health check benchmark ({args.requests} requests)...")
    result = await quick_health_check(
        base_url=args.url,
        iterations=args.requests,
    )
    print(result)
    return 0 if result.success_rate > 95 else 1


async def cmd_api(args: argparse.Namespace) -> int:
    """Run API endpoint benchmarks."""
    from verlihub.benchmarks.api_benchmarks import create_api_benchmark_suite
    
    token = get_token(args)
    
    print(f"Running API benchmarks...")
    print(f"  URL: {args.url}")
    print(f"  Requests per endpoint: {args.requests}")
    print(f"  Concurrency: {args.concurrency}")
    print(f"  Authenticated: {'Yes' if token else 'No'}")
    print()
    
    suite = create_api_benchmark_suite(
        num_requests=args.requests,
        concurrency=args.concurrency,
    )
    results = await suite.run(base_url=args.url, token=token)
    
    print(suite.summary())
    
    if args.output:
        with open(args.output, "w") as f:
            f.write(suite.to_json())
        print(f"Results saved to: {args.output}")
    
    return 0


async def cmd_stress(args: argparse.Namespace) -> int:
    """Run stress tests."""
    from verlihub.benchmarks.api_benchmarks import create_stress_test_suite
    
    token = get_token(args)
    
    print(f"Running stress tests...")
    print(f"  URL: {args.url}")
    print(f"  Requests per endpoint: {args.requests}")
    print(f"  Concurrency: {args.concurrency}")
    print()
    
    suite = create_stress_test_suite(
        num_requests=args.requests,
        concurrency=args.concurrency,
    )
    results = await suite.run(base_url=args.url, token=token)
    
    print(suite.summary())
    
    if args.output:
        with open(args.output, "w") as f:
            f.write(suite.to_json())
        print(f"Results saved to: {args.output}")
    
    return 0


async def cmd_full(args: argparse.Namespace) -> int:
    """Run full benchmark suite."""
    from verlihub.benchmarks.api_benchmarks import run_full_benchmark
    
    token = get_token(args)
    
    print(f"Running full benchmark suite...")
    print(f"  URL: {args.url}")
    print(f"  Requests per endpoint: {args.requests}")
    print(f"  Concurrency: {args.concurrency}")
    print(f"  Include stress tests: {args.stress}")
    print(f"  Include WebSocket: {args.websocket}")
    print()
    
    results = await run_full_benchmark(
        base_url=args.url,
        token=token,
        num_requests=args.requests,
        concurrency=args.concurrency,
        include_stress=args.stress,
        include_websocket=args.websocket,
    )
    
    # Summary
    print("\n" + "=" * 70)
    print("FULL BENCHMARK SUMMARY")
    print("=" * 70)
    print(f"{'Benchmark':<35} {'Req/s':>10} {'p95 (ms)':>12} {'Success':>10}")
    print("-" * 70)
    for r in results:
        print(f"{r.name[:35]:<35} {r.requests_per_second:>10.1f} {r.p95_ms:>12.2f} {r.success_rate:>9.1f}%")
    print("=" * 70)
    
    if args.output:
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "config": {
                "url": args.url,
                "requests": args.requests,
                "concurrency": args.concurrency,
            },
            "results": [r.to_dict() for r in results],
        }
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\nResults saved to: {args.output}")
    
    return 0


async def cmd_latency(args: argparse.Namespace) -> int:
    """Measure detailed latency profile."""
    from verlihub.benchmarks.api_benchmarks import measure_latency_profile
    
    print(f"Measuring latency profile for {args.endpoint}...")
    print(f"  Samples: {args.samples}")
    print()
    
    profile = await measure_latency_profile(
        base_url=args.url,
        path=args.endpoint,
        samples=args.samples,
    )
    
    print("Latency Profile:")
    print(f"  Endpoint: {profile['endpoint']}")
    print(f"  Samples:  {profile['samples']}")
    print()
    print("  Latency (ms):")
    for key, value in profile["latency_ms"].items():
        print(f"    {key:>6}: {value:>10.3f}")
    
    if args.output:
        with open(args.output, "w") as f:
            json.dump(profile, f, indent=2)
        print(f"\nProfile saved to: {args.output}")
    
    return 0


async def cmd_auth(args: argparse.Namespace) -> int:
    """Benchmark authentication endpoint."""
    from verlihub.benchmarks.api_benchmarks import run_auth_benchmark
    
    if not args.username or not args.password:
        print("Error: --username and --password required for auth benchmark")
        return 1
    
    print(f"Running auth benchmark...")
    print(f"  URL: {args.url}")
    print(f"  Username: {args.username}")
    print(f"  Requests: {args.requests}")
    print()
    
    result = await run_auth_benchmark(
        base_url=args.url,
        username=args.username,
        password=args.password,
        num_requests=args.requests,
        concurrency=args.concurrency,
    )
    print(result)
    
    return 0 if result.success_rate > 95 else 1


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Verlihub Performance Benchmark Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    
    # Global options
    parser.add_argument(
        "--url", "-u",
        default="http://localhost:8000",
        help="API base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--token", "-t",
        help="Authentication token",
    )
    parser.add_argument(
        "--username",
        help="Username for authentication",
    )
    parser.add_argument(
        "--password",
        help="Password for authentication",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output file for results (JSON)",
    )
    parser.add_argument(
        "--requests", "-n",
        type=int,
        default=100,
        help="Number of requests per benchmark (default: 100)",
    )
    parser.add_argument(
        "--concurrency", "-c",
        type=int,
        default=10,
        help="Number of concurrent connections (default: 10)",
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Benchmark commands")
    
    # Quick command
    quick_parser = subparsers.add_parser("quick", help="Quick health check benchmark")
    
    # API command
    api_parser = subparsers.add_parser("api", help="Run API endpoint benchmarks")
    
    # Stress command
    stress_parser = subparsers.add_parser("stress", help="Run stress tests")
    
    # Full command
    full_parser = subparsers.add_parser("full", help="Run full benchmark suite")
    full_parser.add_argument(
        "--stress", "-s",
        action="store_true",
        help="Include stress tests",
    )
    full_parser.add_argument(
        "--websocket", "-w",
        action="store_true",
        help="Include WebSocket benchmarks",
    )
    
    # Latency command
    latency_parser = subparsers.add_parser("latency", help="Measure detailed latency profile")
    latency_parser.add_argument(
        "--endpoint", "-e",
        default="/health",
        help="Endpoint to profile (default: /health)",
    )
    latency_parser.add_argument(
        "--samples",
        type=int,
        default=1000,
        help="Number of samples (default: 1000)",
    )
    
    # Auth command
    auth_parser = subparsers.add_parser("auth", help="Benchmark authentication")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 0
    
    # Dispatch commands
    commands = {
        "quick": cmd_quick,
        "api": cmd_api,
        "stress": cmd_stress,
        "full": cmd_full,
        "latency": cmd_latency,
        "auth": cmd_auth,
    }
    
    cmd_func = commands.get(args.command)
    if cmd_func:
        return asyncio.run(cmd_func(args))
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
