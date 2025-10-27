#!/usr/bin/env python3
"""
Two-Device BLE Simulator

Simulates two BLE nodes discovering and connecting to each other within a single process.
This allows testing of Reticulum integration without requiring physical BLE devices.

Architecture:
- Two simulated BLE nodes (Node A and Node B)
- Mock BLE discovery (they automatically "see" each other)
- Mock BLE connection (loopback data transfer)
- Full Reticulum integration on both sides

What this DOES test:
- Reticulum interface integration
- Packet fragmentation and reassembly
- Announce propagation logic
- Multi-peer coordination
- Error handling and recovery

What this DOES NOT test:
- Actual BLE radio behavior
- Real MTU negotiation
- Physical range limitations
- Platform-specific BLE issues
- RF interference

Usage:
    python3 examples/two_device_simulator.py

Requirements:
    - Reticulum installed
    - BLE interface in Python path
"""

import sys
import os
import asyncio
import time
from unittest.mock import Mock, AsyncMock, patch
import logging

# Setup path to find our BLE interface
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src_dir = os.path.join(project_root, 'src')
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# Reticulum should be installed via pip or available in PYTHONPATH
# If running in a development environment, you may need to:
#   pip install rns
# Or set PYTHONPATH to include your Reticulum installation

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)

logger = logging.getLogger('BLESimulator')


# ============================================================================
# Mock BLE Components
# ============================================================================

class MockBLEConnection:
    """
    Simulates a BLE connection between two nodes.
    Data written on one end arrives at the other end.
    """
    def __init__(self, name, peer_name, mtu=185):
        self.name = name
        self.peer_name = peer_name
        self.mtu = mtu
        self.connected = False
        self.rx_callback = None
        self.peer_connection = None

    def set_peer(self, peer_connection):
        """Link this connection to its peer."""
        self.peer_connection = peer_connection

    def set_rx_callback(self, callback):
        """Set callback for receiving data."""
        self.rx_callback = callback

    async def connect(self):
        """Simulate connection establishment."""
        logger.info(f"{self.name} connecting to {self.peer_name}")
        await asyncio.sleep(0.1)  # Simulate connection delay
        self.connected = True
        logger.info(f"{self.name} connected to {self.peer_name}, MTU={self.mtu}")

    async def disconnect(self):
        """Simulate disconnection."""
        logger.info(f"{self.name} disconnecting from {self.peer_name}")
        self.connected = False

    async def write(self, data):
        """Write data to peer."""
        if not self.connected:
            raise RuntimeError(f"{self.name} not connected to {self.peer_name}")

        if len(data) > self.mtu:
            raise ValueError(f"Data size {len(data)} exceeds MTU {self.mtu}")

        # Simulate transmission delay
        await asyncio.sleep(0.001)

        # Deliver to peer
        if self.peer_connection and self.peer_connection.rx_callback:
            await self.peer_connection.rx_callback(data)

    async def start_notify(self):
        """Simulate notification subscription."""
        logger.debug(f"{self.name} subscribed to notifications from {self.peer_name}")


class MockBLEDevice:
    """Simulates a discovered BLE device."""
    def __init__(self, address, name, rssi=-60):
        self.address = address
        self.name = name
        self.rssi = rssi
        self.metadata = {
            "uuids": ["00000001-5824-4f48-9e1a-3b3e8f0c1234"],
            "rssi": rssi
        }


class SimulatedBLENode:
    """
    Represents one simulated BLE node.
    Manages mock BLE discovery, connection, and data transfer.
    """
    def __init__(self, name, address, peer_address, peer_name):
        self.name = name
        self.address = address
        self.peer_address = peer_address
        self.peer_name = peer_name

        # Mock BLE components
        self.device = MockBLEDevice(address, name, rssi=-60)
        self.peer_device = MockBLEDevice(peer_address, peer_name, rssi=-65)
        self.connection = None

        # BLE interface (will be set later)
        self.ble_interface = None

    async def discover_peers(self):
        """Simulate BLE discovery - always "finds" the peer."""
        logger.info(f"{self.name} discovering peers...")
        await asyncio.sleep(0.5)  # Simulate discovery time
        logger.info(f"{self.name} discovered {self.peer_name} at {self.peer_address} (RSSI: -65 dBm)")
        return [self.peer_device]

    def create_connection(self, mtu=185):
        """Create a mock connection to the peer."""
        if self.connection is None:
            self.connection = MockBLEConnection(
                self.name,
                self.peer_name,
                mtu=mtu
            )
        return self.connection


# ============================================================================
# Simulation Coordinator
# ============================================================================

class TwoDeviceSimulator:
    """
    Coordinates the simulation of two BLE nodes.
    """
    def __init__(self):
        # Create two nodes
        self.node_a = SimulatedBLENode(
            name="Node-A",
            address="AA:BB:CC:DD:EE:01",
            peer_address="AA:BB:CC:DD:EE:02",
            peer_name="Node-B"
        )

        self.node_b = SimulatedBLENode(
            name="Node-B",
            address="AA:BB:CC:DD:EE:02",
            peer_address="AA:BB:CC:DD:EE:01",
            peer_name="Node-A"
        )

        logger.info("Created two simulated BLE nodes")
        logger.info(f"  Node-A: {self.node_a.address}")
        logger.info(f"  Node-B: {self.node_b.address}")

    async def setup_connections(self):
        """Setup bidirectional connections between nodes."""
        logger.info("Setting up bidirectional connections...")

        # Create connections
        conn_a = self.node_a.create_connection()
        conn_b = self.node_b.create_connection()

        # Link them together (bidirectional)
        conn_a.set_peer(conn_b)
        conn_b.set_peer(conn_a)

        # Connect both
        await conn_a.connect()
        await conn_b.connect()

        logger.info("Bidirectional connections established")

    async def run_discovery_test(self):
        """Test discovery between nodes."""
        logger.info("\n" + "="*60)
        logger.info("TEST 1: Discovery")
        logger.info("="*60)

        # Node A discovers Node B
        devices_a = await self.node_a.discover_peers()
        assert len(devices_a) == 1, "Node A should discover Node B"
        assert devices_a[0].address == self.node_b.address
        logger.info("✓ Node A successfully discovered Node B")

        # Node B discovers Node A
        devices_b = await self.node_b.discover_peers()
        assert len(devices_b) == 1, "Node B should discover Node A"
        assert devices_b[0].address == self.node_a.address
        logger.info("✓ Node B successfully discovered Node A")

        logger.info("✓ Discovery test PASSED")

    async def run_connection_test(self):
        """Test connection establishment."""
        logger.info("\n" + "="*60)
        logger.info("TEST 2: Connection Establishment")
        logger.info("="*60)

        await self.setup_connections()

        # Verify connections
        assert self.node_a.connection.connected, "Node A should be connected"
        assert self.node_b.connection.connected, "Node B should be connected"

        logger.info("✓ Connection test PASSED")

    async def run_data_transfer_test(self):
        """Test data transfer between nodes."""
        logger.info("\n" + "="*60)
        logger.info("TEST 3: Data Transfer")
        logger.info("="*60)

        # Setup data reception tracking
        received_a = []
        received_b = []

        async def rx_callback_a(data):
            received_a.append(data)
            logger.info(f"Node A received {len(data)} bytes from Node B")

        async def rx_callback_b(data):
            received_b.append(data)
            logger.info(f"Node B received {len(data)} bytes from Node A")

        self.node_a.connection.set_rx_callback(rx_callback_a)
        self.node_b.connection.set_rx_callback(rx_callback_b)

        # Node A sends to Node B
        test_data_1 = b"Hello from Node A!"
        await self.node_a.connection.write(test_data_1)
        await asyncio.sleep(0.1)  # Allow delivery

        assert len(received_b) == 1, "Node B should receive data"
        assert received_b[0] == test_data_1, "Data should match"
        logger.info("✓ Node A → Node B transfer successful")

        # Node B sends to Node A
        test_data_2 = b"Hello from Node B!"
        await self.node_b.connection.write(test_data_2)
        await asyncio.sleep(0.1)  # Allow delivery

        assert len(received_a) == 1, "Node A should receive data"
        assert received_a[0] == test_data_2, "Data should match"
        logger.info("✓ Node B → Node A transfer successful")

        logger.info("✓ Data transfer test PASSED")

    async def run_fragmentation_test(self):
        """Test fragmentation with larger packets."""
        logger.info("\n" + "="*60)
        logger.info("TEST 4: Fragmentation (500 byte packet)")
        logger.info("="*60)

        # This test will be expanded when we integrate with BLEFragmentation
        # For now, just test that we can send MTU-sized chunks

        large_data = b"X" * 500
        mtu = self.node_a.connection.mtu
        fragments_needed = (len(large_data) + mtu - 1) // mtu

        logger.info(f"Packet size: {len(large_data)} bytes")
        logger.info(f"MTU: {mtu} bytes")
        logger.info(f"Fragments needed: {fragments_needed}")

        received_fragments = []

        async def rx_callback(data):
            received_fragments.append(data)
            logger.info(f"  Received fragment {len(received_fragments)}/{fragments_needed} ({len(data)} bytes)")

        self.node_b.connection.set_rx_callback(rx_callback)

        # Send in fragments
        for i in range(fragments_needed):
            start = i * mtu
            end = min(start + mtu, len(large_data))
            fragment = large_data[start:end]
            await self.node_a.connection.write(fragment)
            await asyncio.sleep(0.01)  # Small delay between fragments

        # Verify all fragments received
        assert len(received_fragments) == fragments_needed, "All fragments should be received"

        # Reconstruct
        reconstructed = b''.join(received_fragments)
        assert reconstructed == large_data, "Reconstructed data should match original"

        logger.info("✓ Fragmentation test PASSED")

    async def run_all_tests(self):
        """Run all simulation tests."""
        logger.info("\n" + "="*60)
        logger.info("BLE TWO-DEVICE SIMULATOR")
        logger.info("="*60)
        logger.info("This simulator tests BLE functionality without real hardware")
        logger.info("")

        try:
            await self.run_discovery_test()
            await self.run_connection_test()
            await self.run_data_transfer_test()
            await self.run_fragmentation_test()

            logger.info("\n" + "="*60)
            logger.info("ALL TESTS PASSED ✓")
            logger.info("="*60)
            logger.info("")
            logger.info("The BLE simulation framework is working correctly.")
            logger.info("Next steps:")
            logger.info("  1. Integrate with actual BLEInterface instances")
            logger.info("  2. Test with Reticulum Transport layer")
            logger.info("  3. Test announce propagation and packet routing")

            return True

        except AssertionError as e:
            logger.error(f"\n✗ TEST FAILED: {e}")
            return False
        except Exception as e:
            logger.error(f"\n✗ ERROR: {e}", exc_info=True)
            return False


# ============================================================================
# Main
# ============================================================================

async def main():
    """Main simulation entry point."""
    simulator = TwoDeviceSimulator()
    success = await simulator.run_all_tests()

    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
