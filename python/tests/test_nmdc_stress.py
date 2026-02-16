"""
Tests for NMDC stress test module.
"""
import pytest
from unittest.mock import MagicMock, patch

from verlihub.benchmarks.nmdc_stress import (
    NMDCStressResult,
    NMDCStressTest,
)


class TestNMDCStressResult:
    """Tests for NMDCStressResult class."""
    
    def test_success_rate_calculation(self):
        """Test success rate calculation."""
        result = NMDCStressResult(
            test_name="Test",
            total_operations=100,
            successful_operations=95,
            failed_operations=5,
            total_time_seconds=10.0,
        )
        assert result.success_rate == 95.0
    
    def test_success_rate_zero_ops(self):
        """Test success rate with zero operations."""
        result = NMDCStressResult(
            test_name="Test",
            total_operations=0,
            successful_operations=0,
            failed_operations=0,
            total_time_seconds=0.0,
        )
        assert result.success_rate == 0.0
    
    def test_operations_per_second(self):
        """Test throughput calculation."""
        result = NMDCStressResult(
            test_name="Test",
            total_operations=100,
            successful_operations=100,
            failed_operations=0,
            total_time_seconds=10.0,
        )
        assert result.operations_per_second == 10.0
    
    def test_percentiles(self):
        """Test percentile calculations."""
        result = NMDCStressResult(
            test_name="Test",
            total_operations=5,
            successful_operations=5,
            failed_operations=0,
            total_time_seconds=1.0,
            latencies_ms=[1.0, 2.0, 3.0, 4.0, 5.0],
        )
        assert result.p50_ms == 3.0
        assert result.p95_ms > 4.0
    
    def test_str_representation(self):
        """Test string output."""
        result = NMDCStressResult(
            test_name="Test",
            total_operations=100,
            successful_operations=100,
            failed_operations=0,
            total_time_seconds=10.0,
            connections_made=10,
            messages_sent=100,
        )
        
        output = str(result)
        assert "Test" in output
        assert "100" in output
        assert "Connections Made" in output


class TestNMDCStressTest:
    """Tests for NMDCStressTest class."""
    
    def test_initialization(self):
        """Test stress test initialization."""
        stress = NMDCStressTest(
            host="testhost",
            port=4111,
            username="testuser",
            password="testpass",
        )
        
        assert stress.host == "testhost"
        assert stress.port == 4111
        assert stress.username == "testuser"
        assert stress.password == "testpass"
    
    def test_create_client(self):
        """Test client creation."""
        stress = NMDCStressTest(username="test")
        
        with patch('verlihub.client.NMDCClient') as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            
            client = stress._create_client("123")
            
            # Check that client was created
            assert mock_client.called
    
    @pytest.mark.skip(reason="Requires running hub")
    def test_connection_cycles_integration(self):
        """Integration test for connection cycles."""
        stress = NMDCStressTest(
            host="localhost",
            port=4111,
            username="pytest_stress",
        )
        
        result = stress.test_connection_cycles(num_cycles=3, delay_between_ms=100)
        assert result.total_operations == 3
    
    @pytest.mark.skip(reason="Requires running hub")
    def test_message_throughput_integration(self):
        """Integration test for message throughput."""
        stress = NMDCStressTest(
            host="localhost",
            port=4111,
            username="pytest_throughput",
        )
        
        result = stress.test_message_throughput(num_messages=10, message_size=50)
        assert result.total_operations == 10
    
    @pytest.mark.skip(reason="Requires running hub")
    def test_protocol_robustness_integration(self):
        """Integration test for protocol robustness."""
        stress = NMDCStressTest(
            host="localhost",
            port=4111,
            username="pytest_robust",
        )
        
        result = stress.test_protocol_robustness(num_tests=10)
        # Should not crash, even if some messages fail
        assert result.total_operations == 10


class TestNMDCStressMocked:
    """Tests with mocked NMDC client."""
    
    def test_connection_cycles_mocked(self):
        """Test connection cycles with mocked client."""
        with patch('verlihub.client.NMDCClient') as mock_client:
            mock_instance = MagicMock()
            mock_instance.connect.return_value = True
            mock_client.return_value = mock_instance
            
            stress = NMDCStressTest()
            result = stress.test_connection_cycles(num_cycles=5, delay_between_ms=0)
            
            assert result.total_operations == 5
            assert result.connections_made == 5
            assert result.success_rate == 100.0
    
    def test_connection_cycles_with_failures(self):
        """Test connection cycles with some failures."""
        with patch('verlihub.client.NMDCClient') as mock_client:
            mock_instance = MagicMock()
            # Fail every other connection
            mock_instance.connect.side_effect = [True, False, True, False, True]
            mock_client.return_value = mock_instance
            
            stress = NMDCStressTest()
            result = stress.test_connection_cycles(num_cycles=5, delay_between_ms=0)
            
            assert result.total_operations == 5
            assert result.successful_operations == 3
            assert result.failed_operations == 2
    
    def test_message_throughput_mocked(self):
        """Test message throughput with mocked client."""
        with patch('verlihub.client.NMDCClient') as mock_client:
            mock_instance = MagicMock()
            mock_instance.connect.return_value = True
            mock_client.return_value = mock_instance
            
            stress = NMDCStressTest()
            result = stress.test_message_throughput(num_messages=10, message_size=50)
            
            assert result.total_operations == 10
            assert result.successful_operations == 10
            assert result.messages_sent == 10
    
    def test_concurrent_connections_mocked(self):
        """Test concurrent connections with mocked client."""
        with patch('verlihub.client.NMDCClient') as mock_client:
            mock_instance = MagicMock()
            mock_instance.connect.return_value = True
            mock_client.return_value = mock_instance
            
            stress = NMDCStressTest()
            result = stress.test_concurrent_connections(
                num_connections=3,
                messages_per_connection=5,
            )
            
            assert result.total_operations == 15  # 3 * 5
            assert result.connections_made == 3
    
    def test_rapid_disconnect(self):
        """Test rapid disconnect with real sockets but mock server."""
        # This test uses actual socket connect/disconnect
        # but to a localhost port that likely doesn't exist
        stress = NMDCStressTest(host="127.0.0.1", port=59999)
        
        result = stress.test_rapid_disconnect(num_cycles=3)
        
        # Should complete (with failures since no server)
        assert result.total_operations == 3
        # All should fail since nothing is listening
        assert result.failed_operations == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
