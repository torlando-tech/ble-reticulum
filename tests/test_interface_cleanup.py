"""
Tests for BLEInterface cleanup methods.

These tests cover the new cleanup functionality added to handle:
1. Pending identity connection timeouts (non-Reticulum devices)
2. Delayed interface detachment with grace period (MAC rotation)
3. Orphaned interface validation (race condition protection)
4. Multi-address identity handling (same identity at multiple addresses)

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
        rns.Transport = Mock()
        rns.Transport.interfaces = []
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
    driver.disconnect = Mock()
    driver.connected_peers = []
    return driver


@pytest.fixture
def ble_interface(mock_rns, mock_driver):
    """
    Create a BLEInterface instance with mocked dependencies.

    This uses the REAL BLEInterface class but mocks external dependencies
    to allow testing the cleanup logic.
    """
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
        interface.address_to_interface = {}

        # Identity cache
        interface._identity_cache = {}
        interface._identity_cache_ttl = 60

        # Pending identity connections
        interface._pending_identity_connections = {}
        interface._pending_identity_timeout = 30

        # Pending detachments
        interface._pending_detach = {}
        interface._pending_detach_grace_period = 2.0

        # Zombie detection
        interface._last_real_data = {}
        interface._zombie_timeout = 30.0

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
        interface.OUT = True
        interface.IN = True

        # Add required methods
        interface._compute_identity_hash = lambda x: x[:8].hex()
        interface._get_fragmenter_key = lambda ident, addr: f"{ident[:8].hex()}"

        return interface

    except ImportError as e:
        pytest.skip(f"Cannot import BLEInterface (RNS not installed): {e}")


class TestCleanupPendingIdentityConnections:
    """Test _cleanup_pending_identity_connections() method."""

    def test_no_pending_connections(self, ble_interface, mock_rns):
        """Should handle empty pending connections gracefully."""
        assert len(ble_interface._pending_identity_connections) == 0
        ble_interface._cleanup_pending_identity_connections()
        # Should not crash, no disconnects called
        ble_interface.driver.disconnect.assert_not_called()

    def test_connection_not_timed_out(self, ble_interface, mock_rns):
        """Connections within timeout should not be disconnected."""
        mac = "AA:BB:CC:DD:EE:FF"
        # Connection started 5 seconds ago (within 30s timeout)
        ble_interface._pending_identity_connections[mac] = time.time() - 5

        ble_interface._cleanup_pending_identity_connections()

        # Should not disconnect
        ble_interface.driver.disconnect.assert_not_called()
        # Should still be tracked
        assert mac in ble_interface._pending_identity_connections

    def test_connection_timed_out(self, ble_interface, mock_rns):
        """Connections past timeout should be disconnected."""
        mac = "AA:BB:CC:DD:EE:FF"
        # Connection started 35 seconds ago (past 30s timeout)
        ble_interface._pending_identity_connections[mac] = time.time() - 35

        ble_interface._cleanup_pending_identity_connections()

        # Should disconnect
        ble_interface.driver.disconnect.assert_called_once_with(mac)
        # Should be removed from tracking
        assert mac not in ble_interface._pending_identity_connections

    def test_multiple_connections_mixed_states(self, ble_interface, mock_rns):
        """Should handle mix of timed-out and valid connections."""
        mac_expired1 = "AA:BB:CC:DD:EE:01"
        mac_valid = "AA:BB:CC:DD:EE:02"
        mac_expired2 = "AA:BB:CC:DD:EE:03"

        ble_interface._pending_identity_connections[mac_expired1] = time.time() - 40
        ble_interface._pending_identity_connections[mac_valid] = time.time() - 5
        ble_interface._pending_identity_connections[mac_expired2] = time.time() - 35

        ble_interface._cleanup_pending_identity_connections()

        # Should disconnect both expired connections
        assert ble_interface.driver.disconnect.call_count == 2
        # Valid connection should remain
        assert mac_valid in ble_interface._pending_identity_connections
        assert mac_expired1 not in ble_interface._pending_identity_connections
        assert mac_expired2 not in ble_interface._pending_identity_connections

    def test_disconnect_error_handled(self, ble_interface, mock_rns):
        """Errors during disconnect should be caught and logged."""
        mac = "AA:BB:CC:DD:EE:FF"
        ble_interface._pending_identity_connections[mac] = time.time() - 35
        ble_interface.driver.disconnect.side_effect = Exception("BLE disconnect failed")

        # Should not raise
        ble_interface._cleanup_pending_identity_connections()

        # Connection should still be removed from tracking
        assert mac not in ble_interface._pending_identity_connections


class TestProcessPendingDetaches:
    """Test _process_pending_detaches() method."""

    def test_no_pending_detaches(self, ble_interface, mock_rns):
        """Should handle empty pending detaches gracefully."""
        assert len(ble_interface._pending_detach) == 0
        ble_interface._process_pending_detaches()
        # Should not crash

    def test_detach_within_grace_period(self, ble_interface, mock_rns):
        """Detaches within grace period should not execute."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()

        # Scheduled 0.5 seconds ago (within 2s grace period)
        ble_interface._pending_detach[identity_hash] = time.time() - 0.5

        # Create mock interface
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if

        ble_interface._process_pending_detaches()

        # Should not detach yet
        mock_peer_if.detach.assert_not_called()
        assert identity_hash in ble_interface._pending_detach
        assert identity_hash in ble_interface.spawned_interfaces

    def test_detach_after_grace_period_no_reconnect(self, ble_interface, mock_rns):
        """Detaches past grace period should execute if no reconnection."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()

        # Scheduled 3 seconds ago (past 2s grace period)
        ble_interface._pending_detach[identity_hash] = time.time() - 3
        ble_interface.identity_to_address[identity_hash] = "AA:BB:CC:DD:EE:FF"

        # Create mock interface
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if

        ble_interface._process_pending_detaches()

        # Should detach
        mock_peer_if.detach.assert_called_once()
        assert identity_hash not in ble_interface._pending_detach
        assert identity_hash not in ble_interface.spawned_interfaces
        assert identity_hash not in ble_interface.identity_to_address

    def test_detach_cancelled_if_reconnected(self, ble_interface, mock_rns):
        """Detach should be cancelled if address reconnected during grace period."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()
        mac = "AA:BB:CC:DD:EE:FF"

        # Scheduled 3 seconds ago (past 2s grace period)
        ble_interface._pending_detach[identity_hash] = time.time() - 3

        # But address reconnected (identity mapping exists)
        ble_interface.address_to_identity[mac] = identity

        # Create mock interface
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if

        ble_interface._process_pending_detaches()

        # Should NOT detach (reconnected during grace period)
        mock_peer_if.detach.assert_not_called()
        assert identity_hash not in ble_interface._pending_detach  # Pending cleared
        assert identity_hash in ble_interface.spawned_interfaces  # Interface kept

    def test_detach_cleans_up_fragmenter(self, ble_interface, mock_rns):
        """Detach should clean up fragmenter and reassembler."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()
        frag_key = identity_hash

        # Scheduled 3 seconds ago (past 2s grace period)
        ble_interface._pending_detach[identity_hash] = time.time() - 3
        ble_interface.identity_to_address[identity_hash] = "AA:BB:CC:DD:EE:FF"

        # Create mock interface
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if

        # Create mock fragmenter/reassembler
        ble_interface.fragmenters[frag_key] = Mock()
        ble_interface.reassemblers[frag_key] = Mock()

        ble_interface._process_pending_detaches()

        # Fragmenter/reassembler should be cleaned up
        assert frag_key not in ble_interface.fragmenters
        assert frag_key not in ble_interface.reassemblers

    def test_detach_interface_already_gone(self, ble_interface, mock_rns):
        """Should handle case where interface was already removed."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()

        # Scheduled 3 seconds ago (past 2s grace period)
        ble_interface._pending_detach[identity_hash] = time.time() - 3

        # No interface exists (already cleaned up elsewhere)
        assert identity_hash not in ble_interface.spawned_interfaces

        # Should not crash
        ble_interface._process_pending_detaches()

        # Pending entry should be cleared
        assert identity_hash not in ble_interface._pending_detach


class TestValidateSpawnedInterfaces:
    """Test _validate_spawned_interfaces() method."""

    def test_no_orphaned_interfaces(self, ble_interface, mock_rns):
        """Should handle case with no orphaned interfaces."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()
        mac = "AA:BB:CC:DD:EE:FF"

        # Interface with connected address
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if
        ble_interface.address_to_interface[mac] = mock_peer_if
        ble_interface.address_to_identity[mac] = identity
        ble_interface.identity_to_address[identity_hash] = mac

        # Driver shows address connected
        ble_interface.driver.connected_peers = [mac]

        ble_interface._validate_spawned_interfaces()

        # Nothing should be detached
        mock_peer_if.detach.assert_not_called()
        assert identity_hash in ble_interface.spawned_interfaces
        assert mac in ble_interface.address_to_interface

    def test_orphaned_address_mapping_cleaned(self, ble_interface, mock_rns):
        """Should clean up address mappings for disconnected addresses."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()
        mac_disconnected = "AA:BB:CC:DD:EE:01"
        mac_connected = "AA:BB:CC:DD:EE:02"

        # Interface with two address mappings
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if
        ble_interface.address_to_interface[mac_disconnected] = mock_peer_if
        ble_interface.address_to_interface[mac_connected] = mock_peer_if
        ble_interface.address_to_identity[mac_disconnected] = identity
        ble_interface.address_to_identity[mac_connected] = identity
        ble_interface.identity_to_address[identity_hash] = mac_disconnected

        # Only one address still connected
        ble_interface.driver.connected_peers = [mac_connected]

        ble_interface._validate_spawned_interfaces()

        # Disconnected address should be cleaned up
        assert mac_disconnected not in ble_interface.address_to_interface
        assert mac_disconnected not in ble_interface.address_to_identity

        # Connected address should remain
        assert mac_connected in ble_interface.address_to_interface

        # Interface should NOT be detached (other address still connected)
        mock_peer_if.detach.assert_not_called()
        assert identity_hash in ble_interface.spawned_interfaces

        # identity_to_address should point to connected address
        assert ble_interface.identity_to_address[identity_hash] == mac_connected

    def test_orphaned_interface_detached(self, ble_interface, mock_rns):
        """Should detach interface when all addresses disconnected."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()
        mac = "AA:BB:CC:DD:EE:FF"
        frag_key = identity_hash

        # Interface with no connected addresses
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if
        ble_interface.address_to_interface[mac] = mock_peer_if
        ble_interface.address_to_identity[mac] = identity
        ble_interface.identity_to_address[identity_hash] = mac

        # Create fragmenter/reassembler
        ble_interface.fragmenters[frag_key] = Mock()
        ble_interface.reassemblers[frag_key] = Mock()

        # No addresses connected
        ble_interface.driver.connected_peers = []

        ble_interface._validate_spawned_interfaces()

        # Interface should be detached
        mock_peer_if.detach.assert_called_once()
        assert identity_hash not in ble_interface.spawned_interfaces
        assert identity_hash not in ble_interface.identity_to_address
        assert mac not in ble_interface.address_to_interface
        assert mac not in ble_interface.address_to_identity

        # Fragmenter/reassembler should be cleaned up
        assert frag_key not in ble_interface.fragmenters
        assert frag_key not in ble_interface.reassemblers

    def test_validation_handles_exception(self, ble_interface, mock_rns):
        """Should catch and log exceptions during validation."""
        # Make connected_peers property raise an exception
        type(ble_interface.driver).connected_peers = PropertyMock(
            side_effect=Exception("BLE driver error")
        )

        # Should not raise
        ble_interface._validate_spawned_interfaces()


class TestMultiAddressDisconnect:
    """Test disconnect callback with multiple addresses per identity."""

    def test_disconnect_preserves_interface_with_other_addresses(self, ble_interface, mock_rns):
        """When one address disconnects, interface should stay if others remain."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()
        mac1 = "AA:BB:CC:DD:EE:01"
        mac2 = "AA:BB:CC:DD:EE:02"

        # Setup: Two addresses mapped to same identity/interface
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        mock_peer_if.detach = Mock()
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if
        ble_interface.address_to_interface[mac1] = mock_peer_if
        ble_interface.address_to_interface[mac2] = mock_peer_if
        ble_interface.address_to_identity[mac1] = identity
        ble_interface.address_to_identity[mac2] = identity
        ble_interface.identity_to_address[identity_hash] = mac1

        # Disconnect mac1
        ble_interface._device_disconnected_callback(mac1)

        # Interface should NOT be detached (mac2 still connected)
        mock_peer_if.detach.assert_not_called()
        assert identity_hash in ble_interface.spawned_interfaces

        # mac1 mappings should be cleaned
        assert mac1 not in ble_interface.address_to_interface
        assert mac1 not in ble_interface.address_to_identity

        # mac2 should remain
        assert mac2 in ble_interface.address_to_interface
        assert mac2 in ble_interface.address_to_identity

        # identity_to_address should point to mac2 now
        assert ble_interface.identity_to_address[identity_hash] == mac2

        # No pending detach (other address still connected)
        assert identity_hash not in ble_interface._pending_detach

    def test_disconnect_schedules_detach_when_last_address(self, ble_interface, mock_rns):
        """When last address disconnects, detach should be scheduled."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()
        mac = "AA:BB:CC:DD:EE:FF"

        # Setup: Single address mapped to identity
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        mock_peer_if.detach = Mock()
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if
        ble_interface.address_to_interface[mac] = mock_peer_if
        ble_interface.address_to_identity[mac] = identity
        ble_interface.identity_to_address[identity_hash] = mac

        # Disconnect
        ble_interface._device_disconnected_callback(mac)

        # Interface should NOT be immediately detached
        mock_peer_if.detach.assert_not_called()

        # But detach should be scheduled
        assert identity_hash in ble_interface._pending_detach

        # Address mappings should be cleaned immediately
        assert mac not in ble_interface.address_to_interface
        assert mac not in ble_interface.address_to_identity


class TestCentralDisconnected:
    """Test handle_central_disconnected with multi-address handling."""

    def test_central_disconnect_preserves_interface_with_other_addresses(self, ble_interface, mock_rns):
        """When central disconnects, interface should stay if other addresses exist."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()
        mac_central = "AA:BB:CC:DD:EE:01"
        mac_other = "AA:BB:CC:DD:EE:02"

        # Setup: Two addresses mapped to same identity
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        mock_peer_if.detach = Mock()
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if
        ble_interface.address_to_interface[mac_central] = mock_peer_if
        ble_interface.address_to_interface[mac_other] = mock_peer_if
        ble_interface.address_to_identity[mac_central] = identity
        ble_interface.address_to_identity[mac_other] = identity
        ble_interface.identity_to_address[identity_hash] = mac_central

        # Central disconnects
        ble_interface.handle_central_disconnected(mac_central)

        # Interface should NOT be detached
        mock_peer_if.detach.assert_not_called()
        assert identity_hash in ble_interface.spawned_interfaces

        # identity_to_address should point to remaining address
        assert ble_interface.identity_to_address[identity_hash] == mac_other

    def test_central_disconnect_uses_address_fallback(self, ble_interface, mock_rns):
        """When no identity mapping, should use address_to_interface fallback."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()
        mac = "AA:BB:CC:DD:EE:FF"

        # Setup: Only address_to_interface exists (no identity mapping)
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        mock_peer_if.detach = Mock()
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if
        ble_interface.address_to_interface[mac] = mock_peer_if
        ble_interface.identity_to_address[identity_hash] = mac
        # Note: address_to_identity is NOT set - testing fallback

        # Central disconnects
        ble_interface.handle_central_disconnected(mac)

        # Should still schedule detach via fallback
        assert identity_hash in ble_interface._pending_detach


class TestSpawnPeerInterfaceThreadSafety:
    """Test thread-safety of _spawn_peer_interface."""

    def test_spawn_interface_reuses_existing(self, ble_interface, mock_rns):
        """Should reuse existing interface for same identity at new address."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()
        mac_old = "AA:BB:CC:DD:EE:01"
        mac_new = "AA:BB:CC:DD:EE:02"

        # Create existing interface
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        mock_peer_if.online = True
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if
        ble_interface.address_to_interface[mac_old] = mock_peer_if

        # Spawn interface for new address with same identity
        result = ble_interface._spawn_peer_interface(
            mac_new, "RNS-peer", identity, client=None, mtu=185
        )

        # Should return existing interface
        assert result == mock_peer_if

        # New address should be mapped
        assert ble_interface.address_to_interface[mac_new] == mock_peer_if
        assert ble_interface.address_to_identity[mac_new] == identity
        assert ble_interface.identity_to_address[identity_hash] == mac_new

    def test_spawn_interface_cancels_pending_detach(self, ble_interface, mock_rns):
        """Spawning interface should cancel any pending detach."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()
        mac = "AA:BB:CC:DD:EE:FF"

        # Create existing interface with pending detach
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        mock_peer_if.online = False
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if
        ble_interface._pending_detach[identity_hash] = time.time() - 1

        # Spawn interface (reuse)
        result = ble_interface._spawn_peer_interface(
            mac, "RNS-peer", identity, client=None, mtu=185
        )

        # Pending detach should be cancelled
        assert identity_hash not in ble_interface._pending_detach

        # Interface should be marked online
        assert result.online is True


class TestDeviceConnectedCallback:
    """Test _device_connected_callback with pending identity handling."""

    def test_connected_cancels_pending_detach(self, ble_interface, mock_rns):
        """Connection with identity should cancel pending detach."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()
        mac = "AA:BB:CC:DD:EE:FF"

        # Setup: pending detach exists
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if
        ble_interface._pending_detach[identity_hash] = time.time() - 1

        # Mock driver role
        ble_interface.driver.get_peer_role = Mock(return_value="central")
        ble_interface._check_duplicate_identity = Mock(return_value=False)
        ble_interface._record_connection_success = Mock()
        ble_interface._spawn_peer_interface = Mock(return_value=mock_peer_if)
        ble_interface._ensure_fragmenter = Mock()

        # Device connects
        ble_interface._device_connected_callback(mac, identity)

        # Pending detach should be cancelled
        assert identity_hash not in ble_interface._pending_detach

    def test_connected_removes_from_pending_identity(self, ble_interface, mock_rns):
        """Connection with identity should remove from pending identity tracking."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()
        mac = "AA:BB:CC:DD:EE:FF"

        # Setup: pending identity exists
        ble_interface._pending_identity_connections[mac] = time.time() - 5

        # Mock driver role
        ble_interface.driver.get_peer_role = Mock(return_value="central")
        ble_interface._check_duplicate_identity = Mock(return_value=False)
        ble_interface._record_connection_success = Mock()
        ble_interface._spawn_peer_interface = Mock(return_value=Mock())
        ble_interface._ensure_fragmenter = Mock()

        # Device connects
        ble_interface._device_connected_callback(mac, identity)

        # Should be removed from pending tracking
        assert mac not in ble_interface._pending_identity_connections

    def test_peripheral_connection_tracked_for_timeout(self, ble_interface, mock_rns):
        """Peripheral connection without identity should be tracked for timeout."""
        mac = "AA:BB:CC:DD:EE:FF"

        # Mock driver role (peripheral = waiting for identity handshake)
        ble_interface.driver.get_peer_role = Mock(return_value="peripheral")

        # Device connects as peripheral (no identity yet)
        ble_interface._device_connected_callback(mac, None)

        # Should be tracked for identity timeout
        assert mac in ble_interface._pending_identity_connections


class TestCleanupStaleAddress:
    """Test _cleanup_stale_address (renamed from _cleanup_stale_interface)."""

    def test_cleanup_preserves_interface(self, ble_interface, mock_rns):
        """Cleanup should preserve interface for reuse."""
        identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
        identity_hash = identity[:8].hex()
        old_mac = "AA:BB:CC:DD:EE:01"

        # Setup: interface and mappings
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        mock_peer_if.detach = Mock()
        ble_interface.spawned_interfaces[identity_hash] = mock_peer_if
        ble_interface.address_to_interface[old_mac] = mock_peer_if
        ble_interface.address_to_identity[old_mac] = identity
        ble_interface.identity_to_address[identity_hash] = old_mac
        ble_interface.pending_mtu[old_mac] = 185

        # Cleanup old address
        ble_interface._cleanup_stale_address(identity_hash, old_mac)

        # Interface should NOT be detached (preserved for reuse)
        mock_peer_if.detach.assert_not_called()
        assert identity_hash in ble_interface.spawned_interfaces

        # Old address mappings should be cleaned
        assert old_mac not in ble_interface.address_to_interface
        assert old_mac not in ble_interface.address_to_identity
        assert old_mac not in ble_interface.pending_mtu


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
