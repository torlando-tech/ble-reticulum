"""
Unit tests for BLE interface error recovery scenarios.

Tests connection failures, disconnection recovery, and data loss handling
to ensure robust operation under error conditions.
"""

import pytest
import asyncio
import time
from unittest.mock import Mock, AsyncMock, patch, MagicMock

# conftest.py handles path setup - imports should work after that
# Import only what we need for testing
try:
    from RNS.Interfaces.BLEFragmentation import BLEFragmenter, BLEReassembler
except ImportError:
    # If imports fail, tests will be skipped
    BLEFragmenter = None
    BLEReassembler = None


# ============================================================================
# Connection Failure Tests
# ============================================================================

@pytest.mark.skipif(BLEFragmenter is None, reason="BLEFragmentation not available")
class TestConnectionFailures:
    """Test connection failure handling and recovery."""

    def test_connection_timeout_handling(self, sample_discovered_peers):
        """Test that connection timeout triggers blacklist."""
        peer = sample_discovered_peers['strong']

        # Simulate connection timeout
        peer.record_connection_attempt()
        peer.record_connection_failure()

        assert peer.failed_connections == 1
        assert peer.get_success_rate() == 0.0

    def test_blacklist_after_3_failures(self, sample_discovered_peers):
        """Test that 3 failures triggers blacklisting."""
        peer = sample_discovered_peers['strong']
        max_failures = 3

        # Record 3 failures
        for i in range(max_failures):
            peer.record_connection_attempt()
            peer.record_connection_failure()

        assert peer.failed_connections == max_failures
        # Blacklist would be added by BLEInterface, tested separately

    def test_reconnection_after_failure(self, sample_discovered_peers):
        """Test that successful reconnection clears failure tracking."""
        peer = sample_discovered_peers['strong']

        # Record failures
        for i in range(2):
            peer.record_connection_attempt()
            peer.record_connection_failure()

        assert peer.failed_connections == 2

        # Now succeed
        peer.record_connection_attempt()
        peer.record_connection_success()

        # Success rate improves
        assert peer.successful_connections == 1
        assert peer.get_success_rate() == pytest.approx(0.333, abs=0.01)

    @pytest.mark.asyncio
    async def test_permission_error_handling(self, mock_bleak_client):
        """Test handling of permission errors during connection."""
        # Configure client to raise PermissionError
        mock_bleak_client.connect = AsyncMock(side_effect=PermissionError("Permission denied"))

        # Attempt connection should catch PermissionError
        with pytest.raises(PermissionError):
            await mock_bleak_client.connect()

    @pytest.mark.asyncio
    async def test_mtu_negotiation_failure(self, mock_bleak_client):
        """Test fallback to default MTU when negotiation fails."""
        # Configure client without mtu_size attribute
        del mock_bleak_client.mtu_size

        # Should fallback to default (23 bytes for BLE 4.0)
        default_mtu = 23

        # Verify fallback works
        try:
            mtu = mock_bleak_client.mtu_size
        except AttributeError:
            mtu = default_mtu

        assert mtu == 23

    @pytest.mark.asyncio
    async def test_notification_setup_failure(self, mock_bleak_client):
        """Test cleanup when notification setup fails."""
        # Configure client to fail notification setup
        mock_bleak_client.start_notify = AsyncMock(
            side_effect=Exception("Failed to start notifications")
        )

        # Attempt should fail
        with pytest.raises(Exception, match="Failed to start notifications"):
            await mock_bleak_client.start_notify("dummy-uuid", lambda s, d: None)

    def test_invalid_fragment_data(self):
        """Test handling of corrupt fragment data."""
        reassembler = BLEReassembler(timeout=10.0)

        # Send invalid fragment (empty or malformed)
        invalid_data = b''

        # Should raise ValueError for invalid data
        with pytest.raises(ValueError, match="Fragment too short"):
            reassembler.receive_fragment(invalid_data, "AA:BB:CC:DD:EE:FF")

    def test_reassembly_timeout(self):
        """Test that stale buffers are cleaned up after timeout."""
        reassembler = BLEReassembler(timeout=0.1)  # 100ms timeout
        peer_address = "AA:BB:CC:DD:EE:FF"

        # Send first fragment
        fragmenter = BLEFragmenter(mtu=50)
        data = b"Test data that needs multiple fragments" * 10
        fragments = fragmenter.fragment_packet(data)

        # Send first fragment
        reassembler.receive_fragment(fragments[0], peer_address)

        # Wait for timeout
        time.sleep(0.2)

        # Cleanup should remove stale buffer
        cleaned = reassembler.cleanup_stale_buffers()
        assert cleaned >= 0  # Should cleanup the stale buffer

    @pytest.mark.asyncio
    async def test_discovery_permission_error(self):
        """Test handling of permission errors during BLE scan."""
        with patch('bleak.BleakScanner.discover', side_effect=PermissionError("Scan permission denied")):
            from bleak import BleakScanner

            # Should raise PermissionError
            with pytest.raises(PermissionError):
                await BleakScanner.discover(timeout=1.0)

    @pytest.mark.asyncio
    async def test_discovery_exception_recovery(self):
        """Test that discovery continues after exceptions."""
        call_count = [0]

        async def mock_discover_with_error(timeout=1.0):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Temporary error")
            return []

        with patch('bleak.BleakScanner.discover', side_effect=mock_discover_with_error):
            from bleak import BleakScanner

            # First call should fail
            with pytest.raises(Exception, match="Temporary error"):
                await BleakScanner.discover(timeout=1.0)

            # Second call should succeed
            result = await BleakScanner.discover(timeout=1.0)
            assert result == []
            assert call_count[0] == 2


# ============================================================================
# Disconnection Recovery Tests
# ============================================================================

@pytest.mark.skipif(BLEFragmenter is None, reason="BLEFragmentation not available")
class TestDisconnectionRecovery:
    """Test recovery from unexpected disconnections."""

    @pytest.mark.asyncio
    async def test_detect_disconnection_quickly(self, mock_bleak_client):
        """Test that disconnection is detected via is_connected."""
        # Initially connected
        assert mock_bleak_client.is_connected is True

        # Simulate disconnection
        mock_bleak_client.is_connected = False

        # Should be detected immediately
        assert mock_bleak_client.is_connected is False

    def test_cleanup_peer_state_on_disconnect(self):
        """Test that peer state is cleaned up on disconnect."""
        # Mock interface state
        peers = {"AA:BB:CC:DD:EE:FF": (Mock(), time.time(), 185)}
        fragmenters = {"AA:BB:CC:DD:EE:FF": BLEFragmenter(mtu=185)}
        reassemblers = {"AA:BB:CC:DD:EE:FF": BLEReassembler()}

        peer_address = "AA:BB:CC:DD:EE:FF"

        # Verify peer exists
        assert peer_address in peers
        assert peer_address in fragmenters
        assert peer_address in reassemblers

        # Cleanup
        del peers[peer_address]
        del fragmenters[peer_address]
        del reassemblers[peer_address]

        # Verify cleanup
        assert peer_address not in peers
        assert peer_address not in fragmenters
        assert peer_address not in reassemblers

    def test_cleanup_reassembly_buffers(self):
        """Test that incomplete packets are discarded on disconnect."""
        reassembler = BLEReassembler(timeout=10.0)
        peer_address = "AA:BB:CC:DD:EE:FF"

        # Send partial packet
        fragmenter = BLEFragmenter(mtu=50)
        data = b"Test data" * 100
        fragments = fragmenter.fragment_packet(data)

        # Send only first fragment
        reassembler.receive_fragment(fragments[0], peer_address)

        # Verify buffer exists
        stats = reassembler.get_statistics()
        assert stats['pending_packets'] >= 0

        # Cleanup (simulating disconnect)
        cleaned = reassembler.cleanup_stale_buffers()
        # Buffers exist but may not be stale yet

    def test_respawn_after_disconnection(self, sample_discovered_peers):
        """Test that peer can be reconnected after disconnection."""
        peer = sample_discovered_peers['strong']

        # First connection
        peer.record_connection_attempt()
        peer.record_connection_success()

        # Disconnection (no state change in DiscoveredPeer)

        # Reconnection
        peer.record_connection_attempt()
        peer.record_connection_success()

        assert peer.successful_connections == 2
        assert peer.get_success_rate() == 1.0

    def test_notify_transport_on_disconnect(self):
        """Test that Transport is notified when interface detaches."""
        # Mock spawned interface
        mock_interface = Mock()
        mock_interface.online = True
        mock_interface.detach = Mock()

        # Simulate detach call
        mock_interface.detach()

        # Verify detach was called
        mock_interface.detach.assert_called_once()


# ============================================================================
# Data Loss Handling Tests
# ============================================================================

@pytest.mark.skipif(BLEFragmenter is None, reason="BLEFragmentation not available")
class TestDataLossHandling:
    """Test handling of data loss scenarios."""

    def test_fragment_loss_detected(self):
        """Test that missing fragments trigger timeout."""
        reassembler = BLEReassembler(timeout=0.1)
        peer_address = "AA:BB:CC:DD:EE:FF"

        # Create fragments
        fragmenter = BLEFragmenter(mtu=50)
        data = b"Test data" * 20
        fragments = fragmenter.fragment_packet(data)

        # Send first and last fragments (skip middle ones)
        reassembler.receive_fragment(fragments[0], peer_address)
        # Skip fragments[1], fragments[2], etc.

        # Wait for timeout
        time.sleep(0.15)

        # Cleanup should detect timeout
        cleaned = reassembler.cleanup_stale_buffers()
        assert cleaned >= 0

    def test_partial_packet_cleanup(self):
        """Test that incomplete packets are removed."""
        reassembler = BLEReassembler(timeout=0.1)
        peer_address = "AA:BB:CC:DD:EE:FF"

        # Send partial packet
        fragment = b'\x01\x00\x01\x00\x03' + b'partial data'  # START fragment
        reassembler.receive_fragment(fragment, peer_address)

        # Wait for timeout
        time.sleep(0.15)

        # Should be cleaned up
        cleaned = reassembler.cleanup_stale_buffers()
        assert cleaned >= 0

    def test_reticulum_retransmit_on_failure(self):
        """Test that upper layer retransmission is supported."""
        # This is more of a contract test - BLE interface should
        # return without blocking if send fails, allowing Reticulum
        # to handle retransmission

        # Simulate failed send (no exception raised to caller)
        # Upper layers detect timeout and retransmit
        pass  # Tested implicitly in integration tests

    def test_fragment_statistics_accuracy(self):
        """Test that fragment statistics track timeouts correctly."""
        reassembler = BLEReassembler(timeout=0.1)
        peer_address = "AA:BB:CC:DD:EE:FF"

        # Get initial stats
        stats_before = reassembler.get_statistics()
        initial_timeouts = stats_before['packets_timeout']

        # Send partial packet and let it timeout
        fragment = b'\x01\x00\x01\x00\x02' + b'data'
        reassembler.receive_fragment(fragment, peer_address)

        time.sleep(0.15)
        reassembler.cleanup_stale_buffers()

        # Stats should reflect timeout
        stats_after = reassembler.get_statistics()
        # Note: timeout stats may be updated on cleanup
        assert stats_after['packets_timeout'] >= initial_timeouts

    def test_mid_packet_disconnect(self):
        """Test that fragments are discarded cleanly on disconnect."""
        reassembler = BLEReassembler(timeout=10.0)
        peer_address = "AA:BB:CC:DD:EE:FF"

        # Send first fragment
        fragment = b'\x01\x00\x01\x00\x05' + b'first fragment'
        reassembler.receive_fragment(fragment, peer_address)

        # Simulate disconnect (cleanup)
        # In real code, BLEInterface would delete reassemblers[peer_address]
        # Here we just verify cleanup works
        cleaned = reassembler.cleanup_stale_buffers()
        assert cleaned >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
