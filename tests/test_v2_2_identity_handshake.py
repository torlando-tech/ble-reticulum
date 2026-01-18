"""
Tests for BLE Protocol v2.2 Identity Handshake

The identity handshake is a core v2.2 feature that enables peripheral-side
peer discovery. When a central connects to a peripheral:

1. Central reads peer's identity from Identity characteristic
2. Central writes its own identity (16 bytes) to RX characteristic
3. Peripheral detects handshake (len==16 && no prior identity)
4. Peripheral stores identity mappings
5. Peripheral spawns peer interface

This enables peripheral devices to discover and route to peers that connect
to their GATT server, solving the asymmetric discovery problem in BLE.

Reference: BLE_PROTOCOL_v2.2.md §6 Identity Handshake Protocol
"""

import pytest
import sys
import os

# Add src to path for imports
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
    RNS.Identity.full_hash = lambda x: (x * 2)[:16]  # Simple mock

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
from ble_reticulum.BLEFragmentation import BLEFragmenter, BLEReassembler
import time


class MockOwner:
    """Mock Reticulum owner for testing."""
    def __init__(self):
        self.inbound_calls = []

    def inbound(self, data, interface):
        """Track inbound data calls."""
        self.inbound_calls.append((data, interface))


class TestIdentityHandshakeBasics:
    """Test basic identity handshake detection and handling."""

    def test_peripheral_detects_16_byte_handshake(self):
        """Test that peripheral correctly detects 16-byte handshake packet."""
        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {
            "name": "TestInterface",
            "enable_central": False,
            "enable_peripheral": True,
        }

        interface = BLEInterface(owner, config)
        interface.driver = driver

        # Set driver callbacks
        driver.on_device_connected = interface._device_connected_callback
        driver.on_data_received = interface._data_received_callback

        # Simulate central connection (peripheral role)
        central_address = "11:22:33:44:55:66"
        driver._accept_connection(central_address)  # Peripheral accepts connection

        # Verify no identity yet
        assert central_address not in interface.address_to_identity

        # Simulate 16-byte identity handshake from central
        central_identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        interface.handle_peripheral_data(central_identity, central_address)

        # Verify identity was stored
        assert central_address in interface.address_to_identity
        assert interface.address_to_identity[central_address] == central_identity

        # Verify bidirectional mapping created
        identity_hash = interface._compute_identity_hash(central_identity)
        assert identity_hash in interface.identity_to_address
        assert interface.identity_to_address[identity_hash] == central_address

    def test_handshake_not_confused_with_data(self):
        """Test that 16-byte data packets are not mistaken for handshakes."""
        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_peripheral": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver

        central_address = "11:22:33:44:55:66"

        # Set up existing identity (handshake already occurred)
        existing_identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        interface.address_to_identity[central_address] = existing_identity

        # Create fragmenter and peer interface (simulating post-handshake state)
        frag_key = interface._get_fragmenter_key(existing_identity, central_address)
        interface.fragmenters[frag_key] = BLEFragmenter(mtu=185)
        interface.reassemblers[frag_key] = BLEReassembler()

        # Receive 16-byte data packet (should be processed as data, not handshake)
        data_packet = b'\xaa\xbb\xcc\xdd\xee\xff\x11\x22\x33\x44\x55\x66\x77\x88\x99\x00'
        interface.handle_peripheral_data(data_packet, central_address)

        # Verify identity unchanged (not overwritten)
        assert interface.address_to_identity[central_address] == existing_identity

    def test_handshake_creates_peer_interface(self):
        """Test that handshake triggers peer interface creation."""
        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_peripheral": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver

        central_address = "11:22:33:44:55:66"
        central_identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'

        # Simulate connection
        driver._accept_connection(central_address)

        # Send handshake
        interface.handle_peripheral_data(central_identity, central_address)

        # Verify peer interface was created
        identity_hash = interface._compute_identity_hash(central_identity)
        assert identity_hash in interface.spawned_interfaces

        peer_interface = interface.spawned_interfaces[identity_hash]
        assert peer_interface.peer_address == central_address
        assert peer_interface.peer_identity == central_identity


class TestIdentityHandshakeEdgeCases:
    """Test edge cases and error handling in identity handshake."""

    def test_handshake_wrong_length_rejected(self):
        """Test that non-16-byte packets are not treated as handshakes."""
        driver = MockBLEDriver()
        owner = MockOwner()

        config = {"name": "Test", "enable_peripheral": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver

        central_address = "11:22:33:44:55:66"

        # Try 15-byte packet (too short)
        short_packet = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f'
        interface.handle_peripheral_data(short_packet, central_address)

        # Should not be stored as identity
        assert central_address not in interface.address_to_identity

        # Try 17-byte packet (too long)
        long_packet = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10\x11'
        interface.handle_peripheral_data(long_packet, central_address)

        # Should not be stored as identity
        assert central_address not in interface.address_to_identity

    def test_multiple_handshakes_same_peer_ignored(self):
        """Test that second handshake from same peer is ignored."""
        driver = MockBLEDriver()
        owner = MockOwner()

        config = {"name": "Test", "enable_peripheral": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver

        central_address = "11:22:33:44:55:66"

        # First handshake
        first_identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        interface.handle_peripheral_data(first_identity, central_address)

        # Verify stored
        assert interface.address_to_identity[central_address] == first_identity

        # Second handshake (different identity)
        second_identity = b'\xff\xfe\xfd\xfc\xfb\xfa\xf9\xf8\xf7\xf6\xf5\xf4\xf3\xf2\xf1\xf0'
        interface.handle_peripheral_data(second_identity, central_address)

        # Should still have first identity (not overwritten)
        assert interface.address_to_identity[central_address] == first_identity


class TestIdentityHandshakeBidirectional:
    """Test bidirectional identity exchange using linked drivers."""

    def test_central_reads_peripheral_identity(self):
        """Test that central reads peripheral's identity from characteristic."""
        # Create linked drivers
        central_driver = MockBLEDriver(local_address="AA:AA:AA:AA:AA:AA")
        peripheral_driver = MockBLEDriver(local_address="BB:BB:BB:BB:BB:BB")
        MockBLEDriver.link_drivers(central_driver, peripheral_driver)

        # Start both drivers first (sets up characteristic UUIDs)
        central_driver.start(
            service_uuid="test-uuid",
            rx_char_uuid="rx-uuid",
            tx_char_uuid="tx-uuid",
            identity_char_uuid="identity-uuid"
        )
        peripheral_driver.start(
            service_uuid="test-uuid",
            rx_char_uuid="rx-uuid",
            tx_char_uuid="tx-uuid",
            identity_char_uuid="identity-uuid"
        )

        # Set peripheral identity (after start() so characteristic UUID is available)
        peripheral_identity = b'\x11\x11\x11\x11\x11\x11\x11\x11\x11\x11\x11\x11\x11\x11\x11\x11'
        peripheral_driver.set_identity(peripheral_identity)

        # Central connects to peripheral
        central_driver.connect(peripheral_driver.local_address)

        # Central reads peripheral's identity
        read_identity = central_driver.read_characteristic(
            peripheral_driver.local_address,
            "identity-uuid"
        )

        # Verify identity matches
        assert read_identity == peripheral_identity

    def test_central_sends_identity_handshake(self):
        """Test that central sends its identity to peripheral after connection."""
        # Create linked drivers
        central_driver = MockBLEDriver(local_address="AA:AA:AA:AA:AA:AA")
        peripheral_driver = MockBLEDriver(local_address="BB:BB:BB:BB:BB:BB")
        MockBLEDriver.link_drivers(central_driver, peripheral_driver)

        # Set identities
        central_identity = b'\xaa\xaa\xaa\xaa\xaa\xaa\xaa\xaa\xaa\xaa\xaa\xaa\xaa\xaa\xaa\xaa'
        peripheral_identity = b'\xbb\xbb\xbb\xbb\xbb\xbb\xbb\xbb\xbb\xbb\xbb\xbb\xbb\xbb\xbb\xbb'

        central_driver.set_identity(central_identity)
        peripheral_driver.set_identity(peripheral_identity)

        # Start drivers
        central_driver.start("svc", "rx", "tx", "id")
        peripheral_driver.start("svc", "rx", "tx", "id")

        # Track peripheral's received data
        peripheral_received = []
        peripheral_driver.on_data_received = lambda addr, data: peripheral_received.append((addr, data))

        # Central connects
        central_driver.connect(peripheral_driver.local_address)

        # Central sends identity handshake
        central_driver.send(peripheral_driver.local_address, central_identity)

        # Verify peripheral received the handshake
        assert len(peripheral_received) == 1
        assert peripheral_received[0][0] == central_driver.local_address
        assert peripheral_received[0][1] == central_identity


class TestReassemblerRaceCondition:
    """
    Test that reassembler is created BEFORE peer interface during handshake.

    This prevents a race condition where data arrives immediately after the
    identity handshake but before the reassembler is ready, causing data loss.

    Fix commit: Creates fragmenter/reassembler BEFORE spawning peer interface
    in handle_peripheral_data(), matching the pattern in _mtu_negotiated_callback().
    """

    def test_reassembler_created_before_interface_on_handshake(self):
        """
        Test that reassembler exists before peer interface after handshake.

        Regression test for: "no reassembler for {address}, dropping data"
        This occurred because handle_peripheral_data() spawned the interface
        before creating the reassembler, allowing data to arrive in the gap.
        """
        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_peripheral": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver

        central_address = "11:22:33:44:55:66"
        central_identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'

        # Simulate identity handshake
        interface.handle_peripheral_data(central_identity, central_address)

        # Get keys used for lookup
        frag_key = interface._get_fragmenter_key(central_identity, central_address)
        identity_hash = interface._compute_identity_hash(central_identity)

        # Both reassembler AND interface should exist after handshake
        assert frag_key in interface.reassemblers, "Reassembler should exist after handshake"
        assert frag_key in interface.fragmenters, "Fragmenter should exist after handshake"
        assert identity_hash in interface.spawned_interfaces, "Interface should exist after handshake"

    def test_data_immediately_after_handshake_not_dropped(self):
        """
        Test that data arriving immediately after handshake is processed.

        This simulates the race condition where data packets arrive right
        after the identity handshake completes. Before the fix, data would
        be dropped with "no reassembler" warning.
        """
        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_peripheral": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver

        central_address = "11:22:33:44:55:66"
        central_identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'

        # Step 1: Send identity handshake
        interface.handle_peripheral_data(central_identity, central_address)

        # Step 2: Immediately send data (simulating race condition)
        # This is a minimal fragmented packet: START flag + 1 fragment total + payload
        # Fragment header: [flags(1)] [total_fragments(1)] [fragment_num(1)] [reserved(1)] [payload]
        test_payload = b'\x80\x01\x00\x00' + b'test_data_here!'  # START flag, 1 total, frag 0

        # This should NOT raise or log "no reassembler" - it should be processed
        # (The actual reassembly may fail due to invalid packet format, but that's OK -
        # the key is that we don't get "no reassembler" error)
        frag_key = interface._get_fragmenter_key(central_identity, central_address)

        # Verify reassembler exists before we send data
        assert frag_key in interface.reassemblers, \
            "Reassembler must exist immediately after handshake to prevent data loss"

        # Now send data - it should find the reassembler
        interface.handle_peripheral_data(test_payload, central_address)

        # Verify identity wasn't corrupted (data shouldn't be treated as new handshake)
        assert interface.address_to_identity[central_address] == central_identity

    def test_central_mode_reassembler_order_for_reference(self):
        """
        Verify that central mode creates reassembler before interface.

        This is the reference implementation that peripheral mode should match.
        """
        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        peer_address = "11:22:33:44:55:66"
        peer_identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'

        # Pre-populate identity mapping (simulates identity received during connection)
        interface.address_to_identity[peer_address] = peer_identity
        identity_hash = interface._compute_identity_hash(peer_identity)
        interface.identity_to_address[identity_hash] = peer_address

        # Simulate MTU negotiated callback (central mode entry point)
        interface._mtu_negotiated_callback(peer_address, 185)

        # Get keys
        frag_key = interface._get_fragmenter_key(peer_identity, peer_address)

        # Both should exist
        assert frag_key in interface.reassemblers, "Central mode: reassembler should exist"
        assert frag_key in interface.fragmenters, "Central mode: fragmenter should exist"
        assert identity_hash in interface.spawned_interfaces, "Central mode: interface should exist"


class TestDuplicateIdentityHandshakeRaceCondition:
    """
    Test handling of duplicate identity handshake data.

    When Kotlin provides the identity via callback (from reading the identity characteristic),
    the address_to_identity mapping gets set BEFORE the 16-byte handshake data arrives
    through _data_received_callback. The fix ensures this duplicate handshake data is
    consumed and not passed to the reassembler where it would cause "Invalid fragment type" errors.
    """

    def test_duplicate_handshake_matching_identity_consumed(self):
        """Test that duplicate 16-byte handshake matching known identity is consumed."""
        driver = MockBLEDriver()
        owner = MockOwner()

        config = {"name": "Test", "enable_peripheral": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver

        central_address = "11:22:33:44:55:66"
        central_identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'

        # Simulate identity being set via Kotlin callback (before handshake data arrives)
        interface.address_to_identity[central_address] = central_identity
        identity_hash = interface._compute_identity_hash(central_identity)
        interface.identity_to_address[identity_hash] = central_address

        # Now the handshake data arrives through data channel
        # This should be consumed (return True) and not passed to reassembler
        result = interface._handle_identity_handshake(central_address, central_identity)

        assert result is True, "Duplicate handshake matching identity should be consumed"
        # Identity should still be the same
        assert interface.address_to_identity[central_address] == central_identity

    def test_duplicate_handshake_different_identity_still_consumed(self):
        """Test that 16-byte data different from known identity is still consumed."""
        driver = MockBLEDriver()
        owner = MockOwner()

        config = {"name": "Test", "enable_peripheral": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver

        central_address = "11:22:33:44:55:66"
        known_identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        different_16_bytes = b'\xff\xfe\xfd\xfc\xfb\xfa\xf9\xf8\xf7\xf6\xf5\xf4\xf3\xf2\xf1\xf0'

        # Simulate identity being set via Kotlin callback
        interface.address_to_identity[central_address] = known_identity
        identity_hash = interface._compute_identity_hash(known_identity)
        interface.identity_to_address[identity_hash] = central_address

        # Different 16-byte data arrives - should still be consumed to prevent reassembler errors
        result = interface._handle_identity_handshake(central_address, different_16_bytes)

        assert result is True, "Different 16-byte data should be consumed to prevent reassembler errors"
        # Original identity should be preserved
        assert interface.address_to_identity[central_address] == known_identity

    def test_non_16_byte_data_not_consumed_as_handshake(self):
        """Test that non-16-byte data is not consumed as handshake even with known identity."""
        driver = MockBLEDriver()
        owner = MockOwner()

        config = {"name": "Test", "enable_peripheral": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver

        central_address = "11:22:33:44:55:66"
        known_identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'

        # Set identity via callback
        interface.address_to_identity[central_address] = known_identity

        # Non-16-byte data should not be consumed as handshake
        result_15 = interface._handle_identity_handshake(central_address, b'\x00' * 15)
        result_17 = interface._handle_identity_handshake(central_address, b'\x00' * 17)
        result_10 = interface._handle_identity_handshake(central_address, b'\x00' * 10)

        assert result_15 is False, "15-byte data should not be consumed as handshake"
        assert result_17 is False, "17-byte data should not be consumed as handshake"
        assert result_10 is False, "10-byte data should not be consumed as handshake"

    def test_16_byte_data_without_known_identity_processed_as_handshake(self):
        """Test that 16-byte data without known identity is processed as new handshake."""
        driver = MockBLEDriver()
        owner = MockOwner()

        config = {"name": "Test", "enable_peripheral": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver

        central_address = "11:22:33:44:55:66"
        central_identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'

        # No identity set - this should be processed as a new handshake
        assert central_address not in interface.address_to_identity

        result = interface._handle_identity_handshake(central_address, central_identity)

        assert result is True, "16-byte data without known identity should be processed as handshake"
        assert interface.address_to_identity[central_address] == central_identity


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
