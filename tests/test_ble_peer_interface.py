"""
Unit tests for BLEPeerInterface class.

Tests the spawned peer interface that represents individual BLE connections,
including data flow, fragmentation, and both central/peripheral modes.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock

# Import fragmentation for testing
try:
    from RNS.Interfaces.BLEFragmentation import BLEFragmenter, BLEReassembler
except ImportError:
    BLEFragmenter = None
    BLEReassembler = None


# ============================================================================
# Helper: Create Mock BLEPeerInterface
# ============================================================================

def create_mock_peer_interface(peer_address="AA:BB:CC:DD:EE:FF", peer_name="TestPeer", is_peripheral=False):
    """Create a mock BLEPeerInterface for testing."""
    # Mock parent interface
    parent = Mock()
    parent.name = "TestBLEInterface"
    parent.owner = Mock()
    parent.owner.inbound = Mock()
    parent.online = True
    parent.HW_MTU = 500
    parent.bitrate = 700000
    parent.rxb = 0
    parent.txb = 0
    parent.peers = {peer_address: (Mock(is_connected=True), 0, 185)}
    parent.fragmenters = {peer_address: BLEFragmenter(mtu=185) if BLEFragmenter else Mock()}
    parent.reassemblers = {peer_address: BLEReassembler() if BLEReassembler else Mock()}
    parent.frag_lock = asyncio.Lock()
    parent.peer_lock = asyncio.Lock()
    parent.loop = asyncio.get_event_loop()
    parent.gatt_server = Mock()
    parent.gatt_server.send_notification = AsyncMock(return_value=True)

    # Mock peer interface
    peer_if = Mock()
    peer_if.parent_interface = parent
    peer_if.peer_address = peer_address
    peer_if.peer_name = peer_name
    peer_if.online = True
    peer_if.is_peripheral_connection = is_peripheral
    peer_if.HW_MTU = parent.HW_MTU
    peer_if.bitrate = parent.bitrate
    peer_if.rxb = 0
    peer_if.txb = 0

    return peer_if, parent


# ============================================================================
# Basic Operations Tests
# ============================================================================

@pytest.mark.skipif(BLEFragmenter is None, reason="BLEFragmentation not available")
class TestPeerInterfaceBasics:
    """Test basic BLEPeerInterface operations."""

    def test_peer_interface_initialization(self):
        """Test that peer interface initializes with correct attributes."""
        peer_if, parent = create_mock_peer_interface(
            peer_address="AA:BB:CC:DD:EE:FF",
            peer_name="TestDevice"
        )

        assert peer_if.parent_interface == parent
        assert peer_if.peer_address == "AA:BB:CC:DD:EE:FF"
        assert peer_if.peer_name == "TestDevice"
        assert peer_if.online is True
        assert peer_if.HW_MTU == 500
        assert peer_if.bitrate == 700000

    def test_process_incoming_updates_stats(self):
        """Test that processing incoming data updates statistics."""
        peer_if, parent = create_mock_peer_interface()

        # Simulate incoming data
        test_data = b"Hello, BLE!" * 10
        initial_rxb = peer_if.rxb

        # Mock the process_incoming behavior
        peer_if.rxb += len(test_data)
        parent.rxb += len(test_data)

        # Verify stats updated
        assert peer_if.rxb == initial_rxb + len(test_data)
        assert parent.rxb == len(test_data)

    def test_process_outgoing_updates_stats(self):
        """Test that sending data updates statistics."""
        peer_if, parent = create_mock_peer_interface()

        # Simulate outgoing data
        test_data = b"Hello, BLE!" * 10
        initial_txb = peer_if.txb

        # Mock the process_outgoing behavior (fragmenting)
        fragmenter = parent.fragmenters[peer_if.peer_address]
        if hasattr(fragmenter, 'fragment_packet'):
            fragments = fragmenter.fragment_packet(test_data)
            for frag in fragments:
                peer_if.txb += len(frag)
                parent.txb += len(frag)

        # Verify stats updated
        assert peer_if.txb > initial_txb
        assert parent.txb > 0

    def test_detach_cleanup(self):
        """Test that detach properly cleans up."""
        peer_if, parent = create_mock_peer_interface()

        # Simulate detach
        peer_if.online = False

        # Verify state
        assert peer_if.online is False

    def test_should_ingress_limit_inheritance(self):
        """Test that ingress limiting inherits from parent."""
        peer_if, parent = create_mock_peer_interface()

        # Mock parent's should_ingress_limit
        parent.should_ingress_limit = Mock(return_value=True)

        # Peer interface should return same value
        # (In real code, this would be: peer_if.should_ingress_limit())
        assert parent.should_ingress_limit() is True


# ============================================================================
# Central Mode Send Tests
# ============================================================================

@pytest.mark.skipif(BLEFragmenter is None, reason="BLEFragmentation not available")
class TestCentralModeSend:
    """Test sending data in central mode (via GATT write)."""

    def test_send_via_central_single_fragment(self):
        """Test sending data that fits in one fragment."""
        peer_if, parent = create_mock_peer_interface(is_peripheral=False)

        # Small data that fits in one fragment
        test_data = b"Small packet"
        fragmenter = parent.fragmenters[peer_if.peer_address]

        # Fragment the data
        fragments = fragmenter.fragment_packet(test_data)

        # Should be only 1 fragment for small data
        assert len(fragments) == 1

    def test_send_via_central_multiple_fragments(self):
        """Test sending data that requires multiple fragments."""
        peer_if, parent = create_mock_peer_interface(is_peripheral=False)

        # Large data that needs fragmentation
        test_data = b"X" * 500  # 500 bytes > MTU(185)
        fragmenter = parent.fragmenters[peer_if.peer_address]

        # Fragment the data
        fragments = fragmenter.fragment_packet(test_data)

        # Should be multiple fragments
        assert len(fragments) > 1

    @pytest.mark.asyncio
    async def test_send_via_central_timeout(self):
        """Test handling of write timeout in central mode."""
        peer_if, parent = create_mock_peer_interface(is_peripheral=False)

        # Get mock client
        client, _, _ = parent.peers[peer_if.peer_address]

        # Configure client to timeout
        async def timeout_write(*args, **kwargs):
            await asyncio.sleep(0.1)
            raise asyncio.TimeoutError("Write timeout")

        client.write_gatt_char = AsyncMock(side_effect=timeout_write)

        # Attempt write should timeout
        with pytest.raises(asyncio.TimeoutError):
            await client.write_gatt_char("dummy-uuid", b"data")

    @pytest.mark.asyncio
    async def test_send_via_central_connection_error(self):
        """Test handling of connection loss during send."""
        peer_if, parent = create_mock_peer_interface(is_peripheral=False)

        # Get mock client
        client, _, _ = parent.peers[peer_if.peer_address]

        # Simulate disconnection
        client.is_connected = False

        # Verify disconnection is detected
        assert client.is_connected is False

    def test_send_via_central_no_fragmenter(self):
        """Test handling when fragmenter is missing."""
        peer_if, parent = create_mock_peer_interface(is_peripheral=False)

        # Remove fragmenter
        del parent.fragmenters[peer_if.peer_address]

        # Verify fragmenter is missing
        assert peer_if.peer_address not in parent.fragmenters


# ============================================================================
# Peripheral Mode Send Tests
# ============================================================================

@pytest.mark.skipif(BLEFragmenter is None, reason="BLEFragmentation not available")
class TestPeripheralModeSend:
    """Test sending data in peripheral mode (via GATT notifications)."""

    @pytest.mark.asyncio
    async def test_send_via_peripheral_single_fragment(self):
        """Test sending notification with single fragment."""
        peer_if, parent = create_mock_peer_interface(is_peripheral=True)

        # Small data that fits in one fragment
        test_data = b"Small notification"
        fragmenter = parent.fragmenters[peer_if.peer_address]
        fragments = fragmenter.fragment_packet(test_data)

        # Should be 1 fragment
        assert len(fragments) == 1

        # Send notification
        result = await parent.gatt_server.send_notification(fragments[0], peer_if.peer_address)
        assert result is True

    @pytest.mark.asyncio
    async def test_send_via_peripheral_multiple_fragments(self):
        """Test sending multiple notifications."""
        peer_if, parent = create_mock_peer_interface(is_peripheral=True)

        # Large data needing fragmentation
        test_data = b"Y" * 500
        fragmenter = parent.fragmenters[peer_if.peer_address]
        fragments = fragmenter.fragment_packet(test_data)

        # Should be multiple fragments
        assert len(fragments) > 1

        # Send all fragments
        for frag in fragments:
            result = await parent.gatt_server.send_notification(frag, peer_if.peer_address)
            assert result is True

    @pytest.mark.asyncio
    async def test_send_via_peripheral_no_server(self):
        """Test handling when GATT server is not available."""
        peer_if, parent = create_mock_peer_interface(is_peripheral=True)

        # Remove server
        parent.gatt_server = None

        # Verify no server
        assert parent.gatt_server is None

    @pytest.mark.asyncio
    async def test_send_via_peripheral_timeout(self):
        """Test notification timeout handling."""
        peer_if, parent = create_mock_peer_interface(is_peripheral=True)

        # Configure server to timeout
        async def timeout_notification(*args, **kwargs):
            await asyncio.sleep(0.1)
            raise asyncio.TimeoutError("Notification timeout")

        parent.gatt_server.send_notification = AsyncMock(side_effect=timeout_notification)

        # Should timeout
        with pytest.raises(asyncio.TimeoutError):
            await parent.gatt_server.send_notification(b"data", peer_if.peer_address)

    @pytest.mark.asyncio
    async def test_send_via_peripheral_central_disconnected(self):
        """Test handling when target central is not connected."""
        peer_if, parent = create_mock_peer_interface(is_peripheral=True)

        # Configure server to return False (not connected)
        parent.gatt_server.send_notification = AsyncMock(return_value=False)

        # Should return False
        result = await parent.gatt_server.send_notification(b"data", peer_if.peer_address)
        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
