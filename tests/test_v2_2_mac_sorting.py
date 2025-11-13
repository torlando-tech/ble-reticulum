"""
Tests for BLE Protocol v2.2 MAC Address Sorting

MAC address sorting is a critical v2.2 feature that prevents dual-connection
race conditions in mesh networks. The protocol uses deterministic connection
direction based on MAC address comparison:

- Lower MAC address → Initiates connection (acts as central)
- Higher MAC address → Waits for connection (acts as peripheral only)

This ensures that when two devices discover each other, only ONE attempts to
connect, preventing connection storms and "Operation already in progress" errors.

Example:
  Device A (MAC: AA:BB:CC:DD:EE:FF)
  Device B (MAC: 11:22:33:44:55:66)

  B's MAC (0x112233445566) < A's MAC (0xAABBCCDDEEFF)
  → B initiates connection to A
  → A waits for B to connect (skips connection attempt)

Reference: BLE_PROTOCOL_v2.2.md §5 MAC-Based Connection Direction
"""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

# Mock RNS module before importing BLEInterface
from unittest.mock import Mock, MagicMock
import sys as _sys

# Create RNS mock structure
import RNS
if not hasattr(RNS, 'LOG_INFO'):
    RNS.LOG_CRITICAL = 0
    RNS.LOG_ERROR = 1
    RNS.LOG_WARNING = 2
    RNS.LOG_NOTICE = 3
    RNS.LOG_INFO = 4
    RNS.LOG_VERBOSE = 5
    RNS.LOG_DEBUG = 6
    RNS.LOG_EXTREME = 7
    RNS.log = lambda msg, level=4: None
    RNS.prettyhexrep = lambda data: data.hex() if isinstance(data, bytes) else str(data)
    RNS.hexrep = lambda data, delimit=True: data.hex() if isinstance(data, bytes) else str(data)

# Mock RNS.Transport
if not hasattr(RNS, 'Transport'):
    RNS.Transport = MagicMock()
    RNS.Transport.interfaces = []

# Mock RNS.Identity
if not hasattr(RNS, 'Identity'):
    RNS.Identity = MagicMock()
    RNS.Identity.full_hash = lambda x: (x * 2)[:16]

# Mock RNS.Interfaces.Interface (required by BLEInterface.py)
if 'RNS.Interfaces' not in _sys.modules:
    rns_interfaces_mock = MagicMock()
    _sys.modules['RNS.Interfaces'] = rns_interfaces_mock

    # Create mock Interface base class
    class MockInterface:
        MODE_FULL = 1
        def __init__(self):
            self.IN = True
            self.OUT = True
            self.online = False

    rns_interfaces_mock.Interface = MockInterface

from tests.mock_ble_driver import MockBLEDriver
from RNS.Interfaces.BLEInterface import BLEInterface, DiscoveredPeer
import time


class MockOwner:
    """Mock Reticulum owner."""
    def __init__(self):
        self.inbound_calls = []

    def inbound(self, data, interface):
        self.inbound_calls.append((data, interface))


class TestMACComparison:
    """Test MAC address comparison logic."""

    def test_lower_mac_initiates(self):
        """Test that device with lower MAC initiates connection."""
        driver = MockBLEDriver(local_address="11:22:33:44:55:66")  # Lower MAC
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        # Discover peer with higher MAC
        peer_address = "AA:BB:CC:DD:EE:FF"
        peer = DiscoveredPeer(peer_address, "HigherMAC", -60)
        interface.discovered_peers[peer_address] = peer

        # Select peers to connect
        peers_to_connect = interface._select_peers_to_connect()

        # Should attempt to connect (our MAC is lower)
        peer_addresses = [p.address for p in peers_to_connect]
        assert peer_address in peer_addresses

    def test_higher_mac_waits(self):
        """Test that device with higher MAC does NOT initiate connection."""
        driver = MockBLEDriver(local_address="FF:EE:DD:CC:BB:AA")  # Higher MAC
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        # Discover peer with lower MAC
        peer_address = "11:22:33:44:55:66"
        peer = DiscoveredPeer(peer_address, "LowerMAC", -60)
        interface.discovered_peers[peer_address] = peer

        # Select peers to connect
        peers_to_connect = interface._select_peers_to_connect()

        # Should NOT attempt to connect (our MAC is higher, we wait)
        peer_addresses = [p.address for p in peers_to_connect]
        assert peer_address not in peer_addresses

    def test_mac_comparison_case_insensitive(self):
        """Test that MAC comparison is case-insensitive."""
        driver = MockBLEDriver(local_address="aa:bb:cc:dd:ee:ff")  # Lowercase
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        # Discover peer with uppercase MAC (lower value)
        peer_address = "11:22:33:44:55:66"
        peer = DiscoveredPeer(peer_address, "Peer", -60)
        interface.discovered_peers[peer_address] = peer

        # Should still correctly determine we have higher MAC
        peers_to_connect = interface._select_peers_to_connect()
        peer_addresses = [p.address for p in peers_to_connect]

        # Our MAC (0xaabbccddeeff) > peer MAC (0x112233445566)
        # So we should NOT connect
        assert peer_address not in peer_addresses


class TestMACEdgeCases:
    """Test edge cases in MAC address sorting."""

    def test_same_mac_address(self):
        """Test behavior when local and peer MAC are identical (should not happen in practice)."""
        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        # Discover peer with same MAC (edge case)
        peer_address = "AA:BB:CC:DD:EE:FF"
        peer = DiscoveredPeer(peer_address, "SameMAC", -60)
        interface.discovered_peers[peer_address] = peer

        # Select peers - should handle gracefully
        try:
            peers_to_connect = interface._select_peers_to_connect()
            # If same MAC, we're higher is false, so we should attempt connection
            # (Though this should never happen with real BLE hardware)
            peer_addresses = [p.address for p in peers_to_connect]
            # Implementation detail: equal MACs fall through to connection attempt
        except Exception as e:
            pytest.fail(f"MAC sorting should handle equal MACs gracefully: {e}")

    def test_sequential_mac_addresses(self):
        """Test with sequential MAC addresses."""
        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:01")
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        # Add multiple peers with sequential MACs
        peers_to_discover = [
            ("AA:BB:CC:DD:EE:00", -60),  # Lower than us
            ("AA:BB:CC:DD:EE:02", -60),  # Higher than us
            ("AA:BB:CC:DD:EE:FF", -60),  # Much higher
        ]

        for addr, rssi in peers_to_discover:
            peer = DiscoveredPeer(addr, f"Peer-{addr[-2:]}", rssi)
            interface.discovered_peers[addr] = peer

        # Select peers
        peers_to_connect = interface._select_peers_to_connect()
        peer_addresses = [p.address for p in peers_to_connect]

        # Should only connect to peer with lower MAC (00)
        assert "AA:BB:CC:DD:EE:00" in peer_addresses
        assert "AA:BB:CC:DD:EE:02" not in peer_addresses
        assert "AA:BB:CC:DD:EE:FF" not in peer_addresses


class TestDualConnectionPrevention:
    """Test that MAC sorting prevents dual-connection attempts."""

    def test_prevents_both_devices_connecting(self):
        """Test that only lower-MAC device attempts connection."""
        # Create two devices with different MACs
        device_low = MockBLEDriver(local_address="11:11:11:11:11:11")
        device_high = MockBLEDriver(local_address="99:99:99:99:99:99")

        owner_low = MockOwner()
        owner_high = MockOwner()

        config = {"name": "Test", "enable_central": True}

        interface_low = BLEInterface(owner_low, config)
        interface_low.driver = device_low
        interface_low.local_address = device_low.local_address

        interface_high = BLEInterface(owner_high, config)
        interface_high.driver = device_high
        interface_high.local_address = device_high.local_address

        # Both discover each other
        peer_low = DiscoveredPeer(device_low.local_address, "DeviceLow", -60)
        peer_high = DiscoveredPeer(device_high.local_address, "DeviceHigh", -60)

        interface_low.discovered_peers[device_high.local_address] = peer_high
        interface_high.discovered_peers[device_low.local_address] = peer_low

        # Select peers on both sides
        low_connections = interface_low._select_peers_to_connect()
        high_connections = interface_high._select_peers_to_connect()

        low_addresses = [p.address for p in low_connections]
        high_addresses = [p.address for p in high_connections]

        # Only low-MAC device should attempt connection
        assert device_high.local_address in low_addresses  # Low connects to high
        assert device_low.local_address not in high_addresses  # High does NOT connect to low

    def test_mac_sorting_with_multiple_peers(self):
        """Test MAC sorting with multiple peers of varying MACs."""
        driver = MockBLEDriver(local_address="55:55:55:55:55:55")  # Middle value
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        # Add peers with MACs above and below ours
        peers_data = [
            ("11:11:11:11:11:11", -60),  # Below (should connect)
            ("22:22:22:22:22:22", -60),  # Below (should connect)
            ("AA:AA:AA:AA:AA:AA", -60),  # Above (should NOT connect)
            ("FF:FF:FF:FF:FF:FF", -60),  # Above (should NOT connect)
        ]

        for addr, rssi in peers_data:
            peer = DiscoveredPeer(addr, f"Peer-{addr[:2]}", rssi)
            interface.discovered_peers[addr] = peer

        # Select peers
        peers_to_connect = interface._select_peers_to_connect()
        peer_addresses = [p.address for p in peers_to_connect]

        # Should connect to lower MACs only
        assert "11:11:11:11:11:11" in peer_addresses
        assert "22:22:22:22:22:22" in peer_addresses
        assert "AA:AA:AA:AA:AA:AA" not in peer_addresses
        assert "FF:FF:FF:FF:FF:FF" not in peer_addresses


class TestMACParsingErrors:
    """Test MAC parsing error handling."""

    def test_invalid_mac_format_fallthrough(self):
        """Test that invalid MAC format falls through to normal connection logic."""
        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = "INVALID-MAC"  # Invalid format

        # Add peer
        peer_address = "11:22:33:44:55:66"
        peer = DiscoveredPeer(peer_address, "Peer", -60)
        interface.discovered_peers[peer_address] = peer

        # Should handle gracefully and fall through
        try:
            peers_to_connect = interface._select_peers_to_connect()
            # Invalid MAC should fail parsing and fall through to connection attempt
        except Exception as e:
            pytest.fail(f"Invalid MAC should be handled gracefully: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
