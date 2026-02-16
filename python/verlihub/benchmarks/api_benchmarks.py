"""
Pre-defined benchmarks for Verlihub API endpoints.

This module provides ready-to-run benchmark scenarios for:
- Health and status endpoints
- Hub information endpoints
- User management endpoints
- Authentication endpoints
- WebSocket connections
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from verlihub.benchmarks.core import (
    BenchmarkRunner,
    BenchmarkResult,
    BenchmarkSuite,
    Timer,
)


def create_api_benchmark_suite(
    num_requests: int = 100,
    concurrency: int = 10,
) -> BenchmarkSuite:
    """
    Create a comprehensive benchmark suite for the API.
    
    Args:
        num_requests: Number of requests per benchmark
        concurrency: Concurrent connections
    
    Returns:
        BenchmarkSuite ready to run
    """
    suite = BenchmarkSuite("Verlihub API Benchmarks")
    
    # Health and status endpoints
    suite.add_endpoint(
        name="GET /health",
        method="GET",
        path="/health",
        num_requests=num_requests,
        concurrency=concurrency,
    )
    
    # Hub endpoints
    suite.add_endpoint(
        name="GET /api/v1/hub/stats",
        method="GET",
        path="/api/v1/hub/stats",
        num_requests=num_requests,
        concurrency=concurrency,
    )
    
    suite.add_endpoint(
        name="GET /api/v1/hub/info",
        method="GET",
        path="/api/v1/hub/info",
        num_requests=num_requests,
        concurrency=concurrency,
    )
    
    # User endpoints (may require auth)
    suite.add_endpoint(
        name="GET /api/v1/users/online",
        method="GET",
        path="/api/v1/users/online",
        num_requests=num_requests,
        concurrency=concurrency,
    )
    
    suite.add_endpoint(
        name="GET /api/v1/users/registered",
        method="GET",
        path="/api/v1/users/registered",
        num_requests=num_requests,
        concurrency=concurrency,
    )
    
    # Bans endpoint
    suite.add_endpoint(
        name="GET /api/v1/bans/",
        method="GET",
        path="/api/v1/bans/",
        num_requests=num_requests,
        concurrency=concurrency,
    )
    
    # Auth endpoint (check token)
    suite.add_endpoint(
        name="GET /api/v1/auth/me",
        method="GET",
        path="/api/v1/auth/me",
        num_requests=num_requests,
        concurrency=concurrency,
    )
    
    # Console commands endpoint
    suite.add_endpoint(
        name="GET /api/v1/console/commands",
        method="GET",
        path="/api/v1/console/commands",
        num_requests=num_requests,
        concurrency=concurrency,
    )
    
    return suite


def create_stress_test_suite(
    num_requests: int = 1000,
    concurrency: int = 50,
) -> BenchmarkSuite:
    """
    Create a stress test suite with higher load.
    
    Args:
        num_requests: Number of requests per benchmark (default 1000)
        concurrency: Concurrent connections (default 50)
    """
    suite = BenchmarkSuite("Verlihub Stress Test")
    
    # Focus on high-traffic endpoints
    suite.add_endpoint(
        name="Stress: /health",
        method="GET",
        path="/health",
        num_requests=num_requests,
        concurrency=concurrency,
    )
    
    suite.add_endpoint(
        name="Stress: /api/v1/hub/stats",
        method="GET",
        path="/api/v1/hub/stats",
        num_requests=num_requests,
        concurrency=concurrency,
    )
    
    suite.add_endpoint(
        name="Stress: /api/v1/users/online",
        method="GET",
        path="/api/v1/users/online",
        num_requests=num_requests,
        concurrency=concurrency,
    )
    
    return suite


async def benchmark_websocket(
    base_url: str = "ws://localhost:8000",
    path: str = "/ws/hub",
    token: Optional[str] = None,
    num_connections: int = 10,
    messages_per_connection: int = 100,
    message_interval_ms: float = 10,
) -> BenchmarkResult:
    """
    Benchmark WebSocket connection performance.
    
    Args:
        base_url: WebSocket base URL
        path: WebSocket endpoint path
        token: Authentication token
        num_connections: Number of concurrent connections
        messages_per_connection: Messages to receive per connection
        message_interval_ms: Wait time between connection attempts
    
    Returns:
        BenchmarkResult with WebSocket performance metrics
    """
    try:
        import websockets
    except ImportError:
        raise ImportError("websockets package required: pip install websockets")
    
    latencies = []
    errors = []
    successful = 0
    failed = 0
    
    url = f"{base_url}{path}"
    if token:
        url = f"{url}?token={token}"
    
    start_time = time.perf_counter()
    
    async def connect_and_receive():
        nonlocal successful, failed
        timer = Timer()
        try:
            with timer:
                async with websockets.connect(url) as ws:
                    # Receive some messages
                    for _ in range(messages_per_connection):
                        try:
                            await asyncio.wait_for(ws.recv(), timeout=5.0)
                        except asyncio.TimeoutError:
                            break
            
            successful += 1
            latencies.append(timer.elapsed_ms)
        except Exception as e:
            failed += 1
            errors.append(str(e)[:100])
    
    # Run concurrent connections
    tasks = []
    for _ in range(num_connections):
        tasks.append(connect_and_receive())
        await asyncio.sleep(message_interval_ms / 1000)
    
    await asyncio.gather(*tasks, return_exceptions=True)
    total_time = time.perf_counter() - start_time
    
    return BenchmarkResult(
        name=f"WebSocket {path}",
        total_requests=num_connections,
        successful_requests=successful,
        failed_requests=failed,
        total_time_seconds=total_time,
        latencies_ms=latencies,
        errors=errors,
    )


async def run_auth_benchmark(
    base_url: str = "http://localhost:8000",
    username: str = "admin",
    password: str = "admin",
    num_requests: int = 100,
    concurrency: int = 10,
) -> BenchmarkResult:
    """
    Benchmark authentication endpoint performance.
    
    Tests the login flow to measure auth system performance.
    """
    import httpx
    
    runner = BenchmarkRunner(base_url=base_url)
    client = await runner._get_client()
    
    async def login():
        return await client.post("/api/v1/auth/login", json={
            "username": username,
            "password": password,
        })
    
    try:
        result = await runner.run_benchmark(
            name="POST /api/v1/auth/login",
            func=login,
            num_requests=num_requests,
            concurrency=concurrency,
        )
    finally:
        await runner.close()
    
    return result


async def run_full_benchmark(
    base_url: str = "http://localhost:8000",
    token: Optional[str] = None,
    num_requests: int = 100,
    concurrency: int = 10,
    include_stress: bool = False,
    include_websocket: bool = False,
) -> list[BenchmarkResult]:
    """
    Run a full benchmark of all API endpoints.
    
    Args:
        base_url: API base URL
        token: Authentication token
        num_requests: Requests per endpoint
        concurrency: Concurrent connections
        include_stress: Include stress tests
        include_websocket: Include WebSocket tests
    
    Returns:
        List of BenchmarkResult objects
    """
    results = []
    
    # Run standard API benchmarks
    print("\n>>> Running API Benchmarks...")
    suite = create_api_benchmark_suite(num_requests, concurrency)
    api_results = await suite.run(base_url=base_url, token=token)
    results.extend(api_results)
    
    # Run stress tests if requested
    if include_stress:
        print("\n>>> Running Stress Tests...")
        stress_suite = create_stress_test_suite(
            num_requests=num_requests * 10,
            concurrency=concurrency * 5,
        )
        stress_results = await stress_suite.run(base_url=base_url, token=token)
        results.extend(stress_results)
    
    # Run WebSocket tests if requested
    if include_websocket:
        print("\n>>> Running WebSocket Benchmarks...")
        ws_base = base_url.replace("http://", "ws://").replace("https://", "wss://")
        try:
            ws_result = await benchmark_websocket(
                base_url=ws_base,
                path="/ws/hub",
                token=token,
                num_connections=concurrency,
                messages_per_connection=10,
            )
            print(ws_result)
            results.append(ws_result)
        except Exception as e:
            print(f"WebSocket benchmark failed: {e}")
    
    return results


# Quick benchmark functions for common scenarios
async def quick_health_check(
    base_url: str = "http://localhost:8000",
    iterations: int = 100,
) -> BenchmarkResult:
    """Quick benchmark of just the health endpoint."""
    runner = BenchmarkRunner(base_url=base_url)
    client = await runner._get_client()
    
    try:
        result = await runner.run_benchmark(
            name="Quick Health Check",
            func=lambda: client.get("/health"),
            num_requests=iterations,
            concurrency=10,
        )
    finally:
        await runner.close()
    
    return result


async def measure_latency_profile(
    base_url: str = "http://localhost:8000",
    path: str = "/health",
    samples: int = 1000,
) -> dict:
    """
    Measure detailed latency profile for a single endpoint.
    
    Returns a dictionary with detailed latency statistics.
    """
    import httpx
    
    latencies = []
    
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        # Warmup
        for _ in range(10):
            await client.get(path)
        
        # Collect samples sequentially for accurate measurement
        for _ in range(samples):
            timer = Timer()
            with timer:
                await client.get(path)
            latencies.append(timer.elapsed_ms)
    
    sorted_latencies = sorted(latencies)
    
    return {
        "endpoint": path,
        "samples": samples,
        "latency_ms": {
            "min": round(sorted_latencies[0], 3),
            "max": round(sorted_latencies[-1], 3),
            "mean": round(sum(latencies) / len(latencies), 3),
            "p50": round(sorted_latencies[int(len(sorted_latencies) * 0.50)], 3),
            "p75": round(sorted_latencies[int(len(sorted_latencies) * 0.75)], 3),
            "p90": round(sorted_latencies[int(len(sorted_latencies) * 0.90)], 3),
            "p95": round(sorted_latencies[int(len(sorted_latencies) * 0.95)], 3),
            "p99": round(sorted_latencies[int(len(sorted_latencies) * 0.99)], 3),
            "p999": round(sorted_latencies[int(len(sorted_latencies) * 0.999)] if samples >= 1000 else 0, 3),
        },
    }
