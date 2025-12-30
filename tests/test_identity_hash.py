#!/usr/bin/env python3
"""
Unit tests for _compute_identity_hash() function.

This tests the fix for double-hashing bug where peer_identity (already a hash
from BLE handshake) was incorrectly passed through RNS.Identity.full_hash(),
producing a different value and causing "no reassembler for X" errors.

The tests verify the expected behavior without importing BLEInterface directly
(which has heavy RNS dependencies), instead testing the core logic.
"""

import pytest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))


def compute_identity_hash_fixed(peer_identity):
    """
    The FIXED implementation of _compute_identity_hash().

    This is what the code should do: just convert bytes to hex.
    """
    # peer_identity is already the identity hash from BLE handshake
    # Just convert to hex, don't re-hash (that would corrupt the identity!)
    return peer_identity.hex()[:16]


class TestComputeIdentityHash:
    """Test _compute_identity_hash() returns correct hex representation."""

    def test_identity_hash_returns_hex_of_input(self):
        """
        _compute_identity_hash should return first 16 hex chars of input bytes.

        The peer_identity parameter is already the identity hash from BLE handshake.
        We should NOT hash it again - just convert to hex.
        """
        # Test identity bytes (16 bytes = 32 hex chars, we want first 16)
        test_identity = bytes.fromhex("232f48ba94a3142937c9a64714112ff3")

        result = compute_identity_hash_fixed(test_identity)

        # Should be first 16 hex chars of the input (8 bytes = 16 hex chars)
        assert result == "232f48ba94a31429"
        assert len(result) == 16

    def test_identity_hash_does_not_double_hash(self):
        """
        Verify the fix: _compute_identity_hash must NOT apply RNS.Identity.full_hash().

        The old buggy code did:
            return RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]

        This would produce a completely different value, causing sender/receiver
        identity mismatch and "no reassembler" errors.
        """
        import hashlib

        # Real identity bytes from a test session
        test_identity = bytes.fromhex("232f48ba94a3142937c9a64714112ff3")

        # Get the correct result (hex of input)
        correct_result = compute_identity_hash_fixed(test_identity)

        # Simulate what RNS.Identity.full_hash does (SHA-256)
        # This is what the buggy code would have produced
        buggy_hash = hashlib.sha256(test_identity).digest()
        buggy_result = buggy_hash[:16].hex()[:16]

        # The correct result should be hex of input, NOT a hash of the input
        assert correct_result == test_identity.hex()[:16]

        # The buggy result would be different (a hash of the already-hashed identity)
        assert correct_result != buggy_result, \
            "If these are equal, the test identity accidentally produces same hash"

    def test_identity_hash_with_various_inputs(self):
        """Test with various identity byte patterns."""
        test_cases = [
            bytes.fromhex("00000000000000000000000000000000"),
            bytes.fromhex("ffffffffffffffffffffffffffffffff"),
            bytes.fromhex("0123456789abcdef0123456789abcdef"),
            bytes.fromhex("deadbeefcafebabe1234567890abcdef"),
        ]

        for identity in test_cases:
            result = compute_identity_hash_fixed(identity)

            # Result should always be first 16 hex chars of input
            assert result == identity.hex()[:16]
            assert len(result) == 16
            # Should be valid hex
            int(result, 16)

    def test_actual_bleinterface_implementation(self):
        """
        Verify BLEInterface._compute_identity_hash matches expected behavior.

        This test reads the actual source code and verifies it contains the fix.
        """
        import re

        # Read the actual BLEInterface.py source
        ble_interface_path = os.path.join(
            os.path.dirname(__file__),
            '../src/ble_reticulum/BLEInterface.py'
        )

        with open(ble_interface_path, 'r') as f:
            source = f.read()

        # Find the _compute_identity_hash method
        # Look for the fixed implementation pattern
        fixed_pattern = r'def _compute_identity_hash.*?return peer_identity\.hex\(\)\[:16\]'

        # Look for the buggy implementation pattern
        buggy_pattern = r'RNS\.Identity\.full_hash\(peer_identity\)'

        # The fixed code should have peer_identity.hex()[:16]
        assert re.search(fixed_pattern, source, re.DOTALL), \
            "_compute_identity_hash should use peer_identity.hex()[:16]"

        # The fixed code should NOT have the double-hash
        assert not re.search(buggy_pattern, source), \
            "_compute_identity_hash should NOT use RNS.Identity.full_hash(peer_identity)"
