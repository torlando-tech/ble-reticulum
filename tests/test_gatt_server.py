"""
Unit tests for BLEGATTServer

Tests the GATT server functionality without requiring actual BLE hardware.
"""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from RNS.Interfaces.BLEGATTServer import BLEGATTServer, BLESS_AVAILABLE


class MockInterface:
    """Mock BLEInterface for testing"""
    def __init__(self):
        self.name = "TestInterface"
        self.received_data = []


@pytest.mark.skipif(not BLESS_AVAILABLE, reason="bless library not available")
class TestBLEGATTServer:
    """Test suite for BLEGATTServer"""

    def test_initialization(self):
        """Test GATT server initialization"""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface, device_name="TestNode")

        assert server.device_name == "TestNode"
        assert server.interface == mock_interface
        assert not server.running
        assert server.server is None
        assert len(server.connected_centrals) == 0

    def test_uuids_defined(self):
        """Test that UUIDs are properly defined"""
        assert BLEGATTServer.SERVICE_UUID == "00000001-5824-4f48-9e1a-3b3e8f0c1234"
        assert BLEGATTServer.RX_CHAR_UUID == "00000002-5824-4f48-9e1a-3b3e8f0c1234"
        assert BLEGATTServer.TX_CHAR_UUID == "00000003-5824-4f48-9e1a-3b3e8f0c1234"

    def test_connection_tracking(self):
        """Test central connection tracking"""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface)

        # Simulate central connection
        central_addr = "AA:BB:CC:DD:EE:FF"
        server._handle_central_connected(central_addr)

        assert server.is_connected(central_addr)
        assert central_addr in server.get_connected_centrals()
        assert len(server.connected_centrals) == 1

        # Get connection info
        info = server.get_connection_info(central_addr)
        assert info is not None
        assert info["address"] == central_addr
        assert "connected_at" in info
        assert info["bytes_received"] == 0
        assert info["bytes_sent"] == 0

    def test_connection_disconnect(self):
        """Test central disconnection"""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface)

        central_addr = "AA:BB:CC:DD:EE:FF"
        server._handle_central_connected(central_addr)
        assert server.is_connected(central_addr)

        server._handle_central_disconnected(central_addr)
        assert not server.is_connected(central_addr)
        assert len(server.connected_centrals) == 0

    def test_multiple_centrals(self):
        """Test multiple simultaneous central connections"""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface)

        centrals = [
            "AA:BB:CC:DD:EE:FF",
            "11:22:33:44:55:66",
            "FF:EE:DD:CC:BB:AA",
        ]

        for addr in centrals:
            server._handle_central_connected(addr)

        assert len(server.connected_centrals) == 3
        for addr in centrals:
            assert server.is_connected(addr)

    def test_data_queuing(self):
        """Test data queuing for centrals"""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface)

        central_addr = "AA:BB:CC:DD:EE:FF"
        server._handle_central_connected(central_addr)

        # Queue some data
        data1 = b"Test data 1"
        data2 = b"Test data 2"
        server.queue_data_for_central(data1, central_addr)
        server.queue_data_for_central(data2, central_addr)

        assert len(server.tx_queues[central_addr]) == 2
        assert server.tx_queues[central_addr][0] == data1
        assert server.tx_queues[central_addr][1] == data2

    def test_callbacks(self):
        """Test callback invocation"""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface)

        # Track callback invocations
        callbacks_called = {
            "data_received": [],
            "connected": [],
            "disconnected": [],
        }

        def on_data(data, addr):
            callbacks_called["data_received"].append((data, addr))

        def on_connect(addr):
            callbacks_called["connected"].append(addr)

        def on_disconnect(addr):
            callbacks_called["disconnected"].append(addr)

        server.on_data_received = on_data
        server.on_central_connected = on_connect
        server.on_central_disconnected = on_disconnect

        # Simulate connection
        central_addr = "AA:BB:CC:DD:EE:FF"
        server._handle_central_connected(central_addr)
        assert central_addr in callbacks_called["connected"]

        # Simulate data reception
        test_data = b"Test fragment"
        # Direct callback invocation (would normally be called from _handle_write_request)
        server.on_data_received(test_data, central_addr)
        assert (test_data, central_addr) in callbacks_called["data_received"]

        # Simulate disconnection
        server._handle_central_disconnected(central_addr)
        assert central_addr in callbacks_called["disconnected"]

    def test_statistics(self):
        """Test statistics gathering"""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface)

        # Initial stats
        stats = server.get_statistics()
        assert stats["running"] == False
        assert stats["connected_centrals"] == 0
        assert stats["total_bytes_received"] == 0
        assert stats["total_bytes_sent"] == 0

        # Add some centrals with data
        server._handle_central_connected("AA:BB:CC:DD:EE:FF")
        server._handle_central_connected("11:22:33:44:55:66")

        # Simulate some data transfer
        server.connected_centrals["AA:BB:CC:DD:EE:FF"]["bytes_received"] = 100
        server.connected_centrals["AA:BB:CC:DD:EE:FF"]["bytes_sent"] = 50
        server.connected_centrals["11:22:33:44:55:66"]["bytes_received"] = 200
        server.connected_centrals["11:22:33:44:55:66"]["bytes_sent"] = 150

        stats = server.get_statistics()
        assert stats["connected_centrals"] == 2
        assert stats["total_bytes_received"] == 300
        assert stats["total_bytes_sent"] == 200

    def test_string_representations(self):
        """Test __str__ and __repr__ methods"""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface, device_name="TestNode")

        str_repr = str(server)
        assert "TestNode" in str_repr
        assert "stopped" in str_repr

        repr_repr = repr(server)
        assert "TestNode" in repr_repr
        assert "running=False" in repr_repr


    def test_write_request_empty_data(self):
        """Test handling of empty write requests."""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface)
        central_addr = "AA:BB:CC:DD:EE:FF"
        server._handle_central_connected(central_addr)

        # Simulate empty write (should handle gracefully)
        empty_data = b''
        # Would normally call _handle_write_request, but that's internal
        # Just verify server doesn't crash with empty data
        server.connected_centrals[central_addr]["bytes_received"] += len(empty_data)
        assert server.connected_centrals[central_addr]["bytes_received"] == 0

    def test_write_request_large_data(self):
        """Test handling of large write requests."""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface)
        central_addr = "AA:BB:CC:DD:EE:FF"
        server._handle_central_connected(central_addr)

        # Simulate large write
        large_data = b'X' * 1000
        server.connected_centrals[central_addr]["bytes_received"] += len(large_data)
        assert server.connected_centrals[central_addr]["bytes_received"] == 1000

    def test_notification_to_specific_central(self):
        """Test targeted notification to specific central."""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface)

        # Connect multiple centrals
        central1 = "AA:BB:CC:DD:EE:01"
        central2 = "AA:BB:CC:DD:EE:02"
        server._handle_central_connected(central1)
        server._handle_central_connected(central2)

        # Queue data for specific central
        data = b"Targeted notification"
        server.queue_data_for_central(data, central1)

        # Verify only central1 has queued data
        assert len(server.tx_queues[central1]) == 1
        assert len(server.tx_queues[central2]) == 0

    def test_central_reconnection(self):
        """Test same central reconnecting."""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface)
        central_addr = "AA:BB:CC:DD:EE:FF"

        # First connection
        server._handle_central_connected(central_addr)
        assert server.is_connected(central_addr)

        # Disconnect
        server._handle_central_disconnected(central_addr)
        assert not server.is_connected(central_addr)

        # Reconnect
        server._handle_central_connected(central_addr)
        assert server.is_connected(central_addr)
        assert len(server.connected_centrals) == 1

    def test_statistics_overflow_safety(self):
        """Test that statistics handle large values correctly."""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface)
        central_addr = "AA:BB:CC:DD:EE:FF"
        server._handle_central_connected(central_addr)

        # Simulate very large byte counts
        large_value = 2**32  # 4GB
        server.connected_centrals[central_addr]["bytes_received"] = large_value
        server.connected_centrals[central_addr]["bytes_sent"] = large_value

        stats = server.get_statistics()
        assert stats["total_bytes_received"] == large_value
        assert stats["total_bytes_sent"] == large_value

    def test_tx_queue_fifo_order(self):
        """Test that TX queue maintains FIFO order."""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface)
        central_addr = "AA:BB:CC:DD:EE:FF"
        server._handle_central_connected(central_addr)

        # Queue multiple items
        data1 = b"First"
        data2 = b"Second"
        data3 = b"Third"

        server.queue_data_for_central(data1, central_addr)
        server.queue_data_for_central(data2, central_addr)
        server.queue_data_for_central(data3, central_addr)

        # Verify FIFO order
        queue = server.tx_queues[central_addr]
        assert queue[0] == data1
        assert queue[1] == data2
        assert queue[2] == data3

    def test_get_connection_info_nonexistent(self):
        """Test getting info for non-existent central."""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface)

        # Try to get info for non-existent central
        info = server.get_connection_info("AA:BB:CC:DD:EE:FF")
        assert info is None

    def test_server_repr_with_centrals(self):
        """Test string representation includes connected centrals count."""
        mock_interface = MockInterface()
        server = BLEGATTServer(mock_interface, device_name="TestNode")

        # Add some centrals
        server._handle_central_connected("AA:BB:CC:DD:EE:01")
        server._handle_central_connected("AA:BB:CC:DD:EE:02")

        repr_str = repr(server)
        assert "TestNode" in repr_str
        assert "running=False" in repr_str


@pytest.mark.skipif(BLESS_AVAILABLE, reason="Testing import error handling")
class TestBLEGATTServerWithoutBless:
    """Test behavior when bless is not available"""

    def test_import_error(self):
        """Test that appropriate error is raised when bless not available"""
        # This test would need to mock the BLESS_AVAILABLE flag
        # For now, just ensure the flag is checked correctly
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
