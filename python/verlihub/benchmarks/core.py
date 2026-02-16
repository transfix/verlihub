"""
Core benchmark utilities and runner.
"""
from __future__ import annotations

import asyncio
import statistics
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Optional
import json


@dataclass
class BenchmarkResult:
    """Results from a benchmark run."""
    
    name: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    total_time_seconds: float
    latencies_ms: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.total_requests == 0:
            return 0.0
        return (self.successful_requests / self.total_requests) * 100
    
    @property
    def requests_per_second(self) -> float:
        """Calculate throughput."""
        if self.total_time_seconds == 0:
            return 0.0
        return self.successful_requests / self.total_time_seconds
    
    @property
    def p50_ms(self) -> float:
        """50th percentile latency."""
        return calculate_percentiles(self.latencies_ms, 50)
    
    @property
    def p95_ms(self) -> float:
        """95th percentile latency."""
        return calculate_percentiles(self.latencies_ms, 95)
    
    @property
    def p99_ms(self) -> float:
        """99th percentile latency."""
        return calculate_percentiles(self.latencies_ms, 99)
    
    @property
    def avg_ms(self) -> float:
        """Average latency."""
        if not self.latencies_ms:
            return 0.0
        return statistics.mean(self.latencies_ms)
    
    @property
    def min_ms(self) -> float:
        """Minimum latency."""
        if not self.latencies_ms:
            return 0.0
        return min(self.latencies_ms)
    
    @property
    def max_ms(self) -> float:
        """Maximum latency."""
        if not self.latencies_ms:
            return 0.0
        return max(self.latencies_ms)
    
    @property
    def stddev_ms(self) -> float:
        """Standard deviation of latency."""
        if len(self.latencies_ms) < 2:
            return 0.0
        return statistics.stdev(self.latencies_ms)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "summary": {
                "total_requests": self.total_requests,
                "successful_requests": self.successful_requests,
                "failed_requests": self.failed_requests,
                "success_rate_percent": round(self.success_rate, 2),
                "total_time_seconds": round(self.total_time_seconds, 3),
                "requests_per_second": round(self.requests_per_second, 2),
            },
            "latency_ms": {
                "min": round(self.min_ms, 3),
                "max": round(self.max_ms, 3),
                "avg": round(self.avg_ms, 3),
                "stddev": round(self.stddev_ms, 3),
                "p50": round(self.p50_ms, 3),
                "p95": round(self.p95_ms, 3),
                "p99": round(self.p99_ms, 3),
            },
            "errors": self.errors[:10] if self.errors else [],  # Limit errors shown
        }
    
    def __str__(self) -> str:
        """Human-readable summary."""
        lines = [
            f"\n{'=' * 60}",
            f"Benchmark: {self.name}",
            f"{'=' * 60}",
            f"",
            f"Summary:",
            f"  Total Requests:    {self.total_requests:,}",
            f"  Successful:        {self.successful_requests:,} ({self.success_rate:.1f}%)",
            f"  Failed:            {self.failed_requests:,}",
            f"  Total Time:        {self.total_time_seconds:.2f}s",
            f"  Throughput:        {self.requests_per_second:.2f} req/s",
            f"",
            f"Latency (ms):",
            f"  Min:               {self.min_ms:.3f}",
            f"  Max:               {self.max_ms:.3f}",
            f"  Avg:               {self.avg_ms:.3f}",
            f"  Std Dev:           {self.stddev_ms:.3f}",
            f"  p50 (median):      {self.p50_ms:.3f}",
            f"  p95:               {self.p95_ms:.3f}",
            f"  p99:               {self.p99_ms:.3f}",
        ]
        
        if self.errors:
            lines.extend([
                f"",
                f"Errors (first 5):",
            ])
            for err in self.errors[:5]:
                lines.append(f"  - {err}")
        
        lines.append(f"{'=' * 60}\n")
        return "\n".join(lines)


class Timer:
    """Context manager for timing operations."""
    
    def __init__(self):
        self.start_time: float = 0
        self.end_time: float = 0
        self.elapsed_ms: float = 0
    
    def __enter__(self) -> "Timer":
        self.start_time = time.perf_counter()
        return self
    
    def __exit__(self, *args) -> None:
        self.end_time = time.perf_counter()
        self.elapsed_ms = (self.end_time - self.start_time) * 1000


def calculate_percentiles(values: list[float], percentile: float) -> float:
    """Calculate a specific percentile from a list of values."""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = (len(sorted_values) - 1) * percentile / 100
    lower = int(index)
    upper = lower + 1
    if upper >= len(sorted_values):
        return sorted_values[-1]
    weight = index - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


class BenchmarkRunner:
    """
    Runs benchmarks against API endpoints.
    
    Example usage:
        runner = BenchmarkRunner(base_url="http://localhost:8000")
        result = await runner.run_benchmark(
            name="GET /health",
            func=lambda: client.get("/health"),
            num_requests=1000,
            concurrency=10,
        )
        print(result)
    """
    
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        token: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._client: Any = None
    
    async def _get_client(self):
        """Get or create HTTP client."""
        if self._client is None:
            import httpx
            headers = {}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client
    
    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def run_benchmark(
        self,
        name: str,
        func: Callable[[], Coroutine[Any, Any, Any]],
        num_requests: int = 100,
        concurrency: int = 10,
        warmup_requests: int = 5,
    ) -> BenchmarkResult:
        """
        Run a benchmark with the specified parameters.
        
        Args:
            name: Name of the benchmark
            func: Async function to benchmark
            num_requests: Total number of requests to make
            concurrency: Number of concurrent requests
            warmup_requests: Number of warmup requests (not counted)
        
        Returns:
            BenchmarkResult with timing statistics
        """
        latencies: list[float] = []
        errors: list[str] = []
        successful = 0
        failed = 0
        
        # Warmup phase
        for _ in range(warmup_requests):
            try:
                await func()
            except Exception:
                pass
        
        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(concurrency)
        
        async def make_request():
            nonlocal successful, failed
            async with semaphore:
                timer = Timer()
                try:
                    with timer:
                        response = await func()
                    
                    # Check for HTTP errors if response has status_code
                    if hasattr(response, 'status_code'):
                        if 200 <= response.status_code < 400:
                            successful += 1
                            latencies.append(timer.elapsed_ms)
                        else:
                            failed += 1
                            errors.append(f"HTTP {response.status_code}")
                    else:
                        # Assume success if no status code (custom func)
                        successful += 1
                        latencies.append(timer.elapsed_ms)
                        
                except Exception as e:
                    failed += 1
                    errors.append(str(e)[:100])
        
        # Run benchmark
        start_time = time.perf_counter()
        tasks = [make_request() for _ in range(num_requests)]
        await asyncio.gather(*tasks)
        total_time = time.perf_counter() - start_time
        
        return BenchmarkResult(
            name=name,
            total_requests=num_requests,
            successful_requests=successful,
            failed_requests=failed,
            total_time_seconds=total_time,
            latencies_ms=latencies,
            errors=errors,
        )
    
    async def benchmark_endpoint(
        self,
        name: str,
        method: str,
        path: str,
        num_requests: int = 100,
        concurrency: int = 10,
        json_data: Optional[dict] = None,
        expected_status: int = 200,
    ) -> BenchmarkResult:
        """
        Convenience method to benchmark a specific endpoint.
        
        Args:
            name: Name for the benchmark
            method: HTTP method (GET, POST, etc.)
            path: API path
            num_requests: Number of requests
            concurrency: Concurrent requests
            json_data: JSON body for POST/PUT requests
            expected_status: Expected HTTP status code
        """
        client = await self._get_client()
        
        async def make_request():
            if method.upper() == "GET":
                return await client.get(path)
            elif method.upper() == "POST":
                return await client.post(path, json=json_data)
            elif method.upper() == "PUT":
                return await client.put(path, json=json_data)
            elif method.upper() == "DELETE":
                return await client.delete(path)
            else:
                raise ValueError(f"Unsupported method: {method}")
        
        return await self.run_benchmark(
            name=name,
            func=make_request,
            num_requests=num_requests,
            concurrency=concurrency,
        )


class BenchmarkSuite:
    """
    Collection of benchmarks to run together.
    
    Example:
        suite = BenchmarkSuite("API Benchmarks")
        suite.add_endpoint("Health Check", "GET", "/health")
        suite.add_endpoint("Hub Stats", "GET", "/api/v1/hub/stats")
        results = await suite.run(base_url="http://localhost:8000")
    """
    
    def __init__(self, name: str):
        self.name = name
        self.benchmarks: list[dict] = []
        self.results: list[BenchmarkResult] = []
    
    def add_endpoint(
        self,
        name: str,
        method: str,
        path: str,
        num_requests: int = 100,
        concurrency: int = 10,
        json_data: Optional[dict] = None,
    ) -> "BenchmarkSuite":
        """Add an endpoint benchmark to the suite."""
        self.benchmarks.append({
            "name": name,
            "method": method,
            "path": path,
            "num_requests": num_requests,
            "concurrency": concurrency,
            "json_data": json_data,
        })
        return self
    
    async def run(
        self,
        base_url: str = "http://localhost:8000",
        token: Optional[str] = None,
    ) -> list[BenchmarkResult]:
        """Run all benchmarks in the suite."""
        runner = BenchmarkRunner(base_url=base_url, token=token)
        self.results = []
        
        try:
            for bench in self.benchmarks:
                result = await runner.benchmark_endpoint(**bench)
                self.results.append(result)
                print(result)
        finally:
            await runner.close()
        
        return self.results
    
    def to_json(self) -> str:
        """Export results as JSON."""
        return json.dumps({
            "suite": self.name,
            "benchmarks": [r.to_dict() for r in self.results],
        }, indent=2)
    
    def summary(self) -> str:
        """Generate a summary report of all results."""
        if not self.results:
            return "No results yet. Run the suite first."
        
        lines = [
            f"\n{'#' * 70}",
            f"# Benchmark Suite: {self.name}",
            f"{'#' * 70}",
            "",
            f"{'Benchmark':<30} {'Req/s':>10} {'p50':>10} {'p95':>10} {'p99':>10} {'Success':>10}",
            "-" * 82,
        ]
        
        for r in self.results:
            lines.append(
                f"{r.name[:30]:<30} "
                f"{r.requests_per_second:>10.1f} "
                f"{r.p50_ms:>10.2f} "
                f"{r.p95_ms:>10.2f} "
                f"{r.p99_ms:>10.2f} "
                f"{r.success_rate:>9.1f}%"
            )
        
        lines.extend([
            "-" * 82,
            "",
        ])
        
        return "\n".join(lines)
