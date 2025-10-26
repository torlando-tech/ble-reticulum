#!/usr/bin/env python3
"""
Unit tests for BLE fragmentation protocol
"""

import pytest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

from RNS.Interfaces.BLEFragmentation import BLEFragmenter, BLEReassembler, HDLCFramer


class TestBLEFragmenter:
    """Test BLE packet fragmentation"""

    def test_small_packet_no_fragmentation(self):
        """Small packets should still get fragment headers for consistency"""
        fragmenter = BLEFragmenter(mtu=185)
        packet = b"Hello, Reticulum!"

        fragments = fragmenter.fragment_packet(packet)

        assert len(fragments) == 1
        # Even small packets get headers for uniform protocol handling
        assert len(fragments[0]) == len(packet) + BLEFragmenter.HEADER_SIZE

    def test_exact_mtu_no_fragmentation(self):
        """Packet exactly MTU size will need fragmentation due to headers"""
        mtu = 185
        fragmenter = BLEFragmenter(mtu=mtu)
        packet = b"X" * mtu

        fragments = fragmenter.fragment_packet(packet)

        # With 5-byte header, 185-byte packet needs 2 fragments
        # Fragment 0: 5 header + 180 data = 185
        # Fragment 1: 5 header + 5 data = 10
        assert len(fragments) == 2

    def test_large_packet_fragmentation(self):
        """Large packets should be fragmented"""
        fragmenter = BLEFragmenter(mtu=185)
        packet = b"A" * 500  # Reticulum standard packet size

        fragments = fragmenter.fragment_packet(packet)

        # Should be split into multiple fragments
        assert len(fragments) > 1
        assert len(fragments) == 3  # 500 bytes / 180 payload per fragment

        # Check fragment sizes
        for frag in fragments[:-1]:
            assert len(frag) == 185  # MTU size

        # Last fragment may be smaller
        assert len(fragments[-1]) <= 185

    def test_fragment_headers(self):
        """Fragment headers should be correct"""
        fragmenter = BLEFragmenter(mtu=100)
        packet = b"B" * 300

        fragments = fragmenter.fragment_packet(packet)

        # Check first fragment (START)
        assert fragments[0][0] == BLEFragmenter.TYPE_START

        # Check middle fragments (CONTINUE)
        if len(fragments) > 2:
            for frag in fragments[1:-1]:
                assert frag[0] == BLEFragmenter.TYPE_CONTINUE

        # Check last fragment (END)
        assert fragments[-1][0] == BLEFragmenter.TYPE_END

    def test_sequence_numbers(self):
        """Sequence numbers should be sequential"""
        fragmenter = BLEFragmenter(mtu=50)
        packet = b"C" * 200

        fragments = fragmenter.fragment_packet(packet)

        for i, frag in enumerate(fragments):
            # Extract sequence number (bytes 1-2, big endian)
            seq = (frag[1] << 8) | frag[2]
            assert seq == i

    def test_total_count(self):
        """Total fragment count should be correct in all fragments"""
        fragmenter = BLEFragmenter(mtu=50)
        packet = b"D" * 200

        fragments = fragmenter.fragment_packet(packet)
        total_expected = len(fragments)

        for frag in fragments:
            # Extract total count (bytes 3-4, big endian)
            total = (frag[3] << 8) | frag[4]
            assert total == total_expected

    def test_overhead_calculation(self):
        """Overhead calculation should be accurate"""
        fragmenter = BLEFragmenter(mtu=185)

        # Small packet (still has header overhead)
        num_frags, overhead, pct = fragmenter.get_fragment_overhead(100)
        assert num_frags == 1
        assert overhead == 5  # 1 fragment * 5 byte header
        assert pct == (5 / 100) * 100

        # Large packet (requires fragmentation)
        num_frags, overhead, pct = fragmenter.get_fragment_overhead(500)
        assert num_frags == 3
        assert overhead == 3 * 5  # 3 fragments * 5 byte header
        assert pct == (15 / 500) * 100

    def test_empty_packet_error(self):
        """Empty packets should raise ValueError"""
        fragmenter = BLEFragmenter(mtu=185)

        with pytest.raises(ValueError):
            fragmenter.fragment_packet(b"")

    def test_invalid_type_error(self):
        """Non-bytes packet should raise TypeError"""
        fragmenter = BLEFragmenter(mtu=185)

        with pytest.raises(TypeError):
            fragmenter.fragment_packet("not bytes")


class TestBLEReassembler:
    """Test BLE packet reassembly"""

    def test_single_fragment_packet(self):
        """Single-fragment packet should be returned as-is"""
        fragmenter = BLEFragmenter(mtu=185)
        reassembler = BLEReassembler()

        original = b"Short message"
        fragments = fragmenter.fragment_packet(original)
        assert len(fragments) == 1

        # Non-fragmented packets are returned as-is without headers
        result = reassembler.receive_fragment(fragments[0], "device1")
        assert result == original

    def test_multi_fragment_reassembly(self):
        """Multi-fragment packet should be reassembled correctly"""
        fragmenter = BLEFragmenter(mtu=100)
        reassembler = BLEReassembler()

        original = b"E" * 300
        fragments = fragmenter.fragment_packet(original)
        assert len(fragments) > 1

        # Send all but last fragment
        for frag in fragments[:-1]:
            result = reassembler.receive_fragment(frag, "device1")
            assert result is None  # Not complete yet

        # Send last fragment
        result = reassembler.receive_fragment(fragments[-1], "device1")
        assert result == original  # Complete!

    def test_out_of_order_fragments(self):
        """Fragments arriving out of order should be handled"""
        fragmenter = BLEFragmenter(mtu=50)
        reassembler = BLEReassembler()

        original = b"F" * 150  # Size to ensure exactly 4 fragments
        fragments = fragmenter.fragment_packet(original)

        # Ensure we have exactly 4 fragments for this test
        assert len(fragments) == 4, f"Expected 4 fragments, got {len(fragments)}"

        # Send in scrambled order: 0, 2, 1, 3 (all fragments, just out of order)
        order = [0, 2, 1, 3]
        for i in order[:-1]:
            result = reassembler.receive_fragment(fragments[i], "device1")
            assert result is None  # Not complete yet

        result = reassembler.receive_fragment(fragments[order[-1]], "device1")
        assert result == original  # Should be complete now

    def test_multiple_senders(self):
        """Should handle fragments from multiple senders simultaneously"""
        fragmenter = BLEFragmenter(mtu=100)
        reassembler = BLEReassembler()

        packet_a = b"A" * 300
        packet_b = b"B" * 300

        fragments_a = fragmenter.fragment_packet(packet_a)
        fragments_b = fragmenter.fragment_packet(packet_b)

        # Interleave fragments from two senders
        for i in range(max(len(fragments_a), len(fragments_b))):
            if i < len(fragments_a):
                result_a = reassembler.receive_fragment(fragments_a[i], "device1")
                if i == len(fragments_a) - 1:
                    assert result_a == packet_a
                else:
                    assert result_a is None

            if i < len(fragments_b):
                result_b = reassembler.receive_fragment(fragments_b[i], "device2")
                if i == len(fragments_b) - 1:
                    assert result_b == packet_b
                else:
                    assert result_b is None

    def test_timeout_cleanup(self):
        """Stale fragments should be cleaned up after timeout"""
        fragmenter = BLEFragmenter(mtu=100)
        reassembler = BLEReassembler(timeout=0.1)  # Very short timeout

        original = b"G" * 300
        fragments = fragmenter.fragment_packet(original)

        # Send only first fragment
        result = reassembler.receive_fragment(fragments[0], "device1")
        assert result is None
        assert len(reassembler.reassembly_buffers) == 1

        # Wait for timeout
        import time
        time.sleep(0.2)

        # Cleanup should remove stale buffer
        removed = reassembler.cleanup_stale_buffers()
        assert removed == 1
        assert len(reassembler.reassembly_buffers) == 0

    def test_statistics(self):
        """Statistics should be tracked correctly"""
        fragmenter = BLEFragmenter(mtu=100)
        reassembler = BLEReassembler()

        packet = b"H" * 300
        fragments = fragmenter.fragment_packet(packet)

        for frag in fragments:
            reassembler.receive_fragment(frag, "device1")

        stats = reassembler.get_statistics()
        assert stats['packets_reassembled'] == 1
        assert stats['fragments_received'] == len(fragments)
        assert stats['pending_packets'] == 0


class TestHDLCFramer:
    """Test HDLC framing (alternative to fragmentation)"""

    def test_frame_simple_packet(self):
        """Simple packet should be framed correctly"""
        packet = b"Hello, World!"
        framed = HDLCFramer.frame_packet(packet)

        # Should start and end with FLAG
        assert framed[0] == HDLCFramer.FLAG
        assert framed[-1] == HDLCFramer.FLAG

        # Should be deframeable
        deframed = HDLCFramer.deframe_packet(framed)
        assert deframed == packet

    def test_frame_with_flag_bytes(self):
        """Packet containing FLAG bytes should be stuffed"""
        packet = bytes([0x7E, 0x01, 0x7E])  # Contains FLAG bytes
        framed = HDLCFramer.frame_packet(packet)

        # Should be longer due to byte stuffing
        assert len(framed) > len(packet) + 2

        # Should deframe correctly
        deframed = HDLCFramer.deframe_packet(framed)
        assert deframed == packet

    def test_frame_with_escape_bytes(self):
        """Packet containing ESCAPE bytes should be stuffed"""
        packet = bytes([0x7D, 0x02, 0x7D])  # Contains ESCAPE bytes
        framed = HDLCFramer.frame_packet(packet)

        # Should be longer due to byte stuffing
        assert len(framed) > len(packet) + 2

        # Should deframe correctly
        deframed = HDLCFramer.deframe_packet(framed)
        assert deframed == packet

    def test_round_trip(self):
        """Frame then deframe should return original"""
        for i in range(256):
            packet = bytes([i] * 10)
            framed = HDLCFramer.frame_packet(packet)
            deframed = HDLCFramer.deframe_packet(framed)
            assert deframed == packet


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
