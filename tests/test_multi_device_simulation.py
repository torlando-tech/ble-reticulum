"""
Automated Multi-Device Simulation Tests

Tests the BLE multi-device simulation framework to ensure:
- Mock BLE components work correctly
- Two nodes can discover and connect
- Data transfer works bidirectionally
- Fragmentation works with large packets
- Multiple transfer scenarios work

These tests use the simulation framework (no real BLE hardware required).
"""

import sys
import os
import pytest
import asyncio
from unittest.mock import Mock, patch

# Add project paths
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(project_root, 'src'))
sys.path.insert(0, os.path.join(project_root, 'examples'))

from two_device_simulator import (
    MockBLEConnection,
    MockBLEDevice,
    SimulatedBLENode,
    TwoDeviceSimulator
)


# ============================================================================
# Mock BLE Component Tests
# ============================================================================

class TestMockBLEComponents:
    """Test individual mock BLE components."""

    def test_mock_device_creation(self):
        """Test MockBLEDevice can be created with correct attributes."""
        device = MockBLEDevice(
            address="AA:BB:CC:DD:EE:01",
            name="Test-Device",
            rssi=-65
        )

        assert device.address == "AA:BB:CC:DD:EE:01"
        assert device.name == "Test-Device"
        assert device.rssi == -65
        assert "00000001-5824-4f48-9e1a-3b3e8f0c1234" in device.metadata["uuids"]
        assert device.metadata["rssi"] == -65

    @pytest.mark.asyncio
    async def test_mock_connection_lifecycle(self):
        """Test MockBLEConnection connect/disconnect."""
        conn = MockBLEConnection("Node-A", "Node-B", mtu=185)

        # Initially not connected
        assert not conn.connected

        # Connect
        await conn.connect()
        assert conn.connected

        # Disconnect
        await conn.disconnect()
        assert not conn.connected

    @pytest.mark.asyncio
    async def test_mock_connection_data_transfer(self):
        """Test data transfer between two MockBLEConnections."""
        conn_a = MockBLEConnection("Node-A", "Node-B", mtu=185)
        conn_b = MockBLEConnection("Node-B", "Node-A", mtu=185)

        # Link them together
        conn_a.set_peer(conn_b)
        conn_b.set_peer(conn_a)

        # Connect both
        await conn_a.connect()
        await conn_b.connect()

        # Setup receiver
        received = []
        async def rx_callback(data):
            received.append(data)

        conn_b.set_rx_callback(rx_callback)

        # Send data A → B
        test_data = b"Hello from A!"
        await conn_a.write(test_data)
        await asyncio.sleep(0.01)  # Allow delivery

        assert len(received) == 1
        assert received[0] == test_data

    @pytest.mark.asyncio
    async def test_mock_connection_rejects_oversized_data(self):
        """Test that data exceeding MTU is rejected."""
        conn = MockBLEConnection("Node-A", "Node-B", mtu=185)
        await conn.connect()

        oversized_data = b"X" * 200  # Exceeds MTU of 185

        with pytest.raises(ValueError, match="exceeds MTU"):
            await conn.write(oversized_data)

    @pytest.mark.asyncio
    async def test_mock_connection_rejects_write_when_disconnected(self):
        """Test that writing to disconnected connection fails."""
        conn = MockBLEConnection("Node-A", "Node-B", mtu=185)

        # Not connected
        with pytest.raises(RuntimeError, match="not connected"):
            await conn.write(b"Test")

    @pytest.mark.asyncio
    async def test_bidirectional_data_transfer(self):
        """Test data can flow in both directions."""
        conn_a = MockBLEConnection("Node-A", "Node-B", mtu=185)
        conn_b = MockBLEConnection("Node-B", "Node-A", mtu=185)

        conn_a.set_peer(conn_b)
        conn_b.set_peer(conn_a)

        await conn_a.connect()
        await conn_b.connect()

        # Setup receivers
        received_a = []
        received_b = []

        async def rx_callback_a(data):
            received_a.append(data)

        async def rx_callback_b(data):
            received_b.append(data)

        conn_a.set_rx_callback(rx_callback_a)
        conn_b.set_rx_callback(rx_callback_b)

        # A → B
        await conn_a.write(b"A to B")
        await asyncio.sleep(0.01)

        # B → A
        await conn_b.write(b"B to A")
        await asyncio.sleep(0.01)

        assert len(received_b) == 1
        assert received_b[0] == b"A to B"
        assert len(received_a) == 1
        assert received_a[0] == b"B to A"


# ============================================================================
# Simulated Node Tests
# ============================================================================

class TestSimulatedBLENode:
    """Test SimulatedBLENode functionality."""

    @pytest.mark.asyncio
    async def test_node_discovery(self):
        """Test that a node can discover its peer."""
        node = SimulatedBLENode(
            name="Node-A",
            address="AA:BB:CC:DD:EE:01",
            peer_address="AA:BB:CC:DD:EE:02",
            peer_name="Node-B"
        )

        devices = await node.discover_peers()

        assert len(devices) == 1
        assert devices[0].address == "AA:BB:CC:DD:EE:02"
        assert devices[0].name == "Node-B"

    def test_node_connection_creation(self):
        """Test that a node can create a connection."""
        node = SimulatedBLENode(
            name="Node-A",
            address="AA:BB:CC:DD:EE:01",
            peer_address="AA:BB:CC:DD:EE:02",
            peer_name="Node-B"
        )

        conn = node.create_connection(mtu=247)

        assert conn is not None
        assert conn.name == "Node-A"
        assert conn.peer_name == "Node-B"
        assert conn.mtu == 247

    def test_node_connection_singleton(self):
        """Test that creating connection twice returns same instance."""
        node = SimulatedBLENode(
            name="Node-A",
            address="AA:BB:CC:DD:EE:01",
            peer_address="AA:BB:CC:DD:EE:02",
            peer_name="Node-B"
        )

        conn1 = node.create_connection()
        conn2 = node.create_connection()

        assert conn1 is conn2


# ============================================================================
# Two-Device Simulator Tests
# ============================================================================

class TestTwoDeviceSimulator:
    """Test the complete two-device simulator."""

    def test_simulator_initialization(self):
        """Test that simulator creates two nodes correctly."""
        sim = TwoDeviceSimulator()

        assert sim.node_a is not None
        assert sim.node_b is not None
        assert sim.node_a.address == "AA:BB:CC:DD:EE:01"
        assert sim.node_b.address == "AA:BB:CC:DD:EE:02"
        assert sim.node_a.peer_address == sim.node_b.address
        assert sim.node_b.peer_address == sim.node_a.address

    @pytest.mark.asyncio
    async def test_simulator_discovery(self):
        """Test discovery test scenario."""
        sim = TwoDeviceSimulator()
        success = await sim.run_discovery_test()
        # run_discovery_test uses assertions internally, if it returns it passed
        assert success is None  # Function doesn't return, just completes

    @pytest.mark.asyncio
    async def test_simulator_connection(self):
        """Test connection establishment."""
        sim = TwoDeviceSimulator()
        await sim.setup_connections()

        assert sim.node_a.connection.connected
        assert sim.node_b.connection.connected

    @pytest.mark.asyncio
    async def test_simulator_data_transfer(self):
        """Test data transfer between nodes."""
        sim = TwoDeviceSimulator()
        await sim.setup_connections()

        # Setup receiver
        received = []
        async def rx_callback(data):
            received.append(data)

        sim.node_b.connection.set_rx_callback(rx_callback)

        # Send data
        test_data = b"Test packet"
        await sim.node_a.connection.write(test_data)
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0] == test_data

    @pytest.mark.asyncio
    async def test_simulator_fragmentation(self):
        """Test fragmentation of large packets."""
        sim = TwoDeviceSimulator()
        await sim.setup_connections()

        # Large packet that requires fragmentation
        large_data = b"X" * 500
        mtu = sim.node_a.connection.mtu
        expected_fragments = (len(large_data) + mtu - 1) // mtu

        received_fragments = []
        async def rx_callback(data):
            received_fragments.append(data)

        sim.node_b.connection.set_rx_callback(rx_callback)

        # Send in fragments
        for i in range(expected_fragments):
            start = i * mtu
            end = min(start + mtu, len(large_data))
            fragment = large_data[start:end]
            await sim.node_a.connection.write(fragment)
            await asyncio.sleep(0.01)

        # Verify all fragments received
        assert len(received_fragments) == expected_fragments

        # Verify reconstruction works
        reconstructed = b''.join(received_fragments)
        assert reconstructed == large_data

    @pytest.mark.asyncio
    async def test_simulator_all_tests(self):
        """Test that all simulator tests pass."""
        sim = TwoDeviceSimulator()
        success = await sim.run_all_tests()
        assert success is True


# ============================================================================
# Integration Scenarios
# ============================================================================

class TestIntegrationScenarios:
    """Test various integration scenarios."""

    @pytest.mark.asyncio
    async def test_rapid_transfers(self):
        """Test rapid back-and-forth transfers."""
        sim = TwoDeviceSimulator()
        await sim.setup_connections()

        received_a = []
        received_b = []

        async def rx_callback_a(data):
            received_a.append(data)

        async def rx_callback_b(data):
            received_b.append(data)

        sim.node_a.connection.set_rx_callback(rx_callback_a)
        sim.node_b.connection.set_rx_callback(rx_callback_b)

        # Send 10 packets each direction
        for i in range(10):
            await sim.node_a.connection.write(f"A→B {i}".encode())
            await sim.node_b.connection.write(f"B→A {i}".encode())
            await asyncio.sleep(0.001)

        await asyncio.sleep(0.1)  # Allow all deliveries

        assert len(received_b) == 10
        assert len(received_a) == 10

    @pytest.mark.asyncio
    async def test_various_packet_sizes(self):
        """Test various packet sizes."""
        sim = TwoDeviceSimulator()
        await sim.setup_connections()

        test_sizes = [1, 10, 50, 100, 185]  # Up to MTU
        received = []

        async def rx_callback(data):
            received.append(len(data))

        sim.node_b.connection.set_rx_callback(rx_callback)

        for size in test_sizes:
            data = b"X" * size
            await sim.node_a.connection.write(data)
            await asyncio.sleep(0.01)

        assert received == test_sizes

    @pytest.mark.asyncio
    async def test_connection_disconnect_reconnect(self):
        """Test disconnection and reconnection."""
        sim = TwoDeviceSimulator()
        await sim.setup_connections()

        # Verify connected
        assert sim.node_a.connection.connected

        # Disconnect
        await sim.node_a.connection.disconnect()
        assert not sim.node_a.connection.connected

        # Reconnect
        await sim.node_a.connection.connect()
        assert sim.node_a.connection.connected

        # Data transfer should work again
        received = []
        async def rx_callback(data):
            received.append(data)

        sim.node_b.connection.set_rx_callback(rx_callback)
        await sim.node_a.connection.write(b"After reconnect")
        await asyncio.sleep(0.01)

        assert len(received) == 1
        assert received[0] == b"After reconnect"

    @pytest.mark.asyncio
    async def test_empty_data_transfer(self):
        """Test that empty data can be sent (edge case)."""
        sim = TwoDeviceSimulator()
        await sim.setup_connections()

        received = []
        async def rx_callback(data):
            received.append(data)

        sim.node_b.connection.set_rx_callback(rx_callback)

        # Send empty data
        await sim.node_a.connection.write(b"")
        await asyncio.sleep(0.01)

        assert len(received) == 1
        assert received[0] == b""


# ============================================================================
# Performance Tests
# ============================================================================

class TestPerformance:
    """Test performance characteristics of simulation."""

    @pytest.mark.asyncio
    async def test_throughput_simulation(self):
        """Test sustained throughput in simulation."""
        sim = TwoDeviceSimulator()
        await sim.setup_connections()

        packet_count = 100
        packet_size = 100
        received_count = 0

        async def rx_callback(data):
            nonlocal received_count
            received_count += 1

        sim.node_b.connection.set_rx_callback(rx_callback)

        # Send many packets
        start = asyncio.get_event_loop().time()
        for i in range(packet_count):
            data = b"X" * packet_size
            await sim.node_a.connection.write(data)

        await asyncio.sleep(0.5)  # Allow delivery
        end = asyncio.get_event_loop().time()

        duration = end - start
        assert received_count == packet_count
        assert duration < 2.0  # Should be fast in simulation

    @pytest.mark.asyncio
    async def test_large_packet_fragmentation_performance(self):
        """Test performance with large packets requiring fragmentation."""
        sim = TwoDeviceSimulator()
        await sim.setup_connections()

        # Very large packet (2KB)
        large_data = b"X" * 2000
        mtu = sim.node_a.connection.mtu
        fragments_needed = (len(large_data) + mtu - 1) // mtu

        received_fragments = []
        async def rx_callback(data):
            received_fragments.append(data)

        sim.node_b.connection.set_rx_callback(rx_callback)

        # Send fragments
        start = asyncio.get_event_loop().time()
        for i in range(fragments_needed):
            start_pos = i * mtu
            end_pos = min(start_pos + mtu, len(large_data))
            fragment = large_data[start_pos:end_pos]
            await sim.node_a.connection.write(fragment)

        await asyncio.sleep(0.5)  # Allow delivery
        end = asyncio.get_event_loop().time()

        duration = end - start
        assert len(received_fragments) == fragments_needed
        assert duration < 2.0  # Should be fast

        # Verify reconstruction
        reconstructed = b''.join(received_fragments)
        assert reconstructed == large_data


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
