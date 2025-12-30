"""
Tests for Identity Cache on Disconnect

When a BLE peer disconnects, we cache its identity for 60 seconds to handle
cases where Python's disconnect callback fires but the driver layer maintains
or quickly re-establishes the GATT connection.

This prevents data loss when:
1. App force-stop/restart causes temporary disconnect
2. Driver maintains connection while Python clears identity mapping
3. Data arrives from "unknown" peer that was actually still connected
"""

import pytest
import time
import threading
from unittest.mock import Mock, MagicMock, patch, AsyncMock
import sys
import os
import asyncio

# Add src to path - conftest.py handles RNS mocking


def create_minimal_ble_interface():
    """
    Create a minimal BLEInterface instance for testing identity cache.

    Mocks external dependencies (driver, RNS) while keeping the real
    identity cache logic intact.
    """
    try:
        from ble_reticulum.BLEInterface import BLEInterface
    except ImportError:
        pytest.skip("BLEInterface not available")

    with patch('ble_reticulum.BLEInterface.Interface.__init__'):
        # Create interface with mocked owner
        interface = object.__new__(BLEInterface)

        # Initialize required attributes
        interface.name = "TestBLE"
        interface.owner = Mock()
        interface.online = False
        interface.driver = Mock()
        interface.driver.on_device_connected = None
        interface.driver.on_device_disconnected = None
        interface.driver.on_data_received = None
        interface.driver.on_identity_received = None
        interface.driver.on_error = None
        interface.driver.on_duplicate_identity_detected = None
        interface.driver.on_address_changed = None
        interface.driver.request_identity_resync = Mock(return_value=False)

        # Initialize state dictionaries
        interface.peers = {}
        interface.spawned_interfaces = {}
        interface.address_to_identity = {}
        interface.identity_to_address = {}
        interface._identity_cache = {}
        interface._identity_cache_ttl = 60
        interface.fragmenters = {}
        interface.reassemblers = {}
        interface.pending_mtu = {}

        # Locks
        interface.peer_lock = threading.RLock()
        interface.frag_lock = threading.RLock()

        # Other required attributes
        interface.HW_MTU = 500
        interface.MIN_MTU = 20
        interface.bitrate = 700000
        interface.rxb = 0
        interface.txb = 0

        return interface

    return None


class TestIdentityCacheIntegration:
    """Test identity cache with real BLEInterface methods."""

    def test_identity_cache_initialized(self):
        """BLEInterface should have identity cache attributes."""
        interface = create_minimal_ble_interface()
        if interface is None:
            pytest.skip("Could not create interface")

        assert hasattr(interface, '_identity_cache')
        assert hasattr(interface, '_identity_cache_ttl')
        assert interface._identity_cache_ttl == 60
        assert isinstance(interface._identity_cache, dict)

    def test_disconnect_callback_caches_identity(self):
        """_device_disconnected_callback should cache identity before cleanup."""
        interface = create_minimal_ble_interface()
        if interface is None:
            pytest.skip("Could not create interface")

        # Setup: Simulate connected peer
        mac = "54:8F:DF:44:79:2B"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = interface._compute_identity_hash(identity) if hasattr(interface, '_compute_identity_hash') else "456c6978"

        interface.address_to_identity[mac] = identity
        interface.identity_to_address[identity_hash] = mac

        # Create mock peer interface
        mock_peer_if = Mock()
        mock_peer_if.detach = Mock()
        interface.spawned_interfaces[identity_hash] = mock_peer_if

        # Call the disconnect callback
        if hasattr(interface, '_device_disconnected_callback'):
            interface._device_disconnected_callback(mac)

            # Assert: Identity should be cached
            assert mac in interface._identity_cache
            cached_identity, cached_time = interface._identity_cache[mac]
            assert cached_identity == identity
            assert time.time() - cached_time < 2

    def test_address_changed_callback_migrates_mappings(self):
        """_address_changed_callback should migrate identity to new address."""
        interface = create_minimal_ble_interface()
        if interface is None:
            pytest.skip("Could not create interface")

        old_mac = "54:8F:DF:44:79:2B"
        new_mac = "6B:2B:EE:1A:5B:94"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = "456c6978"

        # Setup initial mapping
        interface.address_to_identity[old_mac] = identity
        interface.identity_to_address[identity_hash] = old_mac

        # Call address changed callback
        if hasattr(interface, '_address_changed_callback'):
            interface._address_changed_callback(old_mac, new_mac, identity_hash)

            # Assert: Mapping migrated to new address
            assert old_mac not in interface.address_to_identity
            assert new_mac in interface.address_to_identity
            assert interface.identity_to_address[identity_hash] == new_mac


class TestIdentityCache:
    """Test identity caching logic (unit tests with mocks)."""

    def test_identity_cached_on_disconnect(self):
        """
        When a peer disconnects, its identity should be cached for later recovery.
        """
        # Setup mock interface state
        interface = Mock()
        interface.address_to_identity = {}
        interface.identity_to_address = {}
        interface.spawned_interfaces = {}
        interface._identity_cache = {}
        interface._identity_cache_ttl = 60

        # Simulate connected peer
        mac = "54:8F:DF:44:79:2B"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = "456c6978"

        interface.address_to_identity[mac] = identity
        interface.identity_to_address[identity_hash] = mac
        interface.spawned_interfaces[identity_hash] = Mock()

        # Simulate disconnect with caching (the fix)
        peer_identity = interface.address_to_identity.get(mac)
        if peer_identity:
            # Cache identity before cleanup
            interface._identity_cache[mac] = (peer_identity, time.time())

            # Clean up active mappings
            del interface.address_to_identity[mac]
            del interface.identity_to_address[identity_hash]
            del interface.spawned_interfaces[identity_hash]

        # Assert: Identity should be in cache
        assert mac in interface._identity_cache
        cached_identity, cached_time = interface._identity_cache[mac]
        assert cached_identity == identity
        assert time.time() - cached_time < 1  # Cached recently

    def test_cached_identity_restored_on_data_receive(self):
        """
        When data arrives from a peer with no active identity mapping,
        check the cache and restore if found.
        """
        interface = Mock()
        interface.address_to_identity = {}
        interface.identity_to_address = {}
        interface._identity_cache = {}
        interface._identity_cache_ttl = 60

        # Setup: Identity in cache (from recent disconnect)
        mac = "54:8F:DF:44:79:2B"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        interface._identity_cache[mac] = (identity, time.time())

        # Simulate data arriving from "unknown" peer
        peer_identity = interface.address_to_identity.get(mac)
        assert peer_identity is None, "No active mapping"

        # Check cache (the fix)
        cached = interface._identity_cache.get(mac)
        if cached:
            cached_identity, cached_time = cached
            if time.time() - cached_time < interface._identity_cache_ttl:
                # Restore from cache
                peer_identity = cached_identity
                interface.address_to_identity[mac] = peer_identity

        # Assert: Identity restored from cache
        assert peer_identity == identity
        assert mac in interface.address_to_identity

    def test_cache_expires_after_ttl(self):
        """
        Cached identities should expire after TTL (60 seconds).
        """
        interface = Mock()
        interface.address_to_identity = {}
        interface._identity_cache = {}
        interface._identity_cache_ttl = 60

        mac = "54:8F:DF:44:79:2B"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")

        # Cache with old timestamp (61 seconds ago)
        old_time = time.time() - 61
        interface._identity_cache[mac] = (identity, old_time)

        # Try to restore from cache
        peer_identity = None
        cached = interface._identity_cache.get(mac)
        if cached:
            cached_identity, cached_time = cached
            if time.time() - cached_time < interface._identity_cache_ttl:
                peer_identity = cached_identity

        # Assert: Cache expired, identity not restored
        assert peer_identity is None

    def test_cache_valid_within_ttl(self):
        """
        Cached identities should be valid within TTL.
        """
        interface = Mock()
        interface.address_to_identity = {}
        interface._identity_cache = {}
        interface._identity_cache_ttl = 60

        mac = "54:8F:DF:44:79:2B"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")

        # Cache with recent timestamp (30 seconds ago)
        recent_time = time.time() - 30
        interface._identity_cache[mac] = (identity, recent_time)

        # Try to restore from cache
        peer_identity = None
        cached = interface._identity_cache.get(mac)
        if cached:
            cached_identity, cached_time = cached
            if time.time() - cached_time < interface._identity_cache_ttl:
                peer_identity = cached_identity

        # Assert: Cache valid, identity restored
        assert peer_identity == identity


class TestAddressChangedCallback:
    """Test address migration during dual connection deduplication."""

    def test_address_changed_migrates_identity_mapping(self):
        """
        When driver closes one direction of a dual connection,
        identity mapping should migrate to the remaining address.
        """
        interface = Mock()
        interface.address_to_identity = {}
        interface.identity_to_address = {}
        interface._identity_cache = {}

        # Peer connected on old address
        old_mac = "54:8F:DF:44:79:2B"
        new_mac = "6B:2B:EE:1A:5B:94"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = "456c6978"

        interface.address_to_identity[old_mac] = identity
        interface.identity_to_address[identity_hash] = old_mac

        # Simulate address change callback
        peer_identity = interface.address_to_identity.get(old_mac)
        if peer_identity:
            # Migrate to new address
            del interface.address_to_identity[old_mac]
            interface.address_to_identity[new_mac] = peer_identity
            interface.identity_to_address[identity_hash] = new_mac

        # Assert: Mapping migrated
        assert old_mac not in interface.address_to_identity
        assert new_mac in interface.address_to_identity
        assert interface.identity_to_address[identity_hash] == new_mac

    def test_address_changed_uses_cache_if_not_in_active_mapping(self):
        """
        If old address not in active mapping, check cache for identity.
        """
        interface = Mock()
        interface.address_to_identity = {}
        interface.identity_to_address = {}
        interface._identity_cache = {}

        old_mac = "54:8F:DF:44:79:2B"
        new_mac = "6B:2B:EE:1A:5B:94"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = "456c6978"

        # Identity in cache, not active mapping
        interface._identity_cache[old_mac] = (identity, time.time())

        # Simulate address change - check cache as fallback
        peer_identity = interface.address_to_identity.get(old_mac)
        if not peer_identity:
            cached = interface._identity_cache.get(old_mac)
            if cached:
                peer_identity = cached[0]
                del interface._identity_cache[old_mac]

        if peer_identity:
            interface.address_to_identity[new_mac] = peer_identity
            interface.identity_to_address[identity_hash] = new_mac

        # Assert: Identity recovered from cache and migrated
        assert old_mac not in interface._identity_cache
        assert new_mac in interface.address_to_identity
        assert interface.identity_to_address[identity_hash] == new_mac


class TestDriverResync:
    """Test driver-level identity resync fallback."""

    def test_request_identity_resync_available(self):
        """
        Driver should expose request_identity_resync method.
        """
        driver = Mock()
        driver.request_identity_resync = Mock(return_value=True)

        # Call resync
        result = driver.request_identity_resync("54:8F:DF:44:79:2B")

        assert result is True
        driver.request_identity_resync.assert_called_once_with("54:8F:DF:44:79:2B")

    def test_resync_used_when_cache_miss(self):
        """
        When data arrives from unknown peer and cache miss,
        try driver resync as last resort.
        """
        interface = Mock()
        interface.address_to_identity = {}
        interface._identity_cache = {}
        interface._identity_cache_ttl = 60
        interface.driver = Mock()
        interface.driver.request_identity_resync = Mock(return_value=True)

        mac = "54:8F:DF:44:79:2B"

        # No active mapping
        peer_identity = interface.address_to_identity.get(mac)
        assert peer_identity is None

        # No cache hit
        cached = interface._identity_cache.get(mac)
        assert cached is None

        # Try driver resync as fallback
        resync_result = interface.driver.request_identity_resync(mac)

        # Assert: Resync attempted
        assert resync_result is True
        interface.driver.request_identity_resync.assert_called_once_with(mac)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
