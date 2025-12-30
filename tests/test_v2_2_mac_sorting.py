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

# Mock ble_reticulum.Interface module (the base class module, not the whole namespace)
# We only mock the Interface.py module, allowing BLEInterface.py to be imported from src/
if 'ble_reticulum.Interface' not in _sys.modules:
    # Create mock Interface base class
    class MockInterface:
        MODE_FULL = 1
        def __init__(self):
            self.IN = True
            self.OUT = True
            self.online = False

        @staticmethod
        def get_config_obj(configuration):
            """Mock config object wrapper - just returns a dict-like object."""
            class ConfigObj:
                def __init__(self, config):
                    self._config = config if config else {}

                def __getitem__(self, key):
                    return self._config.get(key)

                def get(self, key, default=None):
                    return self._config.get(key, default)

                def as_string(self, key, default=None):
                    val = self._config.get(key, default)
                    return str(val) if val is not None else default

                def as_int(self, key, default=None):
                    val = self._config.get(key, default)
                    return int(val) if val is not None else default

                def as_bool(self, key, default=False):
                    val = self._config.get(key, default)
                    if isinstance(val, bool):
                        return val
                    if isinstance(val, str):
                        return val.lower() in ('true', 'yes', '1', 'on')
                    return bool(val) if val is not None else default
            return ConfigObj(configuration)

    # Create a mock module for ble_reticulum.Interface
    interface_module = MagicMock()
    interface_module.Interface = MockInterface
    _sys.modules['ble_reticulum.Interface'] = interface_module

from tests.mock_ble_driver import MockBLEDriver
from ble_reticulum.BLEInterface import BLEInterface, DiscoveredPeer
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

        # MAC sorting: lower MAC initiates. Our MAC is AA:BB:CC:DD:EE:01
        # - AA:BB:CC:DD:EE:00 is LOWER than us, so THEY initiate (we skip)
        # - AA:BB:CC:DD:EE:02 is HIGHER than us, so WE initiate
        # - AA:BB:CC:DD:EE:FF is HIGHER than us, so WE initiate
        assert "AA:BB:CC:DD:EE:00" not in peer_addresses  # They initiate
        assert "AA:BB:CC:DD:EE:02" in peer_addresses  # We initiate
        assert "AA:BB:CC:DD:EE:FF" in peer_addresses  # We initiate


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
        # MAC sorting: lower MAC initiates. Our MAC is 55:55:55:55:55:55
        peers_data = [
            ("11:11:11:11:11:11", -60),  # LOWER than us - THEY initiate, we skip
            ("22:22:22:22:22:22", -60),  # LOWER than us - THEY initiate, we skip
            ("AA:AA:AA:AA:AA:AA", -60),  # HIGHER than us - WE initiate
            ("FF:FF:FF:FF:FF:FF", -60),  # HIGHER than us - WE initiate
        ]

        for addr, rssi in peers_data:
            peer = DiscoveredPeer(addr, f"Peer-{addr[:2]}", rssi)
            interface.discovered_peers[addr] = peer

        # Select peers
        peers_to_connect = interface._select_peers_to_connect()
        peer_addresses = [p.address for p in peers_to_connect]

        # MAC sorting: lower MAC initiates, so we connect to HIGHER MACs
        assert "11:11:11:11:11:11" not in peer_addresses  # They initiate
        assert "22:22:22:22:22:22" not in peer_addresses  # They initiate
        assert "AA:AA:AA:AA:AA:AA" in peer_addresses  # We initiate
        assert "FF:FF:FF:FF:FF:FF" in peer_addresses  # We initiate


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


class TestMACRotationBypassesSorting:
    """
    Test that MAC rotation bypasses MAC sorting.

    Bug fix: After MAC rotation cleanup, the peer must be added to the connection
    list regardless of MAC sorting. Previously, the code fell through to the MAC
    sorting check which could skip the peer if local MAC > peer MAC.

    Fix: After _cleanup_stale_interface(), immediately add peer and continue,
    bypassing the MAC sorting check.
    """

    def test_mac_rotation_bypasses_sorting_when_local_mac_higher(self):
        """
        Test that MAC rotation adds peer even when local MAC is higher.

        This is the core bug fix test. Without the fix:
        - MAC rotation detected, stale interface cleaned up
        - Code falls through to MAC sorting check
        - Local MAC (FF:...) > Peer MAC (11:...) → peer skipped
        - Peer interface never recreated!

        With the fix:
        - MAC rotation detected, stale interface cleaned up
        - Peer immediately added, continue (bypass MAC sorting)
        - Peer interface recreated correctly
        """
        driver = MockBLEDriver(local_address="FF:FF:FF:FF:FF:FF")  # Higher MAC
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        # Set up MAC rotation scenario:
        # - Identity exists at old address
        # - Peer discovered at new address (lower MAC)
        # - Old connection is stale (not in peers dict)
        old_address = "AA:AA:AA:AA:AA:AA"
        new_address = "11:22:33:44:55:66"  # Lower than local MAC
        peer_identity = bytes.fromhex("ab5609dfffb33b21a102e1ff81196be5")
        identity_hash = interface._compute_identity_hash(peer_identity)

        # Set up existing identity mapping at old address
        interface.identity_to_address[identity_hash] = old_address
        interface.address_to_identity[new_address] = peer_identity

        # Create a mock spawned interface (stale)
        mock_peer_interface = MagicMock()
        interface.spawned_interfaces[identity_hash] = mock_peer_interface

        # old_address NOT in interface.peers (connection is dead/stale)

        # Discover peer at new address
        peer = DiscoveredPeer(new_address, "RNS-ab5609", -60)
        interface.discovered_peers[new_address] = peer

        # Select peers to connect
        peers_to_connect = interface._select_peers_to_connect()
        peer_addresses = [p.address for p in peers_to_connect]

        # Even though local MAC > peer MAC, peer should be added due to MAC rotation
        assert new_address in peer_addresses, \
            "MAC rotation should bypass MAC sorting and add peer"

    def test_mac_rotation_cleanup_is_called(self):
        """Test that _cleanup_stale_interface is called during MAC rotation."""
        driver = MockBLEDriver(local_address="FF:FF:FF:FF:FF:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        # Track cleanup calls
        cleanup_calls = []
        original_cleanup = interface._cleanup_stale_interface

        def tracked_cleanup(identity_hash, old_address):
            cleanup_calls.append((identity_hash, old_address))
            return original_cleanup(identity_hash, old_address)

        interface._cleanup_stale_interface = tracked_cleanup

        # Set up MAC rotation scenario
        old_address = "AA:AA:AA:AA:AA:AA"
        new_address = "11:22:33:44:55:66"
        peer_identity = bytes.fromhex("ab5609dfffb33b21a102e1ff81196be5")
        identity_hash = interface._compute_identity_hash(peer_identity)

        interface.identity_to_address[identity_hash] = old_address
        interface.address_to_identity[new_address] = peer_identity

        mock_peer_interface = MagicMock()
        interface.spawned_interfaces[identity_hash] = mock_peer_interface

        # Discover peer at new address
        peer = DiscoveredPeer(new_address, "RNS-ab5609", -60)
        interface.discovered_peers[new_address] = peer

        # Select peers
        interface._select_peers_to_connect()

        # Verify cleanup was called
        assert len(cleanup_calls) == 1
        assert cleanup_calls[0] == (identity_hash, old_address)

    def test_active_connection_prevents_rotation_cleanup(self):
        """Test that active connection prevents MAC rotation cleanup."""
        driver = MockBLEDriver(local_address="FF:FF:FF:FF:FF:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        # Set up scenario where old connection is ACTIVE
        old_address = "AA:AA:AA:AA:AA:AA"
        new_address = "11:22:33:44:55:66"
        peer_identity = bytes.fromhex("ab5609dfffb33b21a102e1ff81196be5")
        identity_hash = interface._compute_identity_hash(peer_identity)

        interface.identity_to_address[identity_hash] = old_address
        interface.address_to_identity[new_address] = peer_identity

        mock_peer_interface = MagicMock()
        interface.spawned_interfaces[identity_hash] = mock_peer_interface

        # OLD connection is ACTIVE (in peers dict)
        interface.peers[old_address] = {"mtu": 512}

        # Discover peer at new address
        peer = DiscoveredPeer(new_address, "RNS-ab5609", -60)
        interface.discovered_peers[new_address] = peer

        # Select peers
        peers_to_connect = interface._select_peers_to_connect()
        peer_addresses = [p.address for p in peers_to_connect]

        # Should NOT add peer (old connection still active)
        assert new_address not in peer_addresses, \
            "Active connection should prevent MAC rotation"

    def test_normal_mac_sorting_still_works(self):
        """Test that normal MAC sorting still works when no rotation."""
        driver = MockBLEDriver(local_address="FF:FF:FF:FF:FF:FF")  # Higher MAC
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        # No existing identity mapping - this is a completely new peer
        peer_address = "11:22:33:44:55:66"  # Lower MAC
        peer = DiscoveredPeer(peer_address, "NewPeer", -60)
        interface.discovered_peers[peer_address] = peer

        # Select peers
        peers_to_connect = interface._select_peers_to_connect()
        peer_addresses = [p.address for p in peers_to_connect]

        # Should NOT add peer (they have lower MAC, they should initiate)
        assert peer_address not in peer_addresses, \
            "Normal MAC sorting should skip peer with lower MAC"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
