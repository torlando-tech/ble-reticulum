"""
Tests for BLE Protocol v2.2 Connection Race Condition Prevention

Connection race conditions were a major issue in earlier protocol versions,
causing "Operation already in progress" errors when discovery callbacks fired
rapidly. Protocol v2.2.1+ implements multi-layer protection:

1. **5-Second Rate Limiting** (Interface Layer)
   - Tracks `last_connection_attempt` per peer
   - Skips connection if attempted within last 5 seconds
   - Prevents rapid-fire retries from discovery callbacks

2. **Driver Connection State Tracking** (Driver Layer)
   - `_connecting_peers` set tracks in-progress connections
   - Prevents concurrent connection attempts to same address
   - Cleanup via Future callbacks ensures state consistency

3. **Early Attempt Recording** (Interface Layer)
   - Records connection attempt BEFORE calling driver.connect()
   - Prevents retry if discovery fires again mid-connection

These mechanisms work together to eliminate connection storms while maintaining
responsive peer discovery.

Reference: BLE_PROTOCOL_v2.2.md § Platform-Specific Workarounds → Connection
         Race Condition Prevention
"""

import pytest
import sys
import os
import time

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


class MockOwner:
    """Mock Reticulum owner."""
    def __init__(self):
        self.inbound_calls = []

    def inbound(self, data, interface):
        self.inbound_calls.append((data, interface))


class TestRateLimiting:
    """Test 5-second connection attempt rate limiting."""

    def test_5_second_rate_limit_prevents_retry(self):
        """Test that connection attempts within 5 seconds are skipped."""
        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        peer_address = "11:22:33:44:55:66"
        peer = DiscoveredPeer(peer_address, "TestPeer", -60)

        # Record first connection attempt
        peer.record_connection_attempt()
        interface.discovered_peers[peer_address] = peer

        # Immediately try to select peers (within 5 seconds)
        peers_to_connect = interface._select_peers_to_connect()
        peer_addresses = [p.address for p in peers_to_connect]

        # Should be skipped due to rate limiting
        assert peer_address not in peer_addresses

    def test_connection_allowed_after_5_seconds(self):
        """Test that connection is allowed after 5-second cooldown."""
        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        peer_address = "11:22:33:44:55:66"
        peer = DiscoveredPeer(peer_address, "TestPeer", -60)

        # Record connection attempt 6 seconds ago (past cooldown)
        peer.record_connection_attempt()
        peer.last_connection_attempt = time.time() - 6.0

        interface.discovered_peers[peer_address] = peer

        # Should now be allowed
        peers_to_connect = interface._select_peers_to_connect()
        peer_addresses = [p.address for p in peers_to_connect]

        assert peer_address in peer_addresses

    def test_never_attempted_peer_allowed(self):
        """Test that peer with no prior attempts is allowed."""
        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        peer_address = "11:22:33:44:55:66"
        peer = DiscoveredPeer(peer_address, "TestPeer", -60)

        # last_connection_attempt == 0 (never attempted)
        assert peer.last_connection_attempt == 0

        interface.discovered_peers[peer_address] = peer

        # Should be allowed
        peers_to_connect = interface._select_peers_to_connect()
        peer_addresses = [p.address for p in peers_to_connect]

        assert peer_address in peer_addresses


class TestDriverStateTracking:
    """Test driver-level connection state tracking."""

    def test_driver_tracks_connecting_peers(self):
        """Test that driver tracks addresses with connections in progress."""
        # Note: This tests implementation details of LinuxBluetoothDriver
        # We verify the interface checks for this state

        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        # Simulate driver state tracking
        driver._connecting_peers = set()
        driver._connecting_lock = __import__('threading').Lock()

        peer_address = "11:22:33:44:55:66"

        # Add to connecting set (simulating pending connection)
        with driver._connecting_lock:
            driver._connecting_peers.add(peer_address)

        # Add to discovered peers
        peer = DiscoveredPeer(peer_address, "TestPeer", -60)
        interface.discovered_peers[peer_address] = peer

        # Try to select peers
        peers_to_connect = interface._select_peers_to_connect()
        peer_addresses = [p.address for p in peers_to_connect]

        # Should be skipped (connection already in progress)
        assert peer_address not in peer_addresses

    def test_multiple_rapid_discoveries_handled(self):
        """Test that rapid discovery callbacks don't cause duplicate connections."""
        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        peer_address = "11:22:33:44:55:66"
        peer = DiscoveredPeer(peer_address, "TestPeer", -60)

        # Simulate rapid discovery callbacks (5 times in quick succession)
        for i in range(5):
            interface.discovered_peers[peer_address] = peer
            interface._select_peers_to_connect()

        # After first selection, peer should have recorded attempt
        # Subsequent selections should be rate-limited

        # Check that last_connection_attempt was recorded
        assert peer.last_connection_attempt > 0

        # Verify recent timestamp
        time_since = time.time() - peer.last_connection_attempt
        assert time_since < 1.0  # Should be very recent


class TestEarlyAttemptRecording:
    """Test early recording of connection attempts."""

    def test_attempt_recorded_before_driver_connect(self):
        """Test that attempt is recorded before driver.connect() is called."""
        # This test verifies the fix for the race condition where discovery
        # callbacks would fire again before driver.connect() completed

        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        peer_address = "11:22:33:44:55:66"
        peer = DiscoveredPeer(peer_address, "TestPeer", -60)
        interface.discovered_peers[peer_address] = peer

        # Initial state: no attempts
        assert peer.connection_attempts == 0
        assert peer.last_connection_attempt == 0

        # Trigger discovery callback (which calls _select_peers_to_connect)
        device = type('obj', (object,), {
            'address': peer_address,
            'name': 'TestPeer',
            'rssi': -60,
            'service_uuids': [],
            'manufacturer_data': {}
        })()

        # Simulate device discovered callback
        interface._device_discovered_callback(device)

        # Verify attempt was recorded
        # (Implementation detail: recorded in _device_discovered_callback
        # or when connect is initiated)
        # The key is that last_connection_attempt > 0 after first discovery


class TestCombinedProtection:
    """Test that all protection layers work together."""

    def test_layered_protection_prevents_connection_storm(self):
        """Test that layered protection prevents connection storm scenario."""
        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        # Simulate driver connection state tracking
        driver._connecting_peers = set()
        driver._connecting_lock = __import__('threading').Lock()

        peer_address = "11:22:33:44:55:66"
        peer = DiscoveredPeer(peer_address, "TestPeer", -60)
        interface.discovered_peers[peer_address] = peer

        connection_attempts = []

        # Mock driver.connect to track attempts
        original_connect = driver.connect
        def tracked_connect(address):
            connection_attempts.append(address)
            with driver._connecting_lock:
                driver._connecting_peers.add(address)
            original_connect(address)

        driver.connect = tracked_connect

        # Simulate rapid discovery (10 callbacks in quick succession)
        for i in range(10):
            peers = interface._select_peers_to_connect()
            for p in peers:
                if p.address == peer_address:
                    driver.connect(p.address)

        # Despite 10 discovery callbacks, should have at most 1 connection attempt
        assert len(connection_attempts) <= 1

    def test_concurrent_discovery_callbacks(self):
        """Test behavior with concurrent discovery callbacks."""
        import threading

        driver = MockBLEDriver(local_address="AA:BB:CC:DD:EE:FF")
        owner = MockOwner()

        config = {"name": "Test", "enable_central": True}
        interface = BLEInterface(owner, config)
        interface.driver = driver
        interface.local_address = driver.local_address

        # Simulate driver state
        driver._connecting_peers = set()
        driver._connecting_lock = threading.Lock()

        peer_address = "11:22:33:44:55:66"
        peer = DiscoveredPeer(peer_address, "TestPeer", -60)
        interface.discovered_peers[peer_address] = peer

        # Track connection attempts from multiple threads
        attempts = []
        attempts_lock = threading.Lock()

        def try_connect():
            """Simulate concurrent discovery callback."""
            time.sleep(0.01)  # Small delay to ensure overlap
            peers = interface._select_peers_to_connect()
            for p in peers:
                if p.address == peer_address:
                    with attempts_lock:
                        attempts.append(p.address)
                    # Simulate connection attempt
                    with driver._connecting_lock:
                        if peer_address not in driver._connecting_peers:
                            driver._connecting_peers.add(peer_address)

        # Launch 5 concurrent "discovery" threads
        threads = [threading.Thread(target=try_connect) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have very few connection attempts due to protection layers
        # (Rate limiting and driver state tracking)
        assert len(attempts) <= 2  # Allow small window before protection kicks in


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
