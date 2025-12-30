"""
Tests for Identity Cache on Disconnect

When a BLE peer disconnects, we cache its identity for 60 seconds to handle
cases where Python's disconnect callback fires but the driver layer maintains
or quickly re-establishes the GATT connection.

This prevents data loss when:
1. App force-stop/restart causes temporary disconnect
2. Driver maintains connection while Python clears identity mapping
3. Data arrives from "unknown" peer that was actually still connected

These tests exercise the REAL BLEInterface code with mocked external dependencies.
"""

import pytest
import time
import threading
from unittest.mock import Mock, MagicMock, patch, PropertyMock
import sys
import os

# Ensure src is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))


@pytest.fixture
def mock_rns():
    """Mock RNS module for testing."""
    with patch.dict('sys.modules', {'RNS': MagicMock()}):
        import sys
        rns = sys.modules['RNS']
        rns.LOG_CRITICAL = 0
        rns.LOG_ERROR = 1
        rns.LOG_WARNING = 2
        rns.LOG_NOTICE = 3
        rns.LOG_INFO = 4
        rns.LOG_VERBOSE = 5
        rns.LOG_DEBUG = 6
        rns.LOG_EXTREME = 7
        rns.log = Mock()
        rns.prettyhexrep = lambda x: x.hex() if isinstance(x, bytes) else str(x)
        yield rns


@pytest.fixture
def mock_driver():
    """Create a mock BLE driver."""
    driver = Mock()
    driver.on_device_connected = None
    driver.on_device_disconnected = None
    driver.on_data_received = None
    driver.on_identity_received = None
    driver.on_error = None
    driver.on_duplicate_identity_detected = None
    driver.on_address_changed = None
    driver.request_identity_resync = Mock(return_value=False)
    driver.get_cached_identity = Mock(return_value=None)
    return driver


@pytest.fixture
def ble_interface(mock_rns, mock_driver):
    """
    Create a BLEInterface instance with mocked dependencies.

    This uses the REAL BLEInterface class but mocks external dependencies
    to allow testing the identity cache logic.
    """
    # Try to import BLEInterface - this will work in CI where RNS is installed
    try:
        from ble_reticulum.BLEInterface import BLEInterface

        # Create interface without calling __init__
        interface = object.__new__(BLEInterface)

        # Initialize all required attributes manually
        interface.name = "TestBLE"
        interface.owner = Mock()
        interface.online = True
        interface.driver = mock_driver

        # Identity mappings
        interface.peers = {}
        interface.spawned_interfaces = {}
        interface.address_to_identity = {}
        interface.identity_to_address = {}
        interface._identity_cache = {}
        interface._identity_cache_ttl = 60

        # Fragmentation
        interface.fragmenters = {}
        interface.reassemblers = {}
        interface.pending_mtu = {}

        # Locks
        interface.peer_lock = threading.RLock()
        interface.frag_lock = threading.RLock()

        # Other attributes
        interface.HW_MTU = 500
        interface.MIN_MTU = 20
        interface.bitrate = 700000
        interface.rxb = 0
        interface.txb = 0

        return interface

    except ImportError as e:
        pytest.skip(f"Cannot import BLEInterface (RNS not installed): {e}")


class TestIdentityCacheOnDisconnect:
    """Test that identity is cached when peer disconnects."""

    def test_disconnect_caches_identity(self, ble_interface, mock_rns):
        """
        When _device_disconnected_callback is called, the peer's identity
        should be cached before cleanup.
        """
        mac = "54:8F:DF:44:79:2B"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()  # First 8 bytes = 16 hex chars

        # Setup: Peer is connected with identity mapping
        ble_interface.address_to_identity[mac] = identity
        ble_interface.identity_to_address[identity_hash] = mac

        # Create mock peer interface
        mock_peer_if = Mock()
        mock_peer_if.detach = Mock()
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if

        # Add _compute_identity_hash method
        ble_interface._compute_identity_hash = lambda x: x[:8].hex()

        # Call the real disconnect callback
        ble_interface._device_disconnected_callback(mac)

        # Assert: Identity should be cached
        assert mac in ble_interface._identity_cache
        cached_identity, cached_time = ble_interface._identity_cache[mac]
        assert cached_identity == identity
        assert time.time() - cached_time < 2  # Cached recently

        # Assert: Active mappings should be cleaned up
        assert mac not in ble_interface.address_to_identity
        assert identity_hash not in ble_interface.identity_to_address

        # Assert: Peer interface was detached
        mock_peer_if.detach.assert_called_once()

    def test_disconnect_unknown_address_no_crash(self, ble_interface, mock_rns):
        """
        Disconnecting an unknown address should not crash.
        """
        # Call disconnect for unknown address
        ble_interface._device_disconnected_callback("XX:XX:XX:XX:XX:XX")

        # Should not raise, cache should be empty
        assert "XX:XX:XX:XX:XX:XX" not in ble_interface._identity_cache


class TestIdentityCacheOnDataReceive:
    """Test that cached identity is restored when data arrives."""

    def test_data_restores_identity_from_cache(self, ble_interface, mock_rns):
        """
        When data arrives from a peer with cached (but not active) identity,
        the identity should be restored from cache.
        """
        mac = "54:8F:DF:44:79:2B"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()

        # Setup: Identity in cache (simulating recent disconnect)
        ble_interface._identity_cache[mac] = (identity, time.time())

        # Add required methods
        ble_interface._compute_identity_hash = lambda x: x[:8].hex()
        ble_interface._get_fragmenter_key = lambda ident, addr: f"{ident[:8].hex()}_{addr}"

        # Create mock fragmenter/reassembler
        from unittest.mock import MagicMock
        mock_reassembler = MagicMock()
        mock_reassembler.add_fragment = Mock(return_value=(False, None))

        frag_key = f"{identity_hash}_{mac}"
        ble_interface.reassemblers[frag_key] = mock_reassembler

        # Create mock peer interface
        mock_peer_if = Mock()
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if

        # Call _handle_ble_data with test data
        test_data = b"\x00\x01test_packet"  # Fragment with header
        ble_interface._handle_ble_data(mac, test_data)

        # Assert: Identity should be restored from cache
        assert mac in ble_interface.address_to_identity
        assert ble_interface.address_to_identity[mac] == identity
        assert ble_interface.identity_to_address[identity_hash] == mac

        # Assert: Cache entry should be removed (now active)
        assert mac not in ble_interface._identity_cache

    def test_data_expired_cache_requests_resync(self, ble_interface, mock_rns):
        """
        When data arrives with expired cache entry, request identity resync.
        """
        mac = "54:8F:DF:44:79:2B"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")

        # Setup: Expired cache entry (61 seconds ago)
        ble_interface._identity_cache[mac] = (identity, time.time() - 61)

        # Add required methods
        ble_interface._compute_identity_hash = lambda x: x[:8].hex()

        # Call _handle_ble_data
        test_data = b"\x00\x01test"
        ble_interface._handle_ble_data(mac, test_data)

        # Assert: Should request resync from driver
        ble_interface.driver.request_identity_resync.assert_called_once_with(mac)

        # Assert: Identity should NOT be restored (expired)
        assert mac not in ble_interface.address_to_identity

    def test_data_no_cache_no_identity_requests_resync(self, ble_interface, mock_rns):
        """
        When data arrives with no identity and no cache, request resync.
        """
        mac = "54:8F:DF:44:79:2B"

        # Add required methods
        ble_interface._compute_identity_hash = lambda x: x[:8].hex()

        # No cache, no identity mapping
        assert mac not in ble_interface._identity_cache
        assert mac not in ble_interface.address_to_identity

        # Call _handle_ble_data
        test_data = b"\x00\x01test"
        ble_interface._handle_ble_data(mac, test_data)

        # Assert: Should request resync
        ble_interface.driver.request_identity_resync.assert_called_once_with(mac)


class TestAddressChangedCallback:
    """Test address migration during dual connection deduplication."""

    def test_address_changed_migrates_mappings(self, ble_interface, mock_rns):
        """
        When address changes, identity mappings should migrate to new address.
        """
        old_mac = "54:8F:DF:44:79:2B"
        new_mac = "6B:2B:EE:1A:5B:94"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()

        # Setup: Peer connected on old address
        ble_interface.address_to_identity[old_mac] = identity
        ble_interface.identity_to_address[identity_hash] = old_mac
        ble_interface.peers[old_mac] = (Mock(), 0, 185)
        ble_interface.pending_mtu[old_mac] = 185

        # Add required methods
        ble_interface._compute_identity_hash = lambda x: x[:8].hex()
        ble_interface._get_fragmenter_key = lambda ident, addr: f"{ident[:8].hex()}_{addr}"

        # Add fragmenter for old address
        old_frag_key = f"{identity_hash}_{old_mac}"
        ble_interface.fragmenters[old_frag_key] = Mock()
        ble_interface.reassemblers[old_frag_key] = Mock()

        # Call address changed callback
        ble_interface._address_changed_callback(old_mac, new_mac, identity_hash)

        # Assert: Old address cleaned up
        assert old_mac not in ble_interface.address_to_identity
        assert old_mac not in ble_interface.peers
        assert old_mac not in ble_interface.pending_mtu
        assert old_frag_key not in ble_interface.fragmenters
        assert old_frag_key not in ble_interface.reassemblers

        # Assert: New address has mappings
        assert new_mac in ble_interface.address_to_identity
        assert ble_interface.address_to_identity[new_mac] == identity
        assert ble_interface.identity_to_address[identity_hash] == new_mac
        assert new_mac in ble_interface.peers
        assert new_mac in ble_interface.pending_mtu

        # Assert: Fragmenter migrated
        new_frag_key = f"{identity_hash}_{new_mac}"
        assert new_frag_key in ble_interface.fragmenters
        assert new_frag_key in ble_interface.reassemblers

    def test_address_changed_uses_cache_fallback(self, ble_interface, mock_rns):
        """
        When old address not in active mapping, check cache.
        """
        old_mac = "54:8F:DF:44:79:2B"
        new_mac = "6B:2B:EE:1A:5B:94"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()

        # Setup: Identity in cache, not active mapping
        ble_interface._identity_cache[old_mac] = (identity, time.time())

        # Add required methods
        ble_interface._compute_identity_hash = lambda x: x[:8].hex()
        ble_interface._get_fragmenter_key = lambda ident, addr: f"{ident[:8].hex()}_{addr}"

        # Call address changed callback
        ble_interface._address_changed_callback(old_mac, new_mac, identity_hash)

        # Assert: Cache was used and cleared
        assert old_mac not in ble_interface._identity_cache

        # Assert: New address has identity
        assert new_mac in ble_interface.address_to_identity
        assert ble_interface.address_to_identity[new_mac] == identity

    def test_address_changed_no_identity_logs_warning(self, ble_interface, mock_rns):
        """
        When no identity found for old address, log warning.
        """
        old_mac = "54:8F:DF:44:79:2B"
        new_mac = "6B:2B:EE:1A:5B:94"
        identity_hash = "456c6978"

        # Add required methods
        ble_interface._compute_identity_hash = lambda x: x[:8].hex()
        ble_interface._get_fragmenter_key = lambda ident, addr: f"{ident[:8].hex()}_{addr}"

        # No identity anywhere
        assert old_mac not in ble_interface.address_to_identity
        assert old_mac not in ble_interface._identity_cache

        # Patch RNS.log at module level to capture calls
        import RNS
        original_log = RNS.log
        log_calls = []
        RNS.log = lambda msg, level=4: log_calls.append((msg, level))

        try:
            # Call address changed callback - should not crash
            ble_interface._address_changed_callback(old_mac, new_mac, identity_hash)

            # Assert: Warning was logged about no identity
            assert len(log_calls) > 0, "Expected log calls"
            assert any("no identity found" in str(msg).lower() for msg, level in log_calls), \
                f"Expected 'no identity found' warning, got: {log_calls}"
        finally:
            RNS.log = original_log


class TestCacheTTL:
    """Test cache TTL (time-to-live) behavior."""

    def test_cache_ttl_default_60_seconds(self, ble_interface):
        """Cache TTL should default to 60 seconds."""
        assert ble_interface._identity_cache_ttl == 60

    def test_cache_valid_at_59_seconds(self, ble_interface, mock_rns):
        """Cache should be valid at 59 seconds."""
        mac = "54:8F:DF:44:79:2B"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()

        # Cache entry from 59 seconds ago
        ble_interface._identity_cache[mac] = (identity, time.time() - 59)

        # Add required methods
        ble_interface._compute_identity_hash = lambda x: x[:8].hex()
        ble_interface._get_fragmenter_key = lambda ident, addr: f"{ident[:8].hex()}_{addr}"

        # Create mock reassembler
        mock_reassembler = MagicMock()
        mock_reassembler.add_fragment = Mock(return_value=(False, None))
        frag_key = f"{identity_hash}_{mac}"
        ble_interface.reassemblers[frag_key] = mock_reassembler

        # Create mock peer interface
        ble_interface.spawned_interfaces[identity_hash] = Mock()

        # Call _handle_ble_data
        test_data = b"\x00\x01test"
        ble_interface._handle_ble_data(mac, test_data)

        # Assert: Identity was restored (cache was valid)
        assert mac in ble_interface.address_to_identity
        assert ble_interface.driver.request_identity_resync.call_count == 0

    def test_cache_expired_at_61_seconds(self, ble_interface, mock_rns):
        """Cache should be expired at 61 seconds."""
        mac = "54:8F:DF:44:79:2B"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")

        # Cache entry from 61 seconds ago
        ble_interface._identity_cache[mac] = (identity, time.time() - 61)

        # Add required methods
        ble_interface._compute_identity_hash = lambda x: x[:8].hex()

        # Call _handle_ble_data
        test_data = b"\x00\x01test"
        ble_interface._handle_ble_data(mac, test_data)

        # Assert: Identity was NOT restored, resync was requested
        assert mac not in ble_interface.address_to_identity
        ble_interface.driver.request_identity_resync.assert_called_once_with(mac)


class TestReassemblyCodePath:
    """Test identity cache in the reassembly code path."""

    def test_reassembly_restores_identity_from_cache(self, ble_interface, mock_rns):
        """
        The reassembly completion code should also check the cache.
        """
        mac = "54:8F:DF:44:79:2B"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()

        # Setup: Identity in cache
        ble_interface._identity_cache[mac] = (identity, time.time())

        # Add required methods
        ble_interface._compute_identity_hash = lambda x: x[:8].hex()
        ble_interface._get_fragmenter_key = lambda ident, addr: f"{ident[:8].hex()}_{addr}"

        # Create mock reassembler that returns complete packet
        mock_reassembler = MagicMock()
        mock_reassembler.add_fragment = Mock(return_value=(True, b"complete_packet"))
        frag_key = f"{identity_hash}_{mac}"
        ble_interface.reassemblers[frag_key] = mock_reassembler

        # Create mock peer interface
        mock_peer_if = Mock()
        mock_peer_if.process_incoming = Mock()
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if

        # Call _handle_ble_data with fragment
        test_data = b"\x00\x01test"
        ble_interface._handle_ble_data(mac, test_data)

        # Assert: Identity was restored
        assert mac in ble_interface.address_to_identity
        assert mac not in ble_interface._identity_cache


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_cache_handles_concurrent_access(self, ble_interface, mock_rns):
        """
        Cache operations should be thread-safe.
        """
        mac = "54:8F:DF:44:79:2B"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")

        # Add required methods
        ble_interface._compute_identity_hash = lambda x: x[:8].hex()

        results = []

        def cache_and_retrieve():
            # Cache identity
            ble_interface._identity_cache[mac] = (identity, time.time())
            time.sleep(0.001)
            # Retrieve from cache
            cached = ble_interface._identity_cache.get(mac)
            results.append(cached is not None)

        # Run multiple threads
        threads = [threading.Thread(target=cache_and_retrieve) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All operations should succeed
        assert all(results)

    def test_multiple_disconnects_same_address(self, ble_interface, mock_rns):
        """
        Multiple disconnects for same address should update cache timestamp.
        """
        mac = "54:8F:DF:44:79:2B"
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()

        # Add required methods
        ble_interface._compute_identity_hash = lambda x: x[:8].hex()

        # First disconnect
        ble_interface.address_to_identity[mac] = identity
        ble_interface.identity_to_address[identity_hash] = mac
        ble_interface.spawned_interfaces[identity_hash] = Mock(detach=Mock())

        ble_interface._device_disconnected_callback(mac)
        first_cache_time = ble_interface._identity_cache[mac][1]

        time.sleep(0.1)

        # Second disconnect (peer reconnected and disconnected again)
        ble_interface.address_to_identity[mac] = identity
        ble_interface.identity_to_address[identity_hash] = mac
        ble_interface.spawned_interfaces[identity_hash] = Mock(detach=Mock())

        ble_interface._device_disconnected_callback(mac)
        second_cache_time = ble_interface._identity_cache[mac][1]

        # Cache timestamp should be updated
        assert second_cache_time > first_cache_time


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
