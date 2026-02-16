"""
Tests for the benchmark module.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from verlihub.benchmarks.core import (
    BenchmarkResult,
    BenchmarkRunner,
    BenchmarkSuite,
    Timer,
    calculate_percentiles,
)


class TestTimer:
    """Tests for Timer context manager."""
    
    def test_timer_measures_time(self):
        """Test that timer measures elapsed time."""
        import time
        
        with Timer() as timer:
            time.sleep(0.01)  # Sleep 10ms
        
        # Should be at least 10ms
        assert timer.elapsed_ms >= 9.0
        # Should be less than 100ms (allowing for overhead)
        assert timer.elapsed_ms < 100.0
    
    def test_timer_attributes(self):
        """Test timer has expected attributes."""
        with Timer() as timer:
            pass
        
        assert timer.start_time > 0
        assert timer.end_time > timer.start_time
        assert timer.elapsed_ms > 0


class TestCalculatePercentiles:
    """Tests for percentile calculation."""
    
    def test_empty_list(self):
        """Test with empty list."""
        assert calculate_percentiles([], 50) == 0.0
    
    def test_single_value(self):
        """Test with single value."""
        assert calculate_percentiles([10.0], 50) == 10.0
        assert calculate_percentiles([10.0], 99) == 10.0
    
    def test_p50_median(self):
        """Test 50th percentile (median)."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        p50 = calculate_percentiles(values, 50)
        assert p50 == 3.0
    
    def test_p95(self):
        """Test 95th percentile."""
        values = list(range(1, 101))  # 1-100
        p95 = calculate_percentiles(values, 95)
        assert 94 < p95 < 96
    
    def test_unsorted_input(self):
        """Test that function handles unsorted input."""
        values = [5.0, 1.0, 4.0, 2.0, 3.0]
        p50 = calculate_percentiles(values, 50)
        assert p50 == 3.0


class TestBenchmarkResult:
    """Tests for BenchmarkResult class."""
    
    def test_success_rate_calculation(self):
        """Test success rate calculation."""
        result = BenchmarkResult(
            name="Test",
            total_requests=100,
            successful_requests=95,
            failed_requests=5,
            total_time_seconds=10.0,
        )
        assert result.success_rate == 95.0
    
    def test_success_rate_zero_requests(self):
        """Test success rate with zero requests."""
        result = BenchmarkResult(
            name="Test",
            total_requests=0,
            successful_requests=0,
            failed_requests=0,
            total_time_seconds=0.0,
        )
        assert result.success_rate == 0.0
    
    def test_requests_per_second(self):
        """Test throughput calculation."""
        result = BenchmarkResult(
            name="Test",
            total_requests=100,
            successful_requests=100,
            failed_requests=0,
            total_time_seconds=10.0,
        )
        assert result.requests_per_second == 10.0
    
    def test_latency_stats(self):
        """Test latency statistics."""
        result = BenchmarkResult(
            name="Test",
            total_requests=5,
            successful_requests=5,
            failed_requests=0,
            total_time_seconds=1.0,
            latencies_ms=[1.0, 2.0, 3.0, 4.0, 5.0],
        )
        
        assert result.min_ms == 1.0
        assert result.max_ms == 5.0
        assert result.avg_ms == 3.0
        assert result.p50_ms == 3.0
    
    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = BenchmarkResult(
            name="Test Benchmark",
            total_requests=100,
            successful_requests=98,
            failed_requests=2,
            total_time_seconds=5.0,
            latencies_ms=[10.0] * 98,
            errors=["Error 1", "Error 2"],
        )
        
        d = result.to_dict()
        
        assert d["name"] == "Test Benchmark"
        assert d["summary"]["total_requests"] == 100
        assert d["summary"]["successful_requests"] == 98
        assert d["latency_ms"]["avg"] == 10.0
        assert len(d["errors"]) == 2
    
    def test_str_representation(self):
        """Test string representation."""
        result = BenchmarkResult(
            name="Test",
            total_requests=100,
            successful_requests=100,
            failed_requests=0,
            total_time_seconds=10.0,
            latencies_ms=[1.0] * 100,
        )
        
        s = str(result)
        assert "Test" in s
        assert "100" in s
        assert "Throughput" in s


class TestBenchmarkSuite:
    """Tests for BenchmarkSuite class."""
    
    def test_add_endpoint(self):
        """Test adding endpoints to suite."""
        suite = BenchmarkSuite("Test Suite")
        suite.add_endpoint("Health", "GET", "/health")
        suite.add_endpoint("Stats", "GET", "/stats")
        
        assert len(suite.benchmarks) == 2
    
    def test_add_endpoint_chainable(self):
        """Test that add_endpoint is chainable."""
        suite = BenchmarkSuite("Test Suite")
        result = suite.add_endpoint("Health", "GET", "/health")
        
        assert result is suite
    
    def test_to_json_empty(self):
        """Test JSON export with no results."""
        suite = BenchmarkSuite("Test Suite")
        json_str = suite.to_json()
        
        import json
        data = json.loads(json_str)
        assert data["suite"] == "Test Suite"
        assert data["benchmarks"] == []
    
    def test_summary_no_results(self):
        """Test summary with no results."""
        suite = BenchmarkSuite("Test Suite")
        summary = suite.summary()
        assert "No results" in summary


class TestBenchmarkRunner:
    """Tests for BenchmarkRunner class."""
    
    @pytest.mark.asyncio
    async def test_runner_initialization(self):
        """Test runner initialization."""
        runner = BenchmarkRunner(
            base_url="http://localhost:8000",
            token="test_token",
            timeout=60.0,
        )
        
        assert runner.base_url == "http://localhost:8000"
        assert runner.token == "test_token"
        assert runner.timeout == 60.0
        
        await runner.close()
    
    @pytest.mark.asyncio
    async def test_runner_strips_trailing_slash(self):
        """Test that runner strips trailing slash from URL."""
        runner = BenchmarkRunner(base_url="http://localhost:8000/")
        assert runner.base_url == "http://localhost:8000"
        await runner.close()
    
    @pytest.mark.asyncio
    async def test_run_benchmark_with_mock(self):
        """Test running benchmark with mock function."""
        runner = BenchmarkRunner()
        
        call_count = 0
        
        async def mock_func():
            nonlocal call_count
            call_count += 1
            mock_response = MagicMock()
            mock_response.status_code = 200
            return mock_response
        
        result = await runner.run_benchmark(
            name="Mock Test",
            func=mock_func,
            num_requests=10,
            concurrency=2,
            warmup_requests=0,
        )
        
        assert result.name == "Mock Test"
        assert result.total_requests == 10
        assert call_count == 10
        assert result.successful_requests == 10
        
        await runner.close()
    
    @pytest.mark.asyncio
    async def test_run_benchmark_handles_errors(self):
        """Test that runner handles errors gracefully."""
        runner = BenchmarkRunner()
        
        async def failing_func():
            raise Exception("Test error")
        
        result = await runner.run_benchmark(
            name="Failing Test",
            func=failing_func,
            num_requests=5,
            concurrency=1,
            warmup_requests=0,
        )
        
        assert result.failed_requests == 5
        assert len(result.errors) == 5
        assert "Test error" in result.errors[0]
        
        await runner.close()


class TestBenchmarkIntegration:
    """Integration tests for benchmarks (require running API)."""
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running API server")
    async def test_quick_health_check(self):
        """Test quick health check benchmark against real API."""
        from verlihub.benchmarks.api_benchmarks import quick_health_check
        
        result = await quick_health_check(
            base_url="http://localhost:8000",
            iterations=10,
        )
        
        assert result.total_requests == 10
        assert result.success_rate > 90


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
