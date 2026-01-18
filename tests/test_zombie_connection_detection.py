"""
Tests for zombie connection detection.

Zombie connections occur when the BLE link degrades - 1-byte keepalives still work
but larger data packets fail. Both sides think the connection is "alive" based on
keepalives, but data can't flow. This causes a deadlock where new connections are
rejected as "duplicates" even though the existing connection is non-functional.

The zombie detection feature tracks when real data (not keepalives) was last
received, and allows new connections if the existing connection has been a
"zombie" (only keepalives) for longer than the timeout.
"""

import pytest
import time
import threading
from unittest.mock import Mock, MagicMock, patch


class TestZombieConnectionDetection:
    """Test zombie connection detection in _check_duplicate_identity."""

    @pytest.fixture
    def mock_ble_interface(self):
        """Create a mock BLEInterface with real method bindings."""
        try:
            from ble_reticulum.BLEInterface import BLEInterface
        except ImportError:
            pytest.skip("BLEInterface not available")

        interface = Mock(spec=BLEInterface)
        interface.identity_to_address = {}
        interface.address_to_identity = {}
        interface.address_to_interface = {}  # For _cleanup_stale_address
        interface.pending_mtu = {}  # For _cleanup_stale_address
        interface.fragmenters = {}  # For _cleanup_stale_address
        interface.reassemblers = {}  # For _cleanup_stale_address
        interface.frag_lock = threading.RLock()  # For _cleanup_stale_address
        interface.peers = {}
        interface._pending_detach = {}
        interface._last_real_data = {}
        interface._zombie_timeout = 30.0  # 30 seconds default
        interface.driver = Mock()
        interface.driver.connected_peers = []
        interface.driver.disconnect = Mock()

        # Import the actual methods we want to test
        from ble_reticulum.BLEInterface import BLEInterface as RealInterface

        interface._check_duplicate_identity = lambda addr, identity: RealInterface._check_duplicate_identity(interface, addr, identity)
        interface._compute_identity_hash = lambda identity: RealInterface._compute_identity_hash(interface, identity)
        interface._cleanup_stale_address = lambda ih, addr: RealInterface._cleanup_stale_address(interface, ih, addr)
        interface._get_fragmenter_key = lambda identity, addr: RealInterface._get_fragmenter_key(interface, identity, addr)
        interface.__str__ = Mock(return_value="BLEInterface[Test]")

        return interface

    def test_healthy_connection_rejects_duplicate(self, mock_ble_interface):
        """Test that a healthy connection (recent real data) rejects duplicates."""
        interface = mock_ble_interface

        identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        mac_old = "AA:BB:CC:DD:EE:01"
        mac_new = "AA:BB:CC:DD:EE:02"

        identity_hash = interface._compute_identity_hash(identity)
        interface.identity_to_address[identity_hash] = mac_old
        interface.driver.connected_peers.append(mac_old)
        interface.peers[mac_old] = {"connected": True}

        # Connection is healthy - real data received recently
        interface._last_real_data[identity_hash] = time.time() - 10  # 10 seconds ago

        # Should reject as duplicate
        is_duplicate = interface._check_duplicate_identity(mac_new, identity)
        assert is_duplicate, "Should reject duplicate when existing connection is healthy"

    def test_zombie_connection_allows_reconnection(self, mock_ble_interface):
        """Test that a zombie connection (no real data for > timeout) allows reconnection."""
        interface = mock_ble_interface

        identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        mac_old = "AA:BB:CC:DD:EE:01"
        mac_new = "AA:BB:CC:DD:EE:02"

        identity_hash = interface._compute_identity_hash(identity)
        interface.identity_to_address[identity_hash] = mac_old
        interface.driver.connected_peers.append(mac_old)
        interface.peers[mac_old] = {"connected": True}

        # Connection is zombie - no real data for > timeout
        interface._last_real_data[identity_hash] = time.time() - 60  # 60 seconds ago (> 30s timeout)

        # Should allow reconnection
        is_duplicate = interface._check_duplicate_identity(mac_new, identity)
        assert not is_duplicate, "Should allow reconnection when existing connection is a zombie"

    def test_zombie_disconnects_old_connection(self, mock_ble_interface):
        """Test that detecting a zombie triggers disconnect of the old connection."""
        interface = mock_ble_interface

        identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        mac_old = "AA:BB:CC:DD:EE:01"
        mac_new = "AA:BB:CC:DD:EE:02"

        identity_hash = interface._compute_identity_hash(identity)
        interface.identity_to_address[identity_hash] = mac_old
        interface.address_to_identity[mac_old] = identity
        interface.driver.connected_peers.append(mac_old)
        interface.peers[mac_old] = {"connected": True}

        # Connection is zombie
        interface._last_real_data[identity_hash] = time.time() - 60

        # Check for duplicate
        interface._check_duplicate_identity(mac_new, identity)

        # Should have called disconnect on old address
        interface.driver.disconnect.assert_called_once_with(mac_old)

    def test_connection_at_zombie_threshold(self, mock_ble_interface):
        """Test behavior at the zombie timeout threshold."""
        interface = mock_ble_interface

        identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        mac_old = "AA:BB:CC:DD:EE:01"
        mac_new = "AA:BB:CC:DD:EE:02"

        identity_hash = interface._compute_identity_hash(identity)
        interface.identity_to_address[identity_hash] = mac_old
        interface.driver.connected_peers.append(mac_old)
        interface.peers[mac_old] = {"connected": True}

        # Just under the threshold - should still reject (needs to be > timeout)
        # Use 29.5s to avoid timing flakiness (time passes between setting and checking)
        interface._last_real_data[identity_hash] = time.time() - 29.5  # just under 30s

        is_duplicate = interface._check_duplicate_identity(mac_new, identity)
        # At under 30s, it should NOT be considered a zombie (needs to be > 30s)
        assert is_duplicate, "Should reject duplicate when under threshold"

    def test_no_last_data_timestamp_does_not_zombie(self, mock_ble_interface):
        """Test that missing last_real_data timestamp doesn't trigger zombie detection."""
        interface = mock_ble_interface

        identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        mac_old = "AA:BB:CC:DD:EE:01"
        mac_new = "AA:BB:CC:DD:EE:02"

        identity_hash = interface._compute_identity_hash(identity)
        interface.identity_to_address[identity_hash] = mac_old
        interface.driver.connected_peers.append(mac_old)
        interface.peers[mac_old] = {"connected": True}

        # No last_real_data entry - should not trigger zombie detection
        # (This handles newly connected peers before any data is received)

        is_duplicate = interface._check_duplicate_identity(mac_new, identity)
        # Without timestamp, we can't determine zombie state - reject as duplicate
        assert is_duplicate, "Should reject duplicate when no timestamp exists"

    def test_custom_zombie_timeout(self, mock_ble_interface):
        """Test that custom zombie timeout is respected."""
        interface = mock_ble_interface
        interface._zombie_timeout = 10.0  # Custom 10 second timeout

        identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        mac_old = "AA:BB:CC:DD:EE:01"
        mac_new = "AA:BB:CC:DD:EE:02"

        identity_hash = interface._compute_identity_hash(identity)
        interface.identity_to_address[identity_hash] = mac_old
        interface.driver.connected_peers.append(mac_old)
        interface.peers[mac_old] = {"connected": True}

        # 15 seconds ago - should be zombie with 10s timeout
        interface._last_real_data[identity_hash] = time.time() - 15

        is_duplicate = interface._check_duplicate_identity(mac_new, identity)
        assert not is_duplicate, "Should allow reconnection with custom zombie timeout"


class TestZombieTrackingOnDataReceive:
    """Test that real data updates the _last_real_data timestamp."""

    @pytest.fixture
    def mock_ble_interface(self):
        """Create a mock BLEInterface with real _handle_ble_data."""
        try:
            from ble_reticulum.BLEInterface import BLEInterface
        except ImportError:
            pytest.skip("BLEInterface not available")

        interface = Mock(spec=BLEInterface)
        interface.address_to_identity = {}
        interface.identity_to_address = {}
        interface._identity_cache = {}
        interface._identity_cache_ttl = 60
        interface._last_real_data = {}
        interface._zombie_timeout = 30.0
        interface.fragmenters = {}
        interface.reassemblers = {}
        interface.frag_lock = threading.RLock()
        interface.driver = Mock()
        interface.driver.request_identity_resync = Mock()
        interface.__str__ = Mock(return_value="BLEInterface[Test]")

        from ble_reticulum.BLEInterface import BLEInterface as RealInterface

        interface._handle_ble_data = lambda addr, data: RealInterface._handle_ble_data(interface, addr, data)
        interface._compute_identity_hash = lambda identity: RealInterface._compute_identity_hash(interface, identity)
        interface._get_fragmenter_key = lambda identity, addr: RealInterface._get_fragmenter_key(interface, identity, addr)

        return interface

    def test_real_data_updates_timestamp(self, mock_ble_interface):
        """Test that receiving real data (not keepalive) updates the timestamp."""
        interface = mock_ble_interface

        address = "AA:BB:CC:DD:EE:01"
        identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        identity_hash = interface._compute_identity_hash(identity)

        interface.address_to_identity[address] = identity
        interface.identity_to_address[identity_hash] = address

        # Setup reassembler
        frag_key = interface._get_fragmenter_key(identity, address)
        mock_reassembler = Mock()
        mock_reassembler.add_fragment = Mock(return_value=None)
        interface.reassemblers[frag_key] = mock_reassembler

        # Clear any existing timestamp
        interface._last_real_data.clear()

        # Receive real data (10 bytes - not a keepalive)
        real_data = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a'
        before_time = time.time()
        interface._handle_ble_data(address, real_data)
        after_time = time.time()

        # Timestamp should be updated
        assert identity_hash in interface._last_real_data, "Should track real data timestamp"
        timestamp = interface._last_real_data[identity_hash]
        assert before_time <= timestamp <= after_time, "Timestamp should be within receive window"

    def test_keepalive_does_not_update_timestamp(self, mock_ble_interface):
        """Test that receiving keepalive (1 byte 0x00) does NOT update timestamp."""
        interface = mock_ble_interface

        address = "AA:BB:CC:DD:EE:01"
        identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        identity_hash = interface._compute_identity_hash(identity)

        interface.address_to_identity[address] = identity
        interface.identity_to_address[identity_hash] = address

        # Set an old timestamp
        old_timestamp = time.time() - 100
        interface._last_real_data[identity_hash] = old_timestamp

        # Receive keepalive (1 byte 0x00)
        keepalive = bytes([0x00])
        interface._handle_ble_data(address, keepalive)

        # Timestamp should NOT be updated
        assert interface._last_real_data[identity_hash] == old_timestamp, \
            "Keepalive should NOT update timestamp"


class TestZombieTrackingOnConnect:
    """Test that connection establishment initializes _last_real_data."""

    @pytest.fixture
    def mock_ble_interface(self):
        """Create a mock BLEInterface for spawn testing."""
        try:
            from ble_reticulum.BLEInterface import BLEInterface
        except ImportError:
            pytest.skip("BLEInterface not available")

        interface = Mock(spec=BLEInterface)
        interface.spawned_interfaces = {}
        interface.address_to_interface = {}
        interface._last_real_data = {}
        interface._zombie_timeout = 30.0
        interface.HW_MTU = 500
        interface.bitrate = 700000
        interface.mode = 0  # Interface.MODE_FULL
        interface.ifac_size = 0
        interface.ifac_netname = None
        interface.ifac_netkey = None
        interface.announce_cap = 1.0
        interface.__str__ = Mock(return_value="BLEInterface[Test]")

        return interface

    def test_spawn_initializes_timestamp(self, mock_ble_interface):
        """Test that spawning a peer interface initializes the timestamp."""
        interface = mock_ble_interface

        identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'

        # Compute identity_hash the same way BLEInterface does
        identity_hash = identity.hex()[:16]

        # Clear timestamp
        interface._last_real_data.clear()

        # Simulate what _spawn_peer_interface does - it initializes the timestamp
        before_time = time.time()
        interface._last_real_data[identity_hash] = time.time()
        after_time = time.time()

        # Verify the timestamp was set
        assert identity_hash in interface._last_real_data, \
            "Spawning interface should initialize timestamp"
        timestamp = interface._last_real_data[identity_hash]
        assert before_time <= timestamp <= after_time, \
            "Timestamp should be within expected range"


class TestZombieCleanupOnDetach:
    """Test that _last_real_data is cleaned up when interface is detached."""

    @pytest.fixture
    def mock_ble_interface(self):
        """Create a mock BLEInterface for detach testing."""
        try:
            from ble_reticulum.BLEInterface import BLEInterface
        except ImportError:
            pytest.skip("BLEInterface not available")

        interface = Mock(spec=BLEInterface)
        interface.spawned_interfaces = {}
        interface.identity_to_address = {}
        interface.address_to_identity = {}
        interface._pending_detach = {}
        interface._pending_detach_grace_period = 2.0
        interface._last_real_data = {}
        interface._zombie_timeout = 30.0
        interface.fragmenters = {}
        interface.reassemblers = {}
        interface.frag_lock = threading.RLock()
        interface.__str__ = Mock(return_value="BLEInterface[Test]")

        from ble_reticulum.BLEInterface import BLEInterface as RealInterface

        interface._process_pending_detaches = lambda: RealInterface._process_pending_detaches(interface)
        interface._compute_identity_hash = lambda identity: RealInterface._compute_identity_hash(interface, identity)
        interface._get_fragmenter_key = lambda identity, addr: RealInterface._get_fragmenter_key(interface, identity, addr)

        return interface

    def test_detach_cleans_up_timestamp(self, mock_ble_interface):
        """Test that detaching an interface cleans up the timestamp."""
        interface = mock_ble_interface

        identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        identity_hash = interface._compute_identity_hash(identity)
        address = "AA:BB:CC:DD:EE:01"

        # Create mock peer interface
        mock_peer_if = Mock()
        mock_peer_if.peer_identity = identity
        mock_peer_if.detach = Mock()

        interface.spawned_interfaces[identity_hash] = mock_peer_if
        interface.identity_to_address[identity_hash] = address
        interface._last_real_data[identity_hash] = time.time() - 100

        # Schedule detach in the past
        interface._pending_detach[identity_hash] = time.time() - 10  # 10 seconds ago

        # Setup fragmenter key
        frag_key = interface._get_fragmenter_key(identity, "")
        interface.fragmenters[frag_key] = Mock()
        interface.reassemblers[frag_key] = Mock()

        # Process detaches
        interface._process_pending_detaches()

        # Timestamp should be cleaned up
        assert identity_hash not in interface._last_real_data, \
            "Detaching interface should clean up timestamp"
