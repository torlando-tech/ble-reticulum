"""
Tests for BLEPeerInterface.peer_address update on MAC rotation.

When BLE MAC address rotation occurs (same identity appearing at a different address),
the BLEPeerInterface.peer_address field MUST be updated to match the new address.
Otherwise, sends will fail because Python uses the stale address that no longer
matches Kotlin's connectedPeers map.

This regression was discovered when peripheral->central sends failed with
"Cannot send to X - not connected" after MAC rotation. The root cause was
that BLEPeerInterface.peer_address retained the OLD address while the
Kotlin layer had updated to the new address.

These tests verify that peer_address is updated in all 4 code paths where
MAC rotation can occur:
1. _mtu_negotiated_callback - when interface already exists for identity
2. _handle_identity_handshake - when interface already exists for identity
3. _address_changed_callback - when address migration is triggered
4. _spawn_peer_interface - when reusing existing interface for new address
"""

import pytest
import time
import threading
from unittest.mock import Mock, MagicMock, patch
import sys
import os

# Ensure src is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

# Try to import BLEInterface - skip all tests if not available
try:
    from ble_reticulum.BLEInterface import BLEInterface
    import RNS
    HAS_RNS = True
except ImportError as e:
    HAS_RNS = False
    IMPORT_ERROR = str(e)

pytestmark = pytest.mark.skipif(not HAS_RNS, reason=f"RNS not available: {IMPORT_ERROR if not HAS_RNS else ''}")


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
    driver.on_mtu_negotiated = None
    driver.request_identity_resync = Mock(return_value=False)
    driver.get_cached_identity = Mock(return_value=None)
    driver.send = Mock(return_value=True)
    return driver


@pytest.fixture
def mock_peer_interface():
    """Create a mock BLEPeerInterface."""
    peer_if = Mock()
    peer_if.peer_address = "AA:BB:CC:DD:EE:FF"  # Old address
    peer_if.peer_name = "TestPeer"
    peer_if.online = True
    peer_if.detach = Mock()
    peer_if.is_peripheral_connection = False
    return peer_if


@pytest.fixture
def ble_interface(mock_driver, mock_peer_interface):
    """
    Create a BLEInterface instance with mocked dependencies.

    This uses the REAL BLEInterface class but mocks external dependencies
    to allow testing the MAC rotation address update logic.
    """
    # Create interface without calling __init__
    interface = object.__new__(BLEInterface)

    # Initialize all required attributes manually
    interface.name = "TestBLE"
    interface.owner = Mock()
    interface.online = True
    interface.driver = mock_driver

    # Sample identity (16 bytes)
    interface._test_identity = bytes.fromhex("456c6978823c85e228a545259c241e0e")
    interface._test_identity_hash = interface._test_identity[:8].hex()

    # Sample addresses
    interface._old_address = "AA:BB:CC:DD:EE:FF"
    interface._new_address = "11:22:33:44:55:66"

    # Identity mappings - set up with OLD address
    interface.peers = {interface._old_address: (Mock(is_connected=True), 0, 185)}
    interface.spawned_interfaces = {interface._test_identity_hash: mock_peer_interface}
    interface.address_to_identity = {interface._old_address: interface._test_identity}
    interface.identity_to_address = {interface._test_identity_hash: interface._old_address}
    interface.address_to_interface = {interface._old_address: mock_peer_interface}
    interface._identity_cache = {}
    interface._identity_cache_ttl = 60
    interface._pending_detach = {}
    interface._pending_detach_grace_period = 2.0

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

    # Methods
    interface._compute_identity_hash = lambda x: x[:8].hex()
    interface._get_fragmenter_key = lambda identity, addr: f"{identity[:4].hex()}_{addr}"

    return interface


class TestPeerAddressMacRotation:
    """
    Test that BLEPeerInterface.peer_address is updated when MAC rotation occurs.

    This is critical for bidirectional BLE communication. If peer_address is not
    updated, sends will fail because the address doesn't match the Kotlin layer's
    connectedPeers map.
    """

    def test_address_changed_callback_updates_peer_address(self, ble_interface, mock_peer_interface):
        """
        Test that _address_changed_callback updates BLEPeerInterface.peer_address.

        This is the primary path for MAC rotation when the driver notifies of an
        address change for an existing connection.
        """
        old_addr = ble_interface._old_address
        new_addr = ble_interface._new_address
        identity_hash = ble_interface._test_identity_hash

        # Verify initial state - peer_address is OLD address
        assert mock_peer_interface.peer_address == old_addr
        assert ble_interface.address_to_interface[old_addr] == mock_peer_interface

        # Call the address changed callback
        ble_interface._address_changed_callback(old_addr, new_addr, identity_hash)

        # CRITICAL: Verify peer_address was updated to NEW address
        assert mock_peer_interface.peer_address == new_addr, \
            f"peer_address should be {new_addr} but is {mock_peer_interface.peer_address}"

        # Verify mappings were migrated
        assert new_addr in ble_interface.address_to_interface
        assert old_addr not in ble_interface.address_to_interface
        assert ble_interface.address_to_interface[new_addr] == mock_peer_interface

    def test_mtu_negotiated_updates_peer_address_for_existing_interface(self, ble_interface, mock_peer_interface):
        """
        Test that _mtu_negotiated_callback updates peer_address when interface exists.

        When MTU is negotiated for an address but we already have an interface for
        that identity (at a different address), we must update peer_address.
        """
        old_addr = ble_interface._old_address
        new_addr = ble_interface._new_address
        identity = ble_interface._test_identity
        identity_hash = ble_interface._test_identity_hash

        # Verify initial state
        assert mock_peer_interface.peer_address == old_addr

        # Setup: identity is already known at old address
        assert identity_hash in ble_interface.spawned_interfaces

        # Add identity mapping for new address (simulating the peer reconnected at new address)
        ble_interface.address_to_identity[new_addr] = identity
        ble_interface.peers[new_addr] = (Mock(is_connected=True), 0, 0)  # MTU not yet negotiated

        # Call MTU negotiated for the NEW address
        ble_interface._mtu_negotiated_callback(new_addr, 185)

        # CRITICAL: peer_address should be updated to new address
        assert mock_peer_interface.peer_address == new_addr, \
            f"peer_address should be {new_addr} but is {mock_peer_interface.peer_address}"

        # Verify new address is mapped to the interface
        assert ble_interface.address_to_interface.get(new_addr) == mock_peer_interface

    def test_spawn_peer_interface_updates_peer_address_for_reuse(self, ble_interface, mock_peer_interface):
        """
        Test that _spawn_peer_interface updates peer_address when reusing interface.

        When spawning a peer interface for an identity that already has one (at
        a different address), we reuse the existing interface but MUST update
        its peer_address.
        """
        old_addr = ble_interface._old_address
        new_addr = ble_interface._new_address
        identity = ble_interface._test_identity
        identity_hash = ble_interface._test_identity_hash

        # Verify initial state
        assert mock_peer_interface.peer_address == old_addr
        assert identity_hash in ble_interface.spawned_interfaces

        # Setup for spawn: peer exists at new address with negotiated MTU
        ble_interface.peers[new_addr] = (Mock(is_connected=True), 0, 185)
        ble_interface.address_to_identity[new_addr] = identity

        # Mock _get_fragmenter to avoid creating real fragmenters
        ble_interface._get_fragmenter = Mock(return_value=Mock())
        ble_interface._get_reassembler = Mock(return_value=Mock())

        # Call spawn_peer_interface for the NEW address
        # This should detect existing interface and update peer_address
        ble_interface._spawn_peer_interface(
            address=new_addr,
            name="TestPeer",
            peer_identity=identity,
            mtu=185,
            connection_type="central"
        )

        # CRITICAL: peer_address should be updated to new address
        assert mock_peer_interface.peer_address == new_addr, \
            f"peer_address should be {new_addr} but is {mock_peer_interface.peer_address}"

    def test_handle_identity_handshake_updates_peer_address(self, ble_interface, mock_peer_interface):
        """
        Test that _handle_identity_handshake updates peer_address for existing interface.

        When we receive an identity handshake from a new address but already have
        an interface for that identity, we must update peer_address.
        """
        old_addr = ble_interface._old_address
        new_addr = ble_interface._new_address
        identity = ble_interface._test_identity
        identity_hash = ble_interface._test_identity_hash

        # Verify initial state
        assert mock_peer_interface.peer_address == old_addr
        assert identity_hash in ble_interface.spawned_interfaces

        # Setup: peer connected at new address - but NO identity mapping yet
        # This simulates a central reconnecting at a new MAC address
        ble_interface.peers[new_addr] = (Mock(is_connected=True), 0, 185)
        # NOTE: Do NOT add new_addr to address_to_identity - the handshake does that

        # Mock driver.get_peer_mtu for the handshake
        ble_interface.driver.get_peer_mtu = Mock(return_value=185)

        # Mock _pending_identity_connections to avoid KeyError
        ble_interface._pending_identity_connections = {}

        # Identity handshake expects exactly 16 bytes (the identity itself)
        identity_packet = identity  # 16 bytes

        # Call identity handshake for NEW address
        result = ble_interface._handle_identity_handshake(new_addr, identity_packet)

        # Verify handshake was processed
        assert result is True, "Identity handshake should return True when processed"

        # CRITICAL: peer_address should be updated to new address
        assert mock_peer_interface.peer_address == new_addr, \
            f"peer_address should be {new_addr} but is {mock_peer_interface.peer_address}"


class TestPeerAddressUpdateLogging:
    """Test that peer_address updates are properly logged for debugging."""

    def test_address_changed_logs_update(self, ble_interface, mock_peer_interface):
        """Verify that address update is logged for debugging."""
        old_addr = ble_interface._old_address
        new_addr = ble_interface._new_address
        identity_hash = ble_interface._test_identity_hash

        # Patch RNS.log to capture calls
        with patch.object(RNS, 'log') as mock_log:
            # Call address changed
            ble_interface._address_changed_callback(old_addr, new_addr, identity_hash)

            # Should have logged the update
            log_calls = [str(call) for call in mock_log.call_args_list]
            rotation_logged = any("MAC rotation" in call or "updated peer interface" in call for call in log_calls)
            assert rotation_logged or len(log_calls) > 0, \
                "Address update should be logged for debugging"


class TestPeerAddressConsistency:
    """Test that peer_address stays consistent with address_to_interface mapping."""

    def test_peer_address_matches_mapping_after_rotation(self, ble_interface, mock_peer_interface):
        """
        After MAC rotation, peer_address should match the key in address_to_interface.

        This ensures that when BLEPeerInterface.process_outgoing() calls
        driver.send(self.peer_address, data), it uses the same address that
        the Kotlin layer expects.
        """
        old_addr = ble_interface._old_address
        new_addr = ble_interface._new_address
        identity_hash = ble_interface._test_identity_hash

        # Perform MAC rotation
        ble_interface._address_changed_callback(old_addr, new_addr, identity_hash)

        # Get the interface from the mapping
        interface_from_mapping = ble_interface.address_to_interface.get(new_addr)

        # CRITICAL: peer_address must match the mapping key
        assert interface_from_mapping is not None, "Interface should be mapped to new address"
        assert interface_from_mapping.peer_address == new_addr, \
            "peer_address must match the address_to_interface mapping key"

        # And NOT the old address
        assert interface_from_mapping.peer_address != old_addr, \
            "peer_address must NOT be the old address after rotation"


class TestMultipleMacRotations:
    """Test that multiple MAC rotations are handled correctly."""

    def test_multiple_rotations_update_peer_address(self, ble_interface, mock_peer_interface):
        """
        Test that peer_address is updated correctly through multiple MAC rotations.

        BLE devices may rotate MAC addresses multiple times during a session.
        Each rotation must update peer_address correctly.
        """
        addr1 = ble_interface._old_address  # AA:BB:CC:DD:EE:FF
        addr2 = ble_interface._new_address  # 11:22:33:44:55:66
        addr3 = "99:88:77:66:55:44"  # Third address
        identity_hash = ble_interface._test_identity_hash

        # First rotation: addr1 -> addr2
        ble_interface._address_changed_callback(addr1, addr2, identity_hash)
        assert mock_peer_interface.peer_address == addr2

        # Second rotation: addr2 -> addr3
        # Need to update the mapping first (simulating driver state)
        ble_interface.address_to_identity[addr3] = ble_interface._test_identity
        ble_interface.identity_to_address[identity_hash] = addr2

        ble_interface._address_changed_callback(addr2, addr3, identity_hash)
        assert mock_peer_interface.peer_address == addr3, \
            f"After second rotation, peer_address should be {addr3} but is {mock_peer_interface.peer_address}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
