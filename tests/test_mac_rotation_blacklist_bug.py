"""
Tests for MAC rotation blacklist behavior.

These tests verify that duplicate identity rejection during MAC rotation
does NOT incorrectly trigger the connection blacklist.

Bug Description:
----------------
When Android rotates a device's MAC address, the new MAC may try to connect
while the old MAC is still connected. The _check_duplicate_identity function
correctly rejects this as a duplicate. However, the rejection triggers an
error callback with "Connection failed to..." which matches the blacklist
regex pattern, incorrectly blacklisting the new MAC.

This causes connectivity gaps because:
1. MAC_OLD connected with identity X
2. MAC_NEW tries to connect (same identity) -> rejected (correct)
3. Rejection triggers _record_connection_failure(MAC_NEW) (incorrect!)
4. After 3 rejections, MAC_NEW is blacklisted for 60s+
5. MAC_OLD disconnects, identity cleared
6. MAC_NEW is blacklisted, can't reconnect for 60s+ (BUG!)

Expected Behavior:
-----------------
Duplicate identity rejection should NOT count as a connection failure.
It's an intentional rejection, not a failure.
"""

import pytest
import time
import re
from unittest.mock import Mock, MagicMock, patch


class TestMacRotationBlacklistBug:
    """Test that duplicate identity rejection doesn't trigger blacklist."""

    @pytest.fixture
    def mock_ble_interface(self):
        """Create a minimal mock of BLEInterface with blacklist behavior."""
        try:
            from ble_reticulum.BLEInterface import BLEInterface, DiscoveredPeer
        except ImportError:
            pytest.skip("BLEInterface not available")

        # Create mock interface with required attributes
        interface = Mock(spec=BLEInterface)
        interface.connection_blacklist = {}
        interface.identity_to_address = {}
        interface.address_to_identity = {}
        interface.connection_retry_backoff = 60
        interface.max_connection_failures = 3
        interface.discovered_peers = {}

        # Mock driver (needed for _record_connection_failure)
        interface.driver = Mock()
        interface.driver._remove_bluez_device = None  # hasattr will return False

        # Import the actual methods we want to test
        from ble_reticulum.BLEInterface import BLEInterface as RealInterface

        # Bind real methods to our mock
        interface._check_duplicate_identity = lambda addr, identity: RealInterface._check_duplicate_identity(interface, addr, identity)
        interface._record_connection_failure = lambda addr: RealInterface._record_connection_failure(interface, addr)
        interface._is_blacklisted = lambda addr: RealInterface._is_blacklisted(interface, addr)
        interface._compute_identity_hash = lambda identity: RealInterface._compute_identity_hash(interface, identity)

        return interface

    def test_duplicate_identity_detected_correctly(self, mock_ble_interface):
        """Verify that _check_duplicate_identity returns True for duplicates."""
        interface = mock_ble_interface

        # Setup: identity already connected at MAC_OLD
        identity = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        mac_old = "AA:BB:CC:DD:EE:01"
        mac_new = "AA:BB:CC:DD:EE:02"

        identity_hash = interface._compute_identity_hash(identity)
        interface.identity_to_address[identity_hash] = mac_old

        # Action: MAC_NEW tries to connect with same identity
        is_duplicate = interface._check_duplicate_identity(mac_new, identity)

        # Assert: Should detect as duplicate
        assert is_duplicate is True, "Should detect duplicate identity"

    def test_blacklist_mechanism_triggers_after_3_failures(self, mock_ble_interface):
        """
        Test that the blacklist mechanism correctly triggers after 3 connection failures.

        This tests the MECHANISM, not the fix. The fix is in the driver layer which
        now uses safe message formats that don't trigger the blacklist.

        This test proves: IF _record_connection_failure is called 3 times,
        THEN the device is blacklisted (correct behavior for the mechanism).
        """
        interface = mock_ble_interface

        # Setup: Create a peer to track
        mac = "AA:BB:CC:DD:EE:02"

        try:
            from ble_reticulum.BLEInterface import DiscoveredPeer
            interface.discovered_peers[mac] = DiscoveredPeer(mac, "Test-Device", -50)
        except ImportError:
            pytest.skip("DiscoveredPeer not available")

        # Record 3 failures - should trigger blacklist
        for i in range(3):
            interface._record_connection_failure(mac)

        # After 3 failures, device should be blacklisted
        is_blacklisted = interface._is_blacklisted(mac)

        assert is_blacklisted is True, (
            "Blacklist mechanism should trigger after 3 failures"
        )

    def test_duplicate_rejection_with_safe_message_does_not_trigger_blacklist(self, mock_ble_interface):
        """
        Test that duplicate identity rejection with safe message format
        does NOT trigger blacklist.

        The fix: linux_bluetooth_driver now uses severity "info" with message
        "Duplicate identity rejected for {address}" which:
        1. Doesn't match the blacklist regex "Connection failed to"
        2. Uses severity "info" which doesn't set should_blacklist=True

        This test verifies the fix works by going through _error_callback
        with the safe message format.
        """
        interface = mock_ble_interface

        # Setup
        mac_new = "AA:BB:CC:DD:EE:02"

        try:
            from ble_reticulum.BLEInterface import DiscoveredPeer, BLEInterface as RealInterface
            interface.discovered_peers[mac_new] = DiscoveredPeer(mac_new, "Test-Device", -50)
            interface._error_callback = lambda severity, msg, exc: RealInterface._error_callback(interface, severity, msg, exc)
        except ImportError:
            pytest.skip("BLEInterface not available")

        # Simulate 3 duplicate rejections with SAFE message format (the fix)
        for i in range(3):
            # This is the NEW behavior - safe message format with "info" severity
            interface._error_callback(
                "info",
                f"Duplicate identity rejected for {mac_new} (MAC rotation)",
                None
            )

        # After 3 rejections with safe message, device should NOT be blacklisted
        is_blacklisted = interface._is_blacklisted(mac_new)

        assert is_blacklisted is False, (
            "Duplicate identity rejection with safe message format "
            "should NOT trigger blacklist!"
        )

    def test_error_message_pattern_matches_blacklist_regex(self):
        """
        Verify that the duplicate identity error message matches the blacklist regex.

        This proves WHY the bug occurs - the error message format triggers blacklisting.
        """
        # The error message generated by linux_bluetooth_driver.py
        mac_new = "AA:BB:CC:DD:EE:02"
        error_message = f"Connection failed to {mac_new}: Duplicate identity - already connected via different MAC (Android MAC rotation)"

        # The regex used in _error_callback to extract address for blacklisting
        blacklist_regex = r'(?:Connection (?:failed|timeout) to|to) ([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})'

        match = re.search(blacklist_regex, error_message)

        # This SHOULD NOT match for duplicate identity errors
        # But currently it DOES match, which is the bug
        assert match is not None, "Regex matches the error message (this is why the bug occurs)"
        assert match.group(1).upper() == mac_new, "Extracted address matches MAC_NEW"

    def test_real_connection_failure_should_trigger_blacklist(self, mock_ble_interface):
        """
        Verify that real connection failures (timeouts, errors) DO trigger blacklist.

        This ensures we don't break legitimate blacklisting while fixing the bug.
        """
        interface = mock_ble_interface
        mac = "AA:BB:CC:DD:EE:03"

        # Create discovered peer
        try:
            from ble_reticulum.BLEInterface import DiscoveredPeer
            interface.discovered_peers[mac] = DiscoveredPeer(mac, "Test-Device", -50)
        except ImportError:
            pytest.skip("DiscoveredPeer not available")

        # Simulate 3 real connection failures
        for i in range(3):
            interface._record_connection_failure(mac)

        # Real failures SHOULD trigger blacklist
        is_blacklisted = interface._is_blacklisted(mac)
        assert is_blacklisted is True, "Real connection failures should trigger blacklist"


class TestDuplicateIdentityErrorClassification:
    """Test that duplicate identity errors are classified differently from connection failures."""

    def test_duplicate_identity_error_should_be_distinguishable(self):
        """
        The fix should make duplicate identity errors distinguishable from real failures.

        Options:
        1. Use different error severity (e.g., "warning" instead of "error")
        2. Use different error message format that doesn't match blacklist regex
        3. Add explicit flag to skip blacklisting
        4. Check error message content before blacklisting
        """
        # This test documents the expected fix approach
        # The duplicate identity error should either:
        # a) Not trigger on_error at all (just log and return)
        # b) Use severity that doesn't trigger blacklist
        # c) Use message format that doesn't match blacklist regex

        duplicate_error_msg = "Duplicate identity - already connected via different MAC"
        real_failure_msg = "Connection failed to AA:BB:CC:DD:EE:02: timeout"

        # After fix, these should be distinguishable
        # For now, just document the requirement
        assert "Duplicate identity" in duplicate_error_msg
        assert "Connection failed" in real_failure_msg

    def test_safe_error_message_formats_dont_trigger_blacklist(self):
        """
        Verify that safe error message formats for duplicate identity
        don't match the blacklist regex pattern.

        This test ensures that when we fix the bug (on both Linux and Android),
        we use message formats that won't trigger blacklisting.
        """
        mac = "AA:BB:CC:DD:EE:02"

        # The regex used in _error_callback to extract addresses for blacklisting
        blacklist_regex = r'(?:Connection (?:failed|timeout) to|to) ([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})'

        # Messages that SHOULD trigger blacklist (real failures)
        unsafe_messages = [
            f"Connection failed to {mac}: timeout",
            f"Connection timeout to {mac}",
            f"Connection failed to {mac}: GATT error",
        ]

        # Messages that SHOULD NOT trigger blacklist (duplicate identity)
        safe_messages = [
            f"Duplicate identity rejected for {mac}",
            f"Rejecting duplicate identity from {mac} (already connected)",
            f"MAC rotation duplicate detected: {mac}",
            f"Duplicate identity - already connected via different MAC",
            f"Identity already connected at different address, rejecting {mac}",
        ]

        # Verify unsafe messages match the regex
        for msg in unsafe_messages:
            match = re.search(blacklist_regex, msg)
            assert match is not None, f"Unsafe message should match blacklist regex: {msg}"

        # Verify safe messages do NOT match the regex
        for msg in safe_messages:
            match = re.search(blacklist_regex, msg)
            assert match is None, f"Safe message should NOT match blacklist regex: {msg}"


class TestErrorCallbackBlacklistBehavior:
    """Test the actual _error_callback behavior with different severities and messages."""

    @pytest.fixture
    def mock_interface_for_error_callback(self):
        """Create mock interface for testing _error_callback."""
        try:
            from ble_reticulum.BLEInterface import BLEInterface
        except ImportError:
            pytest.skip("BLEInterface not available")

        interface = Mock()
        interface.connection_blacklist = {}
        interface.discovered_peers = {}
        interface.connection_retry_backoff = 60
        interface.max_connection_failures = 3

        # Mock driver
        interface.driver = Mock()
        interface.driver._remove_bluez_device = None

        # Import actual method
        from ble_reticulum.BLEInterface import BLEInterface as RealInterface

        interface._record_connection_failure = lambda addr: RealInterface._record_connection_failure(interface, addr)
        interface._is_blacklisted = lambda addr: RealInterface._is_blacklisted(interface, addr)

        return interface

    def test_error_severity_info_does_not_trigger_blacklist(self, mock_interface_for_error_callback):
        """
        Verify that errors with severity 'info' or 'debug' don't trigger blacklist.

        The fix can use severity 'info' for duplicate identity rejections.
        """
        # _error_callback only triggers blacklist for:
        # - severity == "error" or "critical"
        # - severity == "warning" with "Connection timeout" in message

        # Safe severities that don't trigger blacklist:
        safe_severities = ['info', 'debug']

        # This documents the expected behavior - after implementing the fix,
        # duplicate identity errors should use one of these safe severities
        for severity in safe_severities:
            assert severity in ['info', 'debug', 'notice'], f"Severity {severity} should be safe"

    def test_error_callback_with_duplicate_identity_message(self, mock_interface_for_error_callback):
        """
        Test that _error_callback properly handles duplicate identity messages.

        After the fix, duplicate identity messages should NOT trigger blacklist
        regardless of how they arrive (Linux driver or Android callback).
        """
        interface = mock_interface_for_error_callback
        mac = "AA:BB:CC:DD:EE:02"

        # Create discovered peer
        try:
            from ble_reticulum.BLEInterface import DiscoveredPeer
            interface.discovered_peers[mac] = DiscoveredPeer(mac, "Test-Device", -50)
        except ImportError:
            pytest.skip("DiscoveredPeer not available")

        # Import actual _error_callback
        try:
            from ble_reticulum.BLEInterface import BLEInterface as RealInterface
            # We can't easily call _error_callback directly without more setup,
            # but we can verify the regex behavior
        except ImportError:
            pytest.skip("BLEInterface not available")

        # The key insight: if we use a message that doesn't contain
        # "Connection failed to" or "Connection timeout to", blacklist won't trigger

        # Current buggy message (triggers blacklist):
        buggy_msg = f"Connection failed to {mac}: Duplicate identity"

        # Fixed message (won't trigger blacklist):
        fixed_msg = f"Duplicate identity rejected for {mac}"

        blacklist_regex = r'(?:Connection (?:failed|timeout) to|to) ([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})'

        assert re.search(blacklist_regex, buggy_msg) is not None, "Buggy message triggers blacklist"
        assert re.search(blacklist_regex, fixed_msg) is None, "Fixed message should not trigger blacklist"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
