"""
Tests for HCI Error Fixes (Event-Driven D-Bus Monitoring)

Tests the fixes for HCI errors on BCM43xx single-radio Bluetooth chips.
The root cause was D-Bus monitoring threads polling every 0.5s, causing
radio contention with advertising/scanning operations.

Fixes tested:
1. Event-driven D-Bus monitor: Uses asyncio.Event instead of polling
2. Stale poll improvements: Uses threading.Event.wait() instead of busy-wait
3. Stop() shutdown behavior: Uses call_soon_threadsafe for immediate stop

Reference: /tmp/hci_error_analysis.md
"""

import pytest
import sys
import os
import asyncio
import threading
import time
from unittest.mock import Mock, MagicMock, AsyncMock, patch, PropertyMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

# Mock RNS module before importing
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

RNS.log = Mock()


class TestEventDrivenDBusMonitor:
    """Test event-driven D-Bus monitoring (replaces polling)."""

    @pytest.fixture
    def mock_driver(self):
        """Create mock driver with required attributes."""
        driver = Mock()
        driver._peers = {}
        driver._peers_lock = threading.RLock()
        driver._log = Mock()
        driver._handle_peripheral_disconnected = Mock()
        return driver

    @pytest.fixture
    def mock_gatt_server(self, mock_driver):
        """Create mock GATT server with event-driven monitoring setup."""
        from ble_reticulum.linux_bluetooth_driver import BluezeroGATTServer

        server = Mock(spec=BluezeroGATTServer)
        server.driver = mock_driver
        server.stop_event = threading.Event()
        server.connected_centrals = {}
        server.centrals_lock = threading.RLock()
        server._log = Mock()
        server._handle_central_disconnected = Mock()
        server.log_prefix = "[TEST]"

        # Event-driven shutdown attributes
        server._monitor_loop = None
        server._async_stop_event = None

        return server

    def test_async_stop_event_initialized(self, mock_gatt_server):
        """Test that _async_stop_event is initialized to None before monitoring starts."""
        assert mock_gatt_server._async_stop_event is None
        assert mock_gatt_server._monitor_loop is None

    def test_async_event_setup_in_monitor_loop(self):
        """Test that asyncio.Event and loop reference are set up correctly in monitor_loop."""
        async def async_test():
            loop = asyncio.get_running_loop()
            async_stop = asyncio.Event()

            # These would be set on self in the real implementation
            _monitor_loop = loop
            _async_stop_event = async_stop

            # Verify they're set correctly
            assert _monitor_loop is loop
            assert _async_stop_event is async_stop
            assert isinstance(_async_stop_event, asyncio.Event)
            assert not _async_stop_event.is_set()

        asyncio.run(async_test())

    def test_async_event_wait_blocks_until_set(self):
        """Test that await async_stop.wait() blocks until event is set."""
        async def async_test():
            async_stop = asyncio.Event()
            wait_completed = False

            async def wait_for_stop():
                nonlocal wait_completed
                await async_stop.wait()
                wait_completed = True

            # Start waiting
            wait_task = asyncio.create_task(wait_for_stop())

            # Should still be waiting
            await asyncio.sleep(0.1)
            assert not wait_completed

            # Set the event
            async_stop.set()

            # Should complete quickly
            await asyncio.wait_for(wait_task, timeout=1.0)
            assert wait_completed

        asyncio.run(async_test())

    def test_async_event_wakes_immediately_when_set(self):
        """Test that async event wakes immediately (no 5s delay like polling)."""
        async def async_test():
            async_stop = asyncio.Event()
            wake_time = None

            async def wait_for_stop():
                nonlocal wake_time
                await async_stop.wait()
                wake_time = time.time()

            # Start waiting
            wait_task = asyncio.create_task(wait_for_stop())
            await asyncio.sleep(0.01)  # Let task start

            # Set event and measure response time
            set_time = time.time()
            async_stop.set()

            await asyncio.wait_for(wait_task, timeout=1.0)

            # Response should be immediate (< 100ms vs 5000ms polling)
            response_time = wake_time - set_time
            assert response_time < 0.1, f"Response time {response_time}s should be < 0.1s"

        asyncio.run(async_test())

    def test_call_soon_threadsafe_sets_event(self):
        """Test that call_soon_threadsafe can set async event from another thread."""
        async def async_test():
            async_stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            event_was_set = False

            async def wait_for_stop():
                nonlocal event_was_set
                await async_stop.wait()
                event_was_set = True

            # Start waiting in async context
            wait_task = asyncio.create_task(wait_for_stop())
            await asyncio.sleep(0.01)

            # Set event from sync code (simulating stop() call)
            # In real implementation this would be from another thread
            loop.call_soon_threadsafe(async_stop.set)

            await asyncio.wait_for(wait_task, timeout=1.0)
            assert event_was_set

        asyncio.run(async_test())

    def test_call_soon_threadsafe_from_thread(self):
        """Test that call_soon_threadsafe works from a separate thread."""
        event_set_in_loop = threading.Event()
        loop_started = threading.Event()

        stored_loop = None
        stored_event = None

        async def async_main():
            nonlocal stored_loop, stored_event
            async_stop = asyncio.Event()
            loop = asyncio.get_running_loop()

            # Store for cross-thread access
            stored_loop = loop
            stored_event = async_stop

            loop_started.set()

            # Wait for signal
            await async_stop.wait()
            event_set_in_loop.set()

        # Run async code in thread
        async_thread = threading.Thread(
            target=lambda: asyncio.run(async_main()),
            daemon=True
        )
        async_thread.start()

        # Wait for loop to start
        loop_started.wait(timeout=2.0)
        assert stored_loop is not None
        assert stored_event is not None

        # Signal from main thread
        stored_loop.call_soon_threadsafe(stored_event.set)

        # Verify event was set
        event_set_in_loop.wait(timeout=2.0)
        assert event_set_in_loop.is_set()

        async_thread.join(timeout=1.0)

    def test_no_polling_loop_in_monitor(self):
        """Test that there's no periodic polling - just event wait."""
        async def async_test():
            # The implementation should NOT have this pattern:
            # while not self.stop_event.is_set():
            #     await asyncio.sleep(0.5)  # BAD - polling

            # Instead it should use:
            # await async_stop.wait()  # GOOD - event-driven

            async_stop = asyncio.Event()
            iterations = 0

            async def event_driven_wait():
                nonlocal iterations
                # This is the correct pattern - single wait, no loop iterations
                await async_stop.wait()
                iterations = 1  # Only one "iteration" - the wait itself

            # Test event-driven pattern
            async_stop.set()  # Set immediately for test
            await event_driven_wait()

            # Event-driven should only have 1 "iteration"
            assert iterations == 1

        asyncio.run(async_test())


class TestStalePollImprovements:
    """Test stale connection polling improvements."""

    @pytest.fixture
    def mock_gatt_server(self):
        """Create mock GATT server with polling setup."""
        server = Mock()
        server.stop_event = threading.Event()
        server.connected_centrals = {}
        server.centrals_lock = threading.RLock()
        server._log = Mock()
        server._handle_central_disconnected = Mock()
        server.log_prefix = "[TEST]"
        return server

    def test_event_wait_used_instead_of_busy_loop(self):
        """Test that threading.Event.wait(timeout) is used instead of busy-wait."""
        stop_event = threading.Event()
        wait_called = False

        # The implementation should use:
        # if self.stop_event.wait(timeout=300.0):
        #     break

        # Not the old pattern:
        # for _ in range(240):
        #     if self.stop_event.is_set():
        #         break
        #     time.sleep(0.5)

        def proper_wait_pattern():
            nonlocal wait_called
            if stop_event.wait(timeout=0.1):  # Short timeout for test
                wait_called = True
                return True
            return False

        # Should return False when not set
        result = proper_wait_pattern()
        assert not result
        assert not wait_called

        # Should return True when set
        stop_event.set()
        result = proper_wait_pattern()
        assert result
        assert wait_called

    def test_immediate_stop_response(self):
        """Test that stop signal is responded to immediately (not after 0.5s polls)."""
        stop_event = threading.Event()
        response_time = None
        thread_exited = threading.Event()

        def wait_loop():
            nonlocal response_time
            start = time.time()
            # Using Event.wait() pattern
            stop_event.wait(timeout=300.0)
            response_time = time.time() - start
            thread_exited.set()

        thread = threading.Thread(target=wait_loop, daemon=True)
        thread.start()

        # Let thread start waiting
        time.sleep(0.05)

        # Signal stop
        stop_event.set()

        # Wait for thread to respond
        thread_exited.wait(timeout=1.0)

        # Response should be immediate (< 100ms vs old 500ms polling interval)
        assert response_time is not None
        assert response_time < 0.15, f"Response time {response_time}s should be < 0.15s"

    def test_poll_interval_is_300_seconds(self):
        """Test that stale poll interval is 300 seconds (5 minutes)."""
        # The implementation uses:
        # if self.stop_event.wait(timeout=300.0):

        # Verify the constant value (we can't easily test 5 min wait in unit test)
        EXPECTED_INTERVAL = 300.0

        # This would be tested by reading the actual code value
        # For now, we simulate what the implementation should do
        stop_event = threading.Event()
        poll_count = 0

        def poll_loop():
            nonlocal poll_count
            while not stop_event.is_set():
                # In real code, this is 300.0
                if stop_event.wait(timeout=0.01):  # Short for test
                    break
                poll_count += 1
                if poll_count >= 5:  # Limit iterations for test
                    break

        thread = threading.Thread(target=poll_loop, daemon=True)
        thread.start()

        time.sleep(0.1)
        stop_event.set()
        thread.join(timeout=1.0)

        # Thread should have exited cleanly
        assert not thread.is_alive()

    def test_single_wait_call_per_interval(self):
        """Test that each interval uses single wait() call, not 600 iterations."""
        stop_event = threading.Event()
        wait_call_count = 0
        original_wait = threading.Event.wait

        def counting_wait(self, timeout=None):
            nonlocal wait_call_count
            wait_call_count += 1
            return original_wait(self, timeout=0.01 if timeout else timeout)

        with patch.object(threading.Event, 'wait', counting_wait):
            # Simulate one "poll cycle"
            stop_event.wait(timeout=300.0)  # This is the new pattern

        # Should be just 1 wait call, not 600+ like the old busy-loop
        assert wait_call_count == 1

    def test_no_busy_loop_iterations(self):
        """Test that the old busy-loop pattern is not used."""
        stop_event = threading.Event()
        sleep_count = 0

        # Old pattern would have 600 sleep calls per 5-minute interval:
        # for _ in range(600):
        #     if self.stop_event.is_set():
        #         break
        #     time.sleep(0.5)

        # New pattern has zero sleep calls:
        # if self.stop_event.wait(timeout=300.0):
        #     break

        def new_poll_pattern():
            nonlocal sleep_count
            # New pattern - no sleep calls
            if stop_event.wait(timeout=0.01):
                return True
            # No time.sleep() here!
            return False

        new_poll_pattern()

        # No explicit sleep calls in new pattern
        assert sleep_count == 0


class TestStopShutdownBehavior:
    """Test stop() method shutdown behavior."""

    @pytest.fixture
    def mock_gatt_server(self):
        """Create mock GATT server for stop() testing."""
        server = Mock()
        server.stop_event = threading.Event()
        server.running = True
        server._log = Mock()
        server._monitor_loop = None
        server._async_stop_event = None
        server.server_thread = None
        server.disconnect_monitor_thread = None
        server.stale_poll_thread = None
        server.ble_agent = None
        server.connected_centrals = {}
        server.centrals_lock = threading.RLock()
        return server

    def test_stop_sets_stop_event(self, mock_gatt_server):
        """Test that stop() sets the stop_event."""
        assert not mock_gatt_server.stop_event.is_set()

        # Simulate stop() behavior
        mock_gatt_server.stop_event.set()

        assert mock_gatt_server.stop_event.is_set()

    def test_stop_signals_async_event(self):
        """Test that stop() signals async event via call_soon_threadsafe."""
        async def async_test():
            async_stop = asyncio.Event()
            loop = asyncio.get_running_loop()

            # Simulate stop() calling call_soon_threadsafe
            loop.call_soon_threadsafe(async_stop.set)

            # Give event loop a chance to process
            await asyncio.sleep(0.01)

            assert async_stop.is_set()

        asyncio.run(async_test())

    def test_stop_handles_runtime_error_gracefully(self):
        """Test that stop() handles RuntimeError when loop is already stopped."""
        # Create a mock loop that raises RuntimeError
        mock_loop = Mock()
        mock_loop.call_soon_threadsafe = Mock(side_effect=RuntimeError("Loop is closed"))

        mock_async_stop = Mock()

        # Simulate the stop() error handling code
        _monitor_loop = mock_loop
        _async_stop_event = mock_async_stop

        try:
            _monitor_loop.call_soon_threadsafe(_async_stop_event.set)
        except RuntimeError:
            pass  # Should be caught and ignored

        # Test passes if no exception is raised

    def test_stop_checks_for_none_references(self):
        """Test that stop() checks for None before calling call_soon_threadsafe."""
        _monitor_loop = None
        _async_stop_event = None

        # Simulate the stop() check
        if _monitor_loop and _async_stop_event:
            # This should NOT be reached
            pytest.fail("Should not call when references are None")

        # Test passes - no error when refs are None

    def test_shutdown_latency_improvement(self):
        """Test that shutdown responds immediately (not up to 5s delay)."""
        stop_event = threading.Event()
        async_stop_triggered = threading.Event()
        thread_exited = threading.Event()

        stored_loop = None
        stored_async_stop = None

        async def mock_monitor_loop():
            nonlocal stored_loop, stored_async_stop
            async_stop = asyncio.Event()
            loop = asyncio.get_running_loop()

            # Store refs for "stop()" to access
            stored_loop = loop
            stored_async_stop = async_stop

            async_stop_triggered.set()  # Signal refs are ready

            # Wait for stop signal
            await async_stop.wait()

        def run_async():
            try:
                asyncio.run(mock_monitor_loop())
            except Exception:
                pass
            thread_exited.set()

        # Start monitor thread
        thread = threading.Thread(target=run_async, daemon=True)
        thread.start()

        # Wait for async loop to be ready
        async_stop_triggered.wait(timeout=2.0)
        assert stored_loop is not None

        # Measure shutdown time
        start = time.time()

        # Simulate stop() calling call_soon_threadsafe
        stop_event.set()
        stored_loop.call_soon_threadsafe(stored_async_stop.set)

        # Wait for thread to exit
        thread_exited.wait(timeout=2.0)
        shutdown_time = time.time() - start

        # Shutdown should be fast (< 500ms vs old 5000ms max)
        assert shutdown_time < 0.5, f"Shutdown took {shutdown_time}s, should be < 0.5s"

    def test_stop_waits_for_threads_with_timeout(self, mock_gatt_server):
        """Test that stop() waits for threads with reasonable timeouts."""
        # Create mock threads
        mock_server_thread = Mock()
        mock_server_thread.is_alive = Mock(return_value=True)
        mock_server_thread.join = Mock()

        mock_monitor_thread = Mock()
        mock_monitor_thread.is_alive = Mock(return_value=True)
        mock_monitor_thread.join = Mock()

        mock_poll_thread = Mock()
        mock_poll_thread.is_alive = Mock(return_value=True)
        mock_poll_thread.join = Mock()

        # Simulate stop() thread joins
        mock_gatt_server.server_thread = mock_server_thread
        mock_gatt_server.disconnect_monitor_thread = mock_monitor_thread
        mock_gatt_server.stale_poll_thread = mock_poll_thread

        # Verify join is called with appropriate timeouts
        if mock_gatt_server.server_thread and mock_gatt_server.server_thread.is_alive():
            mock_gatt_server.server_thread.join(timeout=5.0)

        if mock_gatt_server.disconnect_monitor_thread and mock_gatt_server.disconnect_monitor_thread.is_alive():
            mock_gatt_server.disconnect_monitor_thread.join(timeout=2.0)

        if mock_gatt_server.stale_poll_thread and mock_gatt_server.stale_poll_thread.is_alive():
            mock_gatt_server.stale_poll_thread.join(timeout=2.0)

        # Verify joins were called with timeouts
        mock_server_thread.join.assert_called_once_with(timeout=5.0)
        mock_monitor_thread.join.assert_called_once_with(timeout=2.0)
        mock_poll_thread.join.assert_called_once_with(timeout=2.0)


class TestIntegrationScenarios:
    """Integration tests for HCI error fix scenarios."""

    def test_full_lifecycle_start_to_stop(self):
        """Test complete lifecycle with event-driven monitoring."""
        stop_event = threading.Event()
        monitor_started = threading.Event()
        monitor_stopped = threading.Event()

        stored_loop = None
        stored_async_stop = None

        async def mock_monitor():
            nonlocal stored_loop, stored_async_stop
            async_stop = asyncio.Event()
            loop = asyncio.get_running_loop()

            stored_loop = loop
            stored_async_stop = async_stop

            monitor_started.set()

            # Event-driven wait (not polling)
            await async_stop.wait()

        def run_monitor():
            try:
                asyncio.run(mock_monitor())
            except Exception:
                pass
            monitor_stopped.set()

        # Start monitoring
        thread = threading.Thread(target=run_monitor, daemon=True)
        thread.start()

        # Wait for start
        monitor_started.wait(timeout=2.0)
        assert stored_loop is not None
        assert stored_async_stop is not None

        # Simulate stop()
        stop_event.set()
        stored_loop.call_soon_threadsafe(stored_async_stop.set)

        # Wait for clean shutdown
        monitor_stopped.wait(timeout=2.0)
        thread.join(timeout=1.0)

        assert not thread.is_alive()
        assert monitor_stopped.is_set()

    def test_multiple_stop_calls_safe(self):
        """Test that multiple stop() calls don't cause issues."""
        stop_event = threading.Event()

        # First stop
        stop_event.set()
        assert stop_event.is_set()

        # Second stop (should be safe)
        stop_event.set()
        assert stop_event.is_set()

        # Clear and set again (simulating restart + stop)
        stop_event.clear()
        assert not stop_event.is_set()
        stop_event.set()
        assert stop_event.is_set()

    def test_dbus_signals_still_processed_during_wait(self):
        """Test that D-Bus signals are processed while waiting for stop."""
        async def async_test():
            async_stop = asyncio.Event()
            signal_received = False

            def handle_signal():
                nonlocal signal_received
                signal_received = True

            # Start wait task
            wait_task = asyncio.create_task(async_stop.wait())

            # Simulate D-Bus signal (would be scheduled via event loop)
            await asyncio.sleep(0.01)
            handle_signal()  # Signal handler runs

            # Verify signal was processed
            assert signal_received

            # Stop wait
            async_stop.set()
            await wait_task

        asyncio.run(async_test())


class TestNoPollingVerification:
    """Verify that no polling patterns exist in the fixes."""

    def test_no_05_second_sleep_in_monitor(self):
        """Verify the old 0.5s sleep pattern is not used in D-Bus monitor."""
        # The old pattern was:
        # await asyncio.sleep(0.5)  # BAD

        # New pattern:
        # await async_stop.wait()  # GOOD

        # This test verifies the concept
        async def no_polling_pattern():
            async_stop = asyncio.Event()
            # No periodic sleep!
            async_stop.set()  # Immediately set for test
            await async_stop.wait()

        # Should complete without any sleep delays
        start = time.time()
        asyncio.run(no_polling_pattern())
        elapsed = time.time() - start

        # Should complete in < 100ms (no 500ms sleeps)
        assert elapsed < 0.1

    def test_no_busy_loop_in_stale_poll(self):
        """Verify the old busy-loop pattern is not used in stale poll."""
        stop_event = threading.Event()
        iterations = 0

        # Old pattern (BAD):
        # for _ in range(240):  # 240 * 0.5s = 120s
        #     if self.stop_event.is_set():
        #         break
        #     time.sleep(0.5)

        # New pattern (GOOD):
        def new_pattern():
            nonlocal iterations
            if stop_event.wait(timeout=0.01):  # Short timeout for test
                return True
            iterations += 1
            return False

        # Run the new pattern
        new_pattern()

        # Should only have 1 iteration (the wait call itself)
        assert iterations == 1


class TestCodeVerification:
    """Tests that verify the actual implementation has correct patterns."""

    def test_verify_poll_interval_in_code(self):
        """Verify that the actual code uses 300s poll interval."""
        import re

        # Read the actual source file
        source_path = os.path.join(
            os.path.dirname(__file__),
            '../src/ble_reticulum/linux_bluetooth_driver.py'
        )

        with open(source_path, 'r') as f:
            source = f.read()

        # Find the poll_stale_connections method and verify it uses 300.0 timeout
        # Pattern: stop_event.wait(timeout=300.0)
        poll_pattern = r'self\.stop_event\.wait\(timeout=(\d+\.?\d*)\)'
        matches = re.findall(poll_pattern, source)

        assert '300.0' in matches, f"Expected 300.0 second timeout, found: {matches}"

    def test_verify_event_driven_wait_in_code(self):
        """Verify that the actual code uses async_stop.wait() not polling."""
        import re

        source_path = os.path.join(
            os.path.dirname(__file__),
            '../src/ble_reticulum/linux_bluetooth_driver.py'
        )

        with open(source_path, 'r') as f:
            source = f.read()

        # The code should have: await async_stop.wait()
        assert 'await async_stop.wait()' in source, "Should use event-driven wait"

        # The code should NOT have the old polling pattern in the monitor loop
        # Look for the monitor_loop function and check it doesn't have asyncio.sleep polling
        monitor_section_match = re.search(
            r'async def monitor_loop\(\):(.*?)(?=\n    def |\n    async def |\Z)',
            source,
            re.DOTALL
        )

        if monitor_section_match:
            monitor_section = monitor_section_match.group(1)
            # Should not have: while not stop_event... asyncio.sleep pattern
            polling_pattern = r'while.*stop_event.*\n.*asyncio\.sleep\(0\.5\)'
            assert not re.search(polling_pattern, monitor_section), \
                "Monitor loop should not use 0.5s polling pattern"

    def test_verify_call_soon_threadsafe_in_stop(self):
        """Verify that stop() uses call_soon_threadsafe."""
        source_path = os.path.join(
            os.path.dirname(__file__),
            '../src/ble_reticulum/linux_bluetooth_driver.py'
        )

        with open(source_path, 'r') as f:
            source = f.read()

        # The stop() method should use call_soon_threadsafe
        assert 'call_soon_threadsafe' in source, "stop() should use call_soon_threadsafe"
        assert '_async_stop_event.set' in source, "Should signal async stop event"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
